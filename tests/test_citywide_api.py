from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.citywide.service import CitywideScanService
from app.main import create_app
from app.models import (
    City,
    CitywideModelSummary,
    CitywideScanStatus,
    Department,
    VehicleModel,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _future_period(days=2, duration_days=1):
    pickup = (datetime.now(SHANGHAI) + timedelta(days=days)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    return pickup, pickup + timedelta(days=duration_days)


def _app_with_departments(engine, count=91, service=None):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = City(
            zuche_city_id="14",
            name="广州",
            latitude=23.1291,
            longitude=113.2644,
        )
        session.add(city)
        session.flush()
        session.add_all([
            Department(
                city_id=city.id,
                zuche_dept_id=80_000 + index,
                name=f"广州网点{index}",
                latitude=23.10 + index / 100_000,
                longitude=113.43 + index / 100_000,
            )
            for index in range(count)
        ])
        session.commit()
        city_id = city.id
    app = create_app(session_factory=factory)
    app.state.citywide_scan_service = service or CitywideScanService(
        factory,
        lambda: (_ for _ in ()).throw(
            AssertionError("创建和控制任务不应访问神州接口")),
    )
    return app, factory, city_id


@pytest.mark.asyncio
async def test_create_citywide_scan_returns_fixed_department_snapshot_progress(engine):
    """若创建接口不固定网点快照，任务进度会随目录变化而漂移。"""
    app, _, city_id = _app_with_departments(engine)
    pickup, return_time = _future_period()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/citywide-scans", json={
            "city_id": city_id,
            "pickup_time": pickup.isoformat(),
            "return_time": return_time.isoformat(),
        })

    assert response.status_code == 201
    assert response.json()["planned_point_count"] == 91
    assert response.json()["status"] == "PENDING"
    assert response.json()["pickup_time"] == pickup.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"city_id": True, "extra": "x"},
    {"city_id": True},
    {"city_id": 1, "pickup_time": "2020-01-01T09:00:00+08:00",
     "return_time": "2020-01-02T09:00:00+08:00"},
])
async def test_create_citywide_scan_rejects_unsafe_or_stale_inputs(engine, payload):
    """若输入契约放宽，布尔 ID、额外字段或过去租期会写入任务。"""
    app, _, city_id = _app_with_departments(engine)
    pickup, return_time = _future_period()
    body = {
        "city_id": city_id,
        "pickup_time": pickup.isoformat(),
        "return_time": return_time.isoformat(),
    }
    body.update(payload)

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/citywide-scans", json=body)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


@pytest.mark.asyncio
async def test_create_rejects_period_longer_than_thirty_days(engine):
    """若不限制租期，匿名接口会接收无意义的超长窗口。"""
    app, _, city_id = _app_with_departments(engine)
    pickup, _ = _future_period()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/citywide-scans", json={
            "city_id": city_id,
            "pickup_time": pickup.isoformat(),
            "return_time": (pickup + timedelta(days=31)).isoformat(),
        })

    assert response.status_code == 422
    assert "30" in response.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_active_run_and_illegal_controls_return_conflict(engine):
    """若领域冲突泄漏为数据库错误，前端无法解释任务状态。"""
    app, _, city_id = _app_with_departments(engine)
    pickup, return_time = _future_period()
    body = {
        "city_id": city_id,
        "pickup_time": pickup.isoformat(),
        "return_time": return_time.isoformat(),
    }

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/citywide-scans", json=body)
        duplicate = await client.post("/api/citywide-scans", json=body)
        run_id = created.json()["id"]
        stopped = await client.post(f"/api/citywide-scans/{run_id}/stop")
        resumed = await client.post(f"/api/citywide-scans/{run_id}/resume")

    assert duplicate.status_code == 409
    assert stopped.status_code == 409
    assert resumed.status_code == 409
    assert "活动" in duplicate.json()["detail"]


@pytest.mark.asyncio
async def test_citywide_scan_history_and_missing_run_are_stable(engine):
    """若历史没有分页或 404，页面轮询会得到不稳定结构。"""
    app, _, city_id = _app_with_departments(engine)
    pickup, return_time = _future_period()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/citywide-scans", json={
            "city_id": city_id,
            "pickup_time": pickup.isoformat(),
            "return_time": return_time.isoformat(),
        })
        history = await client.get("/api/citywide-scans?page=1&page_size=20")
        item = await client.get(f"/api/citywide-scans/{created.json()['id']}")
        missing = await client.get(f"/api/citywide-scans/{uuid4()}")

    assert history.status_code == 200
    assert history.json()["pagination"]["total"] == 1
    assert history.json()["items"][0]["id"] == created.json()["id"]
    assert item.json()["id"] == created.json()["id"]
    assert missing.status_code == 404
    assert missing.json()["detail"] == "全城扫描任务不存在"


@pytest.mark.asyncio
async def test_internal_service_error_is_not_exposed(engine):
    """若直接返回异常正文，上游地址或数据库细节可能泄漏。"""
    class BrokenService:
        def create_run(self, *_):
            raise RuntimeError("secret database url")

    app, _, city_id = _app_with_departments(engine, service=BrokenService())
    pickup, return_time = _future_period()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/citywide-scans", json={
            "city_id": city_id,
            "pickup_time": pickup.isoformat(),
            "return_time": return_time.isoformat(),
        })

    assert response.status_code == 500
    assert response.json()["detail"] == "全城扫描任务创建失败，请稍后重试"
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_citywide_model_and_library_read_apis_return_typed_results(engine):
    """若聚合读取接口缺失，AI 和页面都只能读取旧报价明细。"""
    app, factory, city_id = _app_with_departments(engine, count=1)
    pickup, return_time = _future_period()
    run = app.state.citywide_scan_service.create_run(city_id, pickup, return_time)
    with factory() as session:
        stored = session.get(type(run), run.id)
        stored.status = CitywideScanStatus.COMPLETED
        stored.completed_point_count = stored.planned_point_count
        model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
        session.add(model)
        session.flush()
        session.add(CitywideModelSummary(
            run_id=stored.id,
            vehicle_model_id=model.id,
            available_department_count=1,
            average_price=115,
            minimum_price=115,
            maximum_price=115,
        ))
        session.commit()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        models = await client.get("/api/citywide-models", params={"run_id": str(run.id)})
        library = await client.get("/api/model-library", params={"run_id": str(run.id)})
        detail = await client.get(
            "/api/model-library/4952", params={"run_id": str(run.id)})
        offers = await client.get(
            "/api/citywide-models/4952/offers", params={"run_id": str(run.id)})

    assert models.status_code == 200
    assert models.json()["items"][0]["availability"] == "AVAILABLE"
    assert library.json()["items"][0]["model_name"] == "比亚迪海狮05"
    assert detail.json()["model_id"] == 4952
    assert offers.json() == {"items": []}
