import copy
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.domain import ScanQuery
from app.main import create_app
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger


@pytest.mark.asyncio
async def test_summary_separates_fish_models_from_nearby_supplements(engine):
    payload = json.loads((Path(__file__).parent / "fixtures" /
                          "choose_car_guangzhou_redacted.json").read_text())
    fish = payload["content"]["deptHangModels"][0]
    fish["deptId"] = 79340
    fish["deptName"] = "鱼珠地铁站服务点"
    nearby = copy.deepcopy(fish)
    nearby["deptId"] = 88001
    nearby["deptName"] = "美林天地服务点"
    nearby["models"] = [copy.deepcopy(fish["models"][0]), {
        "modelId": 99001, "modelName": "附近独有车型", "modelDesc": "SUV5座",
        "dailyPrice": "188", "packagePrice": "188", "bookFlag": True,
    }]
    payload["content"]["deptHangModels"].append(nearby)

    class Gateway:
        async def choose_car(self, _query):
            return payload

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        result = await ScanService(Gateway(), ScanRepository(session)).run(
            ScanQuery(city_id="14", location_name="鱼珠地铁站服务点", latitude=23.103,
                      longitude=113.432, pickup_time="2026-09-05T09:00:00",
                      return_time="2026-09-06T09:00:00"), ScanTrigger.MANUAL)

    app = create_app(session_factory=factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/api/discovery/summary", params={
            "scan_id": str(result.scan_id), "primary_department_id": 79340})

    assert response.status_code == 200
    body = response.json()
    assert body["primary_model_count"] == 2
    assert body["supplemental_model_count"] == 1
    assert body["new_or_reappeared_count"] == 3
    assert body["primary_department_name"] == "鱼珠地铁站服务点"
    assert body["pickup_time"].startswith("2026-09-05T09:00:00")
    assert body["return_time"].startswith("2026-09-06T09:00:00")
