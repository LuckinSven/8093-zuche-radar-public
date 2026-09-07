import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.domain import ScanQuery
from app.main import create_app
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger
from tests.test_rebuild_discovery import Gateway


@pytest.mark.asyncio
async def test_data_rows_flatten_latest_scan_with_distance_audit(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        result = await ScanService(Gateway(), ScanRepository(session)).run(
            ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291,
                      longitude=113.2644, pickup_time="2026-09-05T09:00:00",
                      return_time="2026-09-06T09:00:00"), ScanTrigger.MANUAL)

    app = create_app(session_factory=factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/api/data/rows", params={"page_size": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["scan_id"] == str(result.scan_id)
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 1
    row = body["items"][0]
    assert row["scan_location"] == "广州中心"
    assert row["model_id"] in {4665, 4666}
    assert row["department_id"] == 1001
    assert row["shenzhou_distance_km"] == pytest.approx(2.04)
    assert row["coordinate_distance_km"] == pytest.approx(1.812, abs=0.001)
    assert row["native_groups"]
    assert row["price_is_final"] is False


@pytest.mark.asyncio
async def test_data_api_lists_scans_and_exports_excel_compatible_csv(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        result = await ScanService(Gateway(), ScanRepository(session)).run(
            ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291,
                      longitude=113.2644, pickup_time="2026-09-05T09:00:00",
                      return_time="2026-09-06T09:00:00"), ScanTrigger.MANUAL)

    app = create_app(session_factory=factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        scans = await client.get("/api/data/scans")
        exported = await client.get("/api/data/export.csv", params={"scan_id": str(result.scan_id)})

    assert scans.status_code == 200
    assert scans.json()["items"][0]["id"] == str(result.scan_id)
    assert scans.json()["items"][0]["offer_count"] == 2
    assert scans.json()["items"][0]["pickup_time"].startswith("2026-09-05T09:00:00")
    assert scans.json()["items"][0]["return_time"].startswith("2026-09-06T09:00:00")
    assert scans.json()["items"][0]["pickup_time_label"] == "2026-09-05 09:00"
    assert scans.json()["items"][0]["return_time_label"] == "2026-09-06 09:00"
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment;" in exported.headers["content-disposition"]
    assert exported.content.startswith(b"\xef\xbb\xbf")
    text = exported.content.decode("utf-8-sig")
    assert "扫描时间,扫描地点,车型ID,车型名称" in text
    assert "广州测试服务点" in text
    assert "非最终结算价" in text


@pytest.mark.asyncio
async def test_data_rows_and_export_can_filter_by_department(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    gateway = Gateway()
    payload = await gateway.choose_car(None)
    second = dict(payload["content"]["deptHangModels"][0])
    second["deptId"] = 2002
    second["deptName"] = "鱼珠地铁站服务点"
    second["models"] = [dict(second["models"][0], modelId=9002, modelName="鱼珠测试车")]
    payload["content"]["deptHangModels"].append(second)

    class TwoDepartments:
        async def choose_car(self, _query):
            return payload

    with factory() as session:
        result = await ScanService(TwoDepartments(), ScanRepository(session)).run(
            ScanQuery(city_id="14", location_name="鱼珠地铁站服务点", latitude=23.103,
                      longitude=113.432, pickup_time="2026-09-05T09:00:00",
                      return_time="2026-09-06T09:00:00"), ScanTrigger.MANUAL)

    app = create_app(session_factory=factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        rows = await client.get("/api/data/rows", params={
            "scan_id": str(result.scan_id), "department_id": 2002})
        shaped = await client.get("/api/data/rows", params={
            "scan_id": str(result.scan_id), "body_style": "SUV", "max_price": 98})
        exported = await client.get("/api/data/export.csv", params={
            "scan_id": str(result.scan_id), "department_id": 2002})

    assert rows.status_code == 200
    assert rows.json()["total"] == 1
    assert rows.json()["items"][0]["department_id"] == 2002
    assert {item["id"] for item in rows.json()["departments"]} == {1001, 2002}
    assert shaped.status_code == 200
    assert shaped.json()["total"] == 1
    assert shaped.json()["items"][0]["model_name"] == "大众途铠"
    text = exported.content.decode("utf-8-sig")
    assert "鱼珠地铁站服务点" in text
    assert "广州测试服务点" not in text
