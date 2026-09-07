import httpx
import pytest
from datetime import datetime, timedelta
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from tests.test_rebuild_discovery import Gateway
from app.domain import ScanQuery
from app.main import create_app
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger
from app.models import ScanRun, ScanStatus, VehicleModel


@pytest.mark.asyncio
async def test_probe_created_through_api_is_disabled_by_default(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        city = await client.post("/api/cities", json={"zuche_city_id": "14", "name": "广州",
                                                       "latitude": 23.1291, "longitude": 113.2644})
        probe = await client.post("/api/probes", json={"city_id": city.json()["id"], "name": "广州中心",
                                                        "latitude": 23.1291, "longitude": 113.2644})
    assert city.status_code == 201
    assert probe.status_code == 201
    assert probe.json()["enabled"] is False


@pytest.mark.asyncio
async def test_scan_period_validation_returns_422_without_calling_gateway(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/scans", json={"city_id": "14", "location_name": "广州中心",
            "latitude": 23.1291, "longitude": 113.2644, "pickup_time": "2026-09-05T09:00:00",
            "return_time": "2026-09-05T09:00:00"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_scan_validation_errors_are_chinese(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/scans", json={"city_id": "14", "location_name": "广州中心",
            "latitude": 91, "longitude": 113.2644, "pickup_time": "2026-09-05T09:00:00",
            "return_time": "2026-09-06T09:00:00"})

    assert response.status_code == 422
    assert response.json()["detail"] == "纬度必须在 -90 到 90 之间"
    assert "Input should" not in response.text


@pytest.mark.asyncio
async def test_personal_state_api_updates_known_model(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        model = ScanRepository(session).upsert_vehicle_model(4666, "大众途铠")
        session.commit()
        model_id = model.zuche_model_id
    app = create_app(session_factory=factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"/api/models/{model_id}/personal-state", json={"state": "WANT_TO_RENT", "note": "下次试试"})
    assert response.status_code == 200
    assert response.json()["state"] == "WANT_TO_RENT"


@pytest.mark.asyncio
async def test_manual_energy_api_supports_future_correction_from_model_detail(engine):
    """如果车型详情不能再次修改人工结论，该测试应失败。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        ScanRepository(session).upsert_vehicle_model(4666, "大众途铠")
        session.commit()
    app = create_app(session_factory=factory)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.put("/api/models/4666/energy", json={
            "energy_type": "燃油",
            "energy_subtype": "汽油",
            "note": "行驶证核对",
        })

    assert response.status_code == 200
    assert response.json() == {
        "model_id": 4666,
        "energy_type": "燃油",
        "energy_subtype": "汽油",
        "energy_source": "MANUAL",
        "energy_confidence": "HIGH",
    }
    with factory() as session:
        model = session.query(VehicleModel).filter_by(zuche_model_id=4666).one()
        assert (model.energy_type, model.energy_subtype) == ("燃油", "汽油")


def test_discovery_filters_are_query_parameters(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    operation = app.openapi()["paths"]["/api/discovery"]["get"]
    assert "requestBody" not in operation
    assert "scan_id" in {item["name"] for item in operation["parameters"]}


@pytest.mark.asyncio
async def test_model_detail_api_returns_latest_source_offers_and_history(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        query = ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
                          pickup_time="2026-09-05T09:00:00", return_time="2026-09-06T09:00:00")
        result = await ScanService(Gateway(), ScanRepository(session)).run(query, ScanTrigger.MANUAL)
    app = create_app(session_factory=factory)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.put("/api/models/4666/personal-state", json={"state": "WANT_TO_RENT", "note": "周末试驾"})
        response = await client.get("/api/models/4666")
        discovery = await client.get(f"/api/discovery?scan_id={result.scan_id}")
        history = await client.get("/api/history")

    assert response.status_code == 200
    body = response.json()
    assert body["model_id"] == 4666
    assert body["model_name"] == "大众途铠"
    assert body["source"]["offers"][0]["department_name"] == "广州测试服务点"
    assert body["source"]["price_is_final"] is False
    assert body["personal"] == {"state": "WANT_TO_RENT", "note": "周末试驾", "rented_on": None}
    assert body["history"][0]["scan_id"]
    assert {item["name"] for item in discovery.json()["groups"]} == {"经济型", "SUV"}
    latest = history.json()["items"][0]
    assert latest["zuche_city_id"] == "14"
    assert latest["department_count"] == 1
    assert latest["offer_count"] == 2
    assert latest["model_count"] == 2
    assert latest["event_count"] == 2
    assert [item["type"] for item in latest["events"]] == ["FIRST_SEEN", "FIRST_SEEN"]


@pytest.mark.asyncio
async def test_probe_can_be_enabled_after_creation_and_reconciles_scheduler(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory)

    class SchedulerSpy:
        reconciled = 0

        def reconcile(self):
            self.reconciled += 1

    app.state.probe_scheduler = SchedulerSpy()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        city = await client.post("/api/cities", json={"zuche_city_id": "15", "name": "佛山",
            "latitude": 23.1291, "longitude": 113.2644})
        probe = await client.post("/api/probes", json={"city_id": city.json()["id"], "name": "广州中心",
            "latitude": 23.1291, "longitude": 113.2644})
        updated = await client.patch(f"/api/probes/{probe.json()['id']}", json={
            "enabled": True, "schedule": "interval:30"})

    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert updated.json()["schedule"] == "interval:30"
    assert app.state.probe_scheduler.reconciled == 1


@pytest.mark.asyncio
async def test_city_and_probe_support_update_and_probe_delete(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        city = await client.post("/api/cities", json={"zuche_city_id": "16", "name": "深圳",
            "latitude": 23.1291, "longitude": 113.2644})
        city_id = city.json()["id"]
        changed_city = await client.patch(f"/api/cities/{city_id}", json={"name": "广州市"})
        probe = await client.post("/api/probes", json={"city_id": city_id, "name": "广州中心",
            "latitude": 23.1291, "longitude": 113.2644})
        deleted = await client.delete(f"/api/probes/{probe.json()['id']}")
        probes = await client.get("/api/probes")

    assert changed_city.status_code == 200
    assert changed_city.json()["name"] == "广州市"
    assert deleted.status_code == 204
    assert probe.json()["id"] not in {item["id"] for item in probes.json()["items"]}


@pytest.mark.asyncio
async def test_duplicate_city_returns_readable_conflict(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    payload = {"zuche_city_id": "14", "name": "广州", "latitude": 23.1291, "longitude": 113.2644}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/cities", json=payload)
        duplicate = await client.post("/api/cities", json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "神州城市 ID 已存在"


@pytest.mark.asyncio
async def test_enabled_probe_requires_valid_schedule(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        city = await client.post("/api/cities", json={"zuche_city_id": "14", "name": "广州",
            "latitude": 23.1291, "longitude": 113.2644})
        base = {"city_id": city.json()["id"], "name": "广州中心", "latitude": 23.1291,
                "longitude": 113.2644, "enabled": True}
        missing = await client.post("/api/probes", json=base)
        invalid = await client.post("/api/probes", json=base | {"schedule": "interval:0"})

    assert missing.status_code == 422
    assert missing.json()["detail"] == "开启自动扫描时必须设置计划"
    assert invalid.status_code == 422
    assert "扫描间隔必须为正数" in invalid.json()["detail"]


@pytest.mark.asyncio
async def test_city_without_probes_can_be_deleted(engine):
    app = create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        city = await client.post("/api/cities", json={"zuche_city_id": "14", "name": "广州",
            "latitude": 23.1291, "longitude": 113.2644})
        deleted = await client.delete(f"/api/cities/{city.json()['id']}")
        cities = await client.get("/api/cities")

    assert deleted.status_code == 204
    assert cities.json()["items"] == []


@pytest.mark.asyncio
async def test_history_uses_batched_queries_when_runs_accumulate(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        for index in range(25):
            start = datetime(2026, 9, 1) + timedelta(minutes=index)
            session.add(ScanRun(trigger="MANUAL", status=ScanStatus.SUCCESS, zuche_city_id="14",
                location_name=f"地点 {index}", latitude=23.1291, longitude=113.2644,
                pickup_time=start, return_time=start + timedelta(days=1), completed_at=start))
        session.commit()

    statements = []
    def count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        app = create_app(session_factory=factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/history")
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 25
    assert len(statements) == 3
