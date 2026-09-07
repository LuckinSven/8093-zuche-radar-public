from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.main import create_app
from app.model_search.service import ModelSearchService
from app.models import (
    City,
    Department,
    ModelSearchPhase,
    ModelSearchRun,
    ModelSearchRunStatus,
    VehicleModel,
)


NOW = datetime(2026, 9, 2, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def _app(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = City(zuche_city_id="14", name="广州")
        session.add_all([
            city,
            VehicleModel(zuche_model_id=4952, name="比亚迪海狮05"),
            VehicleModel(zuche_model_id=4953, name="比亚迪海狮05"),
        ])
        session.flush()
        session.add(Department(
            city_id=city.id,
            zuche_dept_id=79340,
            name="鱼珠地铁站服务点",
            latitude=23.101610,
            longitude=113.432649,
        ))
        session.commit()
    app = create_app(session_factory=factory)
    app.state.model_search_service = ModelSearchService(
        factory, lambda: (_ for _ in ()).throw(
            AssertionError("创建和控制任务不应请求神州")))
    app.state.model_search_now = lambda: NOW
    return app


@pytest.mark.asyncio
async def test_create_list_read_stop_and_resume_model_search_run(engine):
    """任务接口必须让页面和其他AI完整控制手动扫描。"""
    app = _app(engine)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/model-search-runs", json={
            "model_names": ["比亚迪海狮05"],
        })
        run_id = created.json()["id"]
        listed = await client.get("/api/model-search-runs")
        detail = await client.get(f"/api/model-search-runs/{run_id}")
        stopped = await client.post(f"/api/model-search-runs/{run_id}/stop")
        resumed = await client.post(f"/api/model-search-runs/{run_id}/resume")

    assert created.status_code == 201
    assert created.json()["planned_sample_count"] == 8
    assert listed.json()["items"][0]["id"] == run_id
    assert detail.json()["requested_names"] == ["比亚迪海狮05"]
    assert stopped.json()["status"] == "STOPPED"
    assert resumed.json()["status"] == "RUNNING"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"model_names": []},
    {"model_names": ["不存在车型"]},
    {"model_names": ["比亚迪海狮05"], "extra": True},
])
async def test_create_rejects_empty_unknown_and_extra_inputs_in_chinese(engine, body):
    app = _app(engine)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/model-search-runs", json=body)

    assert response.status_code in {409, 422}
    assert isinstance(response.json()["detail"], str)


@pytest.mark.asyncio
async def test_model_library_options_groups_same_name_variants(engine):
    app = _app(engine)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/model-library/options", params={"q": "海狮"})

    assert response.status_code == 200
    assert response.json()["items"] == [{
        "model_name": "比亚迪海狮05",
        "variant_count": 2,
        "last_seen_at": None,
    }]


@pytest.mark.asyncio
async def test_completed_run_reports_retries_without_stale_failure(engine):
    """最终全部成功的任务不能继续把已恢复的临时异常显示成失败。"""
    app = _app(engine)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/model-search-runs", json={
            "model_names": ["比亚迪海狮05"],
        })
        run_id = created.json()["id"]
        with Session(engine) as session:
            run = session.get(ModelSearchRun, run_id)
            run.status = ModelSearchRunStatus.COMPLETED
            run.phase = ModelSearchPhase.FINE
            run.completed_sample_count = 8
            run.failed_sample_count = 0
            run.request_count = 12
            run.last_error_summary = "神州匿名接口网络请求失败"
            session.commit()

        response = await client.get(f"/api/model-search-runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["final_success"] is True
    assert response.json()["retry_count"] == 4
    assert response.json()["last_error_summary"] is None
