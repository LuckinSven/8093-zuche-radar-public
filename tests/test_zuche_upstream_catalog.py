from datetime import datetime

import httpx
import pytest

from app.main import create_app


class ProbeGateway:
    def __init__(self):
        self.choose_query = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def list_cities(self):
        return [{"cityId": "14", "cityName": "广州"}, {"cityId": "20", "cityName": "深圳"}]

    async def resolve_city(self, latitude, longitude):
        return type("Resolution", (), {"city_id": "14", "city_name": "广州"})()

    async def choose_car(self, query):
        self.choose_query = query
        return {"code": 1, "content": {"deptHangModels": [
            {"deptId": 1, "models": [{"modelId": 101}, {"modelId": 102}]},
            {"deptId": 2, "models": [{"modelId": 103}]},
        ]}}

    async def list_departments(self, city_id):
        return [
            {"deptId": 79340, "deptName": "鱼珠地铁站服务点"},
            {"deptId": 2033, "deptName": "东圃服务点"},
        ]


@pytest.mark.asyncio
async def test_upstream_catalog_api_returns_evidence_scoped_inventory():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/api/zuche/upstream")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 38
    assert body["scope"] == "当前项目、参考项目和神州公开 H5 首页资源中可观察到的 URI"
    entries = {item["uri"]: item for item in body["items"]}
    assert entries["/resource/carrctapi/order/chooseCar/v3"]["status"] == "verified"
    assert entries["/resource/carrctapi/order/chooseCar/v3"]["version"] == "v3"
    assert entries["/action/carrctapi/order/cityLocation/v1"]["status"] == "verified"
    assert entries["/action/carrctapi/order/cityLocation/v1"]["last_verified_at"] == "2026-09-01 22:36 +08:00"
    directory = entries["/action/carrctapi/order/deptList/v1"]
    assert directory["status"] == "verified"
    assert directory["auth"] == "anonymous"
    assert directory["safe_probe"] is True
    assert directory["request_fields"] == ["cityId", "entrance", "pickupFlag"]
    assert "content.districtList[].deptList[]" in directory["response_fields"]
    assert "modelImgUrl" in entries["/resource/carrctapi/order/chooseCar/v3"]["not_persisted_fields"]
    assert entries["/resource/carrctapi/order/chooseCar/v1"]["status"] == "observed"
    assert entries["/resource/carrctapi/base/cityList/v1"]["status"] == "lead"


@pytest.mark.asyncio
async def test_safe_upstream_probes_report_sanitized_results_without_persisting_data():
    app = create_app()
    gateway = ProbeGateway()
    app.state.zuche_client_factory = lambda: gateway
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        cities = await client.post("/api/zuche/upstream/probe/city-list-v1")
        location = await client.post("/api/zuche/upstream/probe/city-location-v1")
        departments = await client.post("/api/zuche/upstream/probe/dept-list-v1")
        cars = await client.post("/api/zuche/upstream/probe/choose-car-v3")

    assert cities.status_code == location.status_code == departments.status_code == cars.status_code == 200
    assert cities.json()["summary"] == "返回 2 个城市"
    assert location.json()["summary"] == "识别为广州（城市 ID 14）"
    assert departments.json()["summary"] == "广州目录返回 2 个网点"
    assert cars.json()["summary"] == "返回 2 个网点、3 条车型报价"
    assert datetime.fromisoformat(cars.json()["checked_at"])
    assert gateway.choose_query.city_id == "14"
    assert gateway.choose_query.return_time > gateway.choose_query.pickup_time


@pytest.mark.asyncio
async def test_probe_rejects_unverified_leads_without_calling_the_gateway():
    app = create_app()
    app.state.zuche_client_factory = lambda: (_ for _ in ()).throw(AssertionError("不应调用上游"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.post("/api/zuche/upstream/probe/base-city-list-v1")

    assert response.status_code == 404
    assert response.json()["detail"] == "该接口未列入安全探测范围"
