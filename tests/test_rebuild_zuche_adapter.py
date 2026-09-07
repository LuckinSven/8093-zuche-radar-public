import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from app.domain import ScanQuery
from app.zuche.client import ZucheClient, ZucheGatewayError
from app.zuche.parser import parse_choose_car


FIXTURE = Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json"


def query() -> ScanQuery:
    return ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
                     pickup_time=datetime(2026, 9, 5, 9), return_time=datetime(2026, 9, 6, 9))


def test_parser_preserves_source_and_derived_fields():
    payload = json.loads(FIXTURE.read_text())
    payload["content"]["deptHangModels"][0]["deptDistanceDouble"] = 2039
    parsed = parse_choose_car(payload, query())
    department = parsed.departments[0]
    offer = next(item for item in parsed.offers if item.model_id == 4666)
    assert department.business_hours == "00:00-23:59"
    assert department.is_open_24h is False
    assert department.self_service_pickup is True
    assert department.self_service_return is True
    assert (offer.daily_price, offer.distance_km, offer.body_style, offer.seat_count) == (98, 2.04, "SUV", 5)
    group = next(group for group in parsed.groups if group.model_id == 4666)
    assert group.group_id == 16
    assert group.group_low_price_desc == "¥98起"
    assert group.sort_num == 3
    assert group.model_name == "大众途铠"
    assert group.model_low_price_desc == "¥98起"
    assert offer.image_url == "https://dfs.zuchecdn.com/redacted/4666.png"


def test_parser_keeps_group_model_image_when_upstream_supplies_it():
    """若分组车型图片未展开，车型库无法在报价缺图时补全图片。"""
    payload = json.loads(FIXTURE.read_text())
    payload["content"]["modelGroups"][0]["modelItems"][0]["modelImgUrl"] = (
        "https://dfs.zuchecdn.com/redacted/group-4665.png"
    )

    parsed = parse_choose_car(payload, query())
    group = next(item for item in parsed.groups if item.model_id == 4665)

    assert group.model_image_url == "https://dfs.zuchecdn.com/redacted/group-4665.png"


def test_parser_converts_numeric_department_distance_from_meters_when_text_is_missing():
    payload = json.loads(FIXTURE.read_text())
    department = payload["content"]["deptHangModels"][0]
    department.pop("deptDistance")
    department["deptDistanceDouble"] = 2039

    parsed = parse_choose_car(payload, query())

    assert parsed.departments[0].distance_km == pytest.approx(2.039)
    assert parsed.offers[0].distance_km == pytest.approx(2.039)


def test_parser_classifies_explicit_new_energy_evidence_without_misclassifying_gasoline():
    payload = json.loads(FIXTURE.read_text())
    payload["content"]["deptHangModels"][0]["models"] = [
        {"modelId": 7001, "modelName": "唐新能源", "modelDesc": "SUV5座"},
        {"modelId": 7002, "modelName": "秦PLUS", "modelDesc": "1.5T插电混 SUV5座"},
        {"modelId": 7003, "modelName": "深蓝SL03", "modelDesc": "32kWh增程式 三厢5座"},
        {"modelId": 7004, "modelName": "普通轿车", "modelDesc": "1.5自动 三厢5座"},
        {"modelId": 7005, "modelName": "燃油蓝牙版", "modelDesc": "蓝牙自动 三厢5座"},
    ]
    payload["content"]["modelGroups"] = [{
        "groupId": 70,
        "name": "新能源车",
        "modelItems": [{"modelId": 7004}],
    }]

    parsed = parse_choose_car(payload, query())

    assert {offer.model_id for offer in parsed.offers if offer.energy_type == "新能源"} == {
        7001, 7002, 7003, 7004,
    }
    assert next(offer for offer in parsed.offers if offer.model_id == 7005).energy_type is None


def test_parser_classifies_a_model_only_marked_by_an_insert_hybrid_group():
    payload = json.loads(FIXTURE.read_text())
    payload["content"]["deptHangModels"][0]["models"] = [{
        "modelId": 7006,
        "modelName": "普通轿车",
        "modelDesc": "1.5自动 三厢5座",
    }]
    payload["content"]["modelGroups"] = [{
        "groupId": 71,
        "name": "插混车型",
        "modelItems": [{"modelId": 7006}],
    }]

    parsed = parse_choose_car(payload, query())

    assert parsed.offers[0].energy_type == "新能源"


@pytest.mark.asyncio
async def test_client_uses_gateway_form_contract(httpx_mock):
    httpx_mock.add_response(json=json.loads(FIXTURE.read_text()))
    async with ZucheClient() as client:
        await client.choose_car(query())
    request = httpx_mock.get_request()
    assert request.url.params["uri"] == "/resource/carrctapi/order/chooseCar/v3"
    data = json.loads(parse_qs(request.content.decode())["data"][0])
    assert data == {
        "pickupCityId": "14",
        "pickupTime": "2026-09-05 09:00",
        "returnCityId": "14",
        "returnTime": "2026-09-06 09:00",
        "entrance": 1,
        "userChooseLat": 23.1291,
        "userChooseLon": 113.2644,
        "holidaysWaitingFlag": 0,
    }


@pytest.mark.asyncio
async def test_client_sends_a_distinct_return_city_for_cross_city_search(httpx_mock):
    """若还车城市仍取取车城市，异地还鱼珠的搜索结果就没有意义。"""
    httpx_mock.add_response(json=json.loads(FIXTURE.read_text()))
    cross_city_query = ScanQuery(
        city_id="20",
        return_city_id="14",
        location_name="武汉站",
        latitude=30.6101,
        longitude=114.4240,
        pickup_time=datetime(2026, 9, 24, 9),
        return_time=datetime(2026, 10, 8, 9),
    )

    async with ZucheClient() as client:
        await client.choose_car(cross_city_query)

    request = httpx_mock.get_request()
    data = json.loads(parse_qs(request.content.decode())["data"][0])
    assert data["pickupCityId"] == "20"
    assert data["returnCityId"] == "14"


@pytest.mark.asyncio
async def test_client_lists_anonymous_city_catalog_from_city_list_content(httpx_mock):
    httpx_mock.add_response(json={"code": 1, "content": {"cityList": [
        {"cityId": "14", "cityName": "广州"}, {"cityId": "20", "cityName": "深圳"},
    ]}})

    async with ZucheClient() as client:
        cities = await client.list_cities()

    request = httpx_mock.get_request()
    assert request.url.params["uri"] == "/action/carrctapi/order/cityList/v1"
    assert request.headers.get("cookie") is None
    assert cities == [{"cityId": "14", "cityName": "广州"}, {"cityId": "20", "cityName": "深圳"}]


@pytest.mark.asyncio
async def test_client_lists_anonymous_city_catalog_from_confirmed_all_cities_content(httpx_mock):
    httpx_mock.add_response(json={"code": 1, "content": {"allCities": [
        {"cityId": "14", "cityName": "广州", "cityLat": "23.1291", "cityLon": "113.2644",
         "code": "GZ", "enName": "Guangzhou"},
    ]}})

    async with ZucheClient() as client:
        cities = await client.list_cities()

    assert cities == [{"cityId": "14", "cityName": "广州", "cityLat": "23.1291", "cityLon": "113.2644",
                       "code": "GZ", "enName": "Guangzhou"}]


@pytest.mark.asyncio
async def test_client_lists_all_departments_and_keeps_their_district_context(httpx_mock):
    httpx_mock.add_response(json={"code": 1, "content": {"districtList": [
        {
            "districtId": 15,
            "districtName": "越秀区",
            "deptCount": 1,
            "deptList": [{
                "deptId": 55274,
                "deptName": "淘金保利时光里服务点",
                "deptAddress": "广东省广州市越秀区环市东路334号",
                "deptLat": "23.137479",
                "deptLon": "113.28212",
                "workTime": "08:00-21:00",
                "wholeDayFlag": False,
                "selfServiceFlag": True,
                "inventoryAbleFlag": True,
            }],
        },
        {"districtId": 16, "districtName": "机场周边", "deptCount": 0, "deptList": []},
    ]}})

    async with ZucheClient() as client:
        departments = await client.list_departments("14")

    request = httpx_mock.get_request()
    assert request.url.params["uri"] == "/action/carrctapi/order/deptList/v1"
    assert request.headers.get("cookie") is None
    assert json.loads(parse_qs(request.content.decode())["data"][0]) == {
        "cityId": "14", "entrance": 1, "pickupFlag": 1,
    }
    assert departments == [{
        "deptId": 55274,
        "deptName": "淘金保利时光里服务点",
        "deptAddress": "广东省广州市越秀区环市东路334号",
        "deptLat": "23.137479",
        "deptLon": "113.28212",
        "workTime": "08:00-21:00",
        "wholeDayFlag": False,
        "selfServiceFlag": True,
        "inventoryAbleFlag": True,
        "districtId": 15,
        "districtName": "越秀区",
    }]


@pytest.mark.asyncio
async def test_client_converts_unknown_business_errors_to_gateway_error(httpx_mock):
    httpx_mock.add_response(json={"code": 0, "msg": "上游业务提示"})

    async with ZucheClient() as client:
        with pytest.raises(ZucheGatewayError, match="神州接口业务处理失败"):
            await client.list_cities()
