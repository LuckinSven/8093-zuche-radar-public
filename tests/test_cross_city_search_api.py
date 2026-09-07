from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.main import create_app
from app.model_search.service import ModelSearchService
from app.models import City, Department, VehicleModel


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _app(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        guangzhou = City(zuche_city_id="14", name="广州")
        wuhan = City(zuche_city_id="20", name="武汉")
        target = VehicleModel(
            zuche_model_id=4677,
            name="小鹏P7+",
            latest_description="60kWh电动粤A牌 | 三厢5座",
        )
        other = VehicleModel(
            zuche_model_id=5139,
            name="小鹏P7+",
            latest_description="49kWh增程 | 三厢5座",
        )
        session.add_all([guangzhou, wuhan, target, other])
        session.flush()
        session.add_all([
            Department(
                city_id=guangzhou.id,
                zuche_dept_id=79340,
                name="鱼珠地铁站服务点",
                latitude=23.101610,
                longitude=113.432649,
            ),
            Department(
                city_id=wuhan.id,
                zuche_dept_id=86,
                name="武广服务点",
                latitude=30.6101,
                longitude=114.4240,
            ),
        ])
        session.commit()
        ids = guangzhou.id, wuhan.id, target.id
    app = create_app(session_factory=factory)
    app.state.model_search_service = ModelSearchService(
        factory, lambda: (_ for _ in ()).throw(
            AssertionError("创建和控制任务不应请求神州")))
    return app, ids


def _payload(city_ids, model_id):
    guangzhou_id, wuhan_id = city_ids
    first = datetime(2026, 9, 24, 9, tzinfo=SHANGHAI)
    second = datetime(2026, 9, 25, 9, tzinfo=SHANGHAI)
    return {
        "zuche_model_id": 4677,
        "pickup_city_ids": [wuhan_id],
        "return_city_id": guangzhou_id,
        "return_location_name": "鱼珠地铁站服务点",
        "windows": [
            {"pickup_time": first.isoformat(),
             "return_time": (first + timedelta(days=14)).isoformat()},
            {"pickup_time": second.isoformat(),
             "return_time": (second + timedelta(days=14)).isoformat()},
        ],
        "rail_costs": {str(wuhan_id): 600},
    }


@pytest.mark.asyncio
async def test_create_and_read_cross_city_search_with_exact_variant(engine):
    """API 必须保留精确车型和两组十四天租期，不能退化成同名搜索。"""
    app, (guangzhou_id, wuhan_id, target_id) = _app(engine)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/cross-city-search-runs", json=_payload(
            (guangzhou_id, wuhan_id), target_id))
        run_id = created.json()["id"]
        detail = await client.get(f"/api/cross-city-search-runs/{run_id}")

    assert created.status_code == 201
    assert created.json()["search_kind"] == "CROSS_CITY"
    assert created.json()["planned_sample_count"] == 2
    assert detail.json()["vehicle_model"]["zuche_model_id"] == 4677
    assert detail.json()["pickup_cities"][0]["name"] == "武汉"
    assert detail.json()["return_city"]["name"] == "广州"
    assert detail.json()["return_location_name"] == "鱼珠地铁站服务点"
    assert len(detail.json()["rental_windows"]) == 2


@pytest.mark.asyncio
async def test_cross_city_search_rejects_non_fourteen_day_window_in_chinese(engine):
    """租期不是十四天时不能静默创建不符合优惠目标的任务。"""
    app, (guangzhou_id, wuhan_id, target_id) = _app(engine)
    body = _payload((guangzhou_id, wuhan_id), target_id)
    body["windows"][0]["return_time"] = datetime(
        2026, 10, 7, 9, tzinfo=SHANGHAI).isoformat()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/cross-city-search-runs", json=body)

    assert response.status_code == 422
    assert "14 天" in response.json()["detail"]


@pytest.mark.asyncio
async def test_cross_city_run_can_be_listed_controlled_and_read_as_incomplete(engine):
    """专项页面必须能恢复历史任务，并且未执行样本不能显示成未找到。"""
    app, (guangzhou_id, wuhan_id, target_id) = _app(engine)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/cross-city-search-runs", json=_payload(
            (guangzhou_id, wuhan_id), target_id))
        run_id = created.json()["id"]
        listed = await client.get("/api/cross-city-search-runs")
        weekend_list = await client.get("/api/model-search-runs")
        results = await client.get(f"/api/cross-city-search-runs/{run_id}/results")
        stopped = await client.post(f"/api/cross-city-search-runs/{run_id}/stop")
        resumed = await client.post(f"/api/cross-city-search-runs/{run_id}/resume")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == run_id
    assert weekend_list.json()["items"] == []
    assert results.status_code == 200
    assert results.json()["items"][0]["availability"] == "INCOMPLETE"
    assert stopped.json()["status"] == "STOPPED"
    assert resumed.json()["status"] == "RUNNING"
