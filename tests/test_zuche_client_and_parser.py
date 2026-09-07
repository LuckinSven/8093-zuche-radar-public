import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from app.schemas import ScanRequest
from app.zuche_client import ZucheClient
from app.zuche_parser import parse_choose_car


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json"


def load_choose_car_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_parser_keeps_a_vehicle_available_at_its_department_with_native_group():
    request = ScanRequest(
        city_id="14",
        location_name="广州中心",
        latitude=23.1291,
        longitude=113.2644,
        pickup_time=datetime(2026, 9, 5, 9, 0),
        return_time=datetime(2026, 9, 6, 9, 0),
    )

    parsed = parse_choose_car(load_choose_car_fixture(), request)

    assert parsed.departments[0].zuche_dept_id == 1001
    assert parsed.departments[0].distance_km == 2.04
    assert parsed.availabilities[1].zuche_model_id == 4666
    assert parsed.availabilities[1].daily_price == 98
    assert parsed.availabilities[1].body_style == "SUV"
    assert parsed.availabilities[1].seat_count == 5
    assert parsed.groups[1].group_id == 16
    assert parsed.groups[1].group_name == "SUV"


@pytest.mark.asyncio
async def test_choose_car_posts_json_string_in_data_form_field(httpx_mock):
    httpx_mock.add_response(json=load_choose_car_fixture())
    request = ScanRequest(
        city_id="14",
        location_name="广州中心",
        latitude=23.1291,
        longitude=113.2644,
        pickup_time=datetime(2026, 9, 5, 9, 0),
        return_time=datetime(2026, 9, 6, 9, 0),
    )

    async with ZucheClient() as client:
        response = await client.choose_car(request)

    sent = httpx_mock.get_request()
    assert sent.url.path == "/api/gw.do"
    assert sent.url.params["uri"] == "/resource/carrctapi/order/chooseCar/v3"
    assert json.loads(parse_qs(sent.content.decode())["data"][0]) == {
        "cityId": "14",
        "endTime": "2026-09-06 09:00",
        "lat": "23.129100",
        "lon": "113.264400",
        "startTime": "2026-09-05 09:00"
    }
    assert response["code"] == 1
