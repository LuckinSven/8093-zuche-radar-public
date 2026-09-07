import json
import copy
from datetime import datetime
from pathlib import Path

import pytest
import httpx
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.discovery import DiscoveryFilters, DiscoveryService
from app.domain import ScanQuery
from app.main import create_app
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger
from app.models import AvailabilitySnapshot


class Gateway:
    async def choose_car(self, _):
        return json.loads((Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json").read_text())


class PayloadGateway:
    def __init__(self, payload):
        self.payload = payload

    async def choose_car(self, _):
        return self.payload


@pytest.mark.asyncio
async def test_discovery_combines_native_group_price_and_distance_filters(session):
    query = ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
                      pickup_time=datetime(2026, 9, 5, 9), return_time=datetime(2026, 9, 6, 9))
    result = await ScanService(Gateway(), ScanRepository(session)).run(query, ScanTrigger.MANUAL)

    items = DiscoveryService(session).search(DiscoveryFilters(
        scan_id=result.scan_id, group_id=16, max_price=100, max_distance_km=3))

    assert [(item.model_id, item.model_name, item.native_groups) for item in items] == [(4666, "大众途铠", ["SUV"])]
    assert items[0].price_is_final is False
    assert items[0].personal_state == "UNTRIED"

    snapshot = session.scalar(select(AvailabilitySnapshot).where(
        AvailabilitySnapshot.vehicle_model_id == snapshot_model_id(session, 4666)))
    snapshot.department_distance = "上游格式已变化"
    session.commit()
    refreshed = DiscoveryService(session).search(DiscoveryFilters(scan_id=result.scan_id, group_id=16))
    assert refreshed[0].nearest_distance_km == 2.04


@pytest.mark.asyncio
async def test_disappeared_filter_returns_model_from_previous_snapshot(session):
    original = json.loads((Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json").read_text())
    query = ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
                      pickup_time=datetime(2026, 9, 5, 9), return_time=datetime(2026, 9, 6, 9))
    await ScanService(PayloadGateway(original), ScanRepository(session)).run(query, ScanTrigger.MANUAL)
    current = copy.deepcopy(original)
    current["content"]["deptHangModels"][0]["models"] = [
        item for item in current["content"]["deptHangModels"][0]["models"] if item["modelId"] != 4665]
    result = await ScanService(PayloadGateway(current), ScanRepository(session)).run(query, ScanTrigger.MANUAL)

    items = DiscoveryService(session).search(DiscoveryFilters(scan_id=result.scan_id, change_type="DISAPPEARED"))

    assert [(item.model_id, item.model_name, item.changes) for item in items] == [
        (4665, "雪佛兰科鲁泽", ["DISAPPEARED"])]
    assert items[0].offers[0].bookable is False


@pytest.mark.asyncio
async def test_disappeared_card_uses_exact_predecessor_groups_and_all_offers(session):
    original = json.loads((Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json").read_text())
    query = ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
                      pickup_time=datetime(2026, 9, 5, 9), return_time=datetime(2026, 9, 6, 9))
    first = await ScanService(PayloadGateway(original), ScanRepository(session)).run(query, ScanTrigger.MANUAL)

    # 同车型在前序扫描的第二个网点也有报价，消失卡片必须完整恢复两个报价。
    second_department = copy.deepcopy(original["content"]["deptHangModels"][0])
    second_department["deptId"] = 99002
    second_department["deptName"] = "广州第二服务点"
    second_department["distance"] = "8.50km"
    second_department["models"] = [copy.deepcopy(second_department["models"][1])]
    original["content"]["deptHangModels"].append(second_department)
    predecessor = await ScanService(PayloadGateway(original), ScanRepository(session)).run(query, ScanTrigger.MANUAL)

    # 另一个地点稍后扫描同一车型，不能被误当作本地点的前序快照。
    other_query = query.model_copy(update={"location_name": "深圳临时点", "city_id": "16",
                                           "latitude": 22.5431, "longitude": 114.0579})
    await ScanService(PayloadGateway(original), ScanRepository(session)).run(other_query, ScanTrigger.MANUAL)

    current = copy.deepcopy(original)
    for department in current["content"]["deptHangModels"]:
        department["models"] = [item for item in department["models"] if item["modelId"] != 4666]
    result = await ScanService(PayloadGateway(current), ScanRepository(session)).run(query, ScanTrigger.MANUAL)

    items = DiscoveryService(session).search(DiscoveryFilters(
        scan_id=result.scan_id, change_type="DISAPPEARED", group_id=16, max_distance_km=3))

    assert len(items) == 1
    assert items[0].model_id == 4666
    assert items[0].native_groups == ["SUV"]
    assert len(items[0].offers) == 2
    assert items[0].nearest_department == "广州测试服务点"
    assert {item["name"] for item in DiscoveryService(session).list_groups(result.scan_id)} == {"经济型", "SUV"}
    from app.models import ChangeEvent
    event = session.scalar(select(ChangeEvent).where(
        ChangeEvent.scan_run_id == result.scan_id, ChangeEvent.event_type == "DISAPPEARED"))
    assert json.loads(event.detail)["previous_scan_id"] == str(predecessor.scan_id)
    assert first.scan_id != predecessor.scan_id


def energy_payload():
    payload = json.loads((Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json").read_text())
    payload["content"]["deptHangModels"][0]["models"] = [
        {"modelId": 7001, "modelName": "唐新能源", "modelDesc": "SUV5座", "dailyPrice": "100",
         "packagePrice": "100", "bookFlag": True},
        {"modelId": 7002, "modelName": "秦PLUS", "modelDesc": "1.5T插电混 SUV5座", "dailyPrice": "101",
         "packagePrice": "101", "bookFlag": True},
        {"modelId": 7003, "modelName": "深蓝SL03", "modelDesc": "32kWh增程式 三厢5座", "dailyPrice": "102",
         "packagePrice": "102", "bookFlag": True},
        {"modelId": 7004, "modelName": "普通轿车", "modelDesc": "1.5自动 三厢5座", "dailyPrice": "103",
         "packagePrice": "103", "bookFlag": True},
        {"modelId": 7005, "modelName": "燃油蓝牙版", "modelDesc": "蓝牙自动 三厢5座", "dailyPrice": "104",
         "packagePrice": "104", "bookFlag": True},
    ]
    payload["content"]["modelGroups"] = [{
        "groupId": 70,
        "name": "新能源车",
        "modelItems": [{"modelId": 7004}],
    }]
    return payload


def energy_query():
    return ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
                     pickup_time=datetime(2026, 9, 5, 9), return_time=datetime(2026, 9, 6, 9))


@pytest.mark.asyncio
async def test_new_energy_filter_keeps_all_explicit_signals_and_excludes_gasoline(session):
    result = await ScanService(PayloadGateway(energy_payload()), ScanRepository(session)).run(
        energy_query(), ScanTrigger.MANUAL)

    items = DiscoveryService(session).search(DiscoveryFilters(
        scan_id=result.scan_id, energy_type="新能源"))

    assert [item.model_id for item in items] == [7001, 7002, 7003, 7004]
    assert {item.model_id for item in items if item.native_groups == ["新能源车"]} == {7004}
    assert all(item.model_id != 7005 for item in items)


@pytest.mark.asyncio
async def test_disappeared_new_energy_card_uses_the_same_energy_classification(session):
    first = await ScanService(PayloadGateway(energy_payload()), ScanRepository(session)).run(
        energy_query(), ScanTrigger.MANUAL)
    current = energy_payload()
    current["content"]["deptHangModels"][0]["models"] = [
        item for item in current["content"]["deptHangModels"][0]["models"] if item["modelId"] != 7004
    ]
    result = await ScanService(PayloadGateway(current), ScanRepository(session)).run(
        energy_query(), ScanTrigger.MANUAL)

    items = DiscoveryService(session).search(DiscoveryFilters(
        scan_id=result.scan_id, change_type="DISAPPEARED", energy_type="新能源"))

    assert first.status == "SUCCESS"
    assert [(item.model_id, item.native_groups) for item in items] == [(7004, ["新能源车"])]


@pytest.mark.asyncio
async def test_discovery_api_accepts_new_energy_query_filter(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        result = await ScanService(PayloadGateway(energy_payload()), ScanRepository(session)).run(
            energy_query(), ScanTrigger.MANUAL)
    app = create_app(session_factory=factory)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/discovery?scan_id={result.scan_id}&energy_type=新能源")

    assert response.status_code == 200
    assert [item["model_id"] for item in response.json()["items"]] == [7001, 7002, 7003, 7004]


def snapshot_model_id(session, external_id):
    from app.models import VehicleModel
    return session.scalar(select(VehicleModel.id).where(VehicleModel.zuche_model_id == external_id))
