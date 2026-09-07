import gzip
import json
import copy
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest
import httpx
from sqlalchemy import select

from app.domain import ScanQuery
from app.models import Department, RawPayload, ScanRun
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger
from app.zuche.client import ZucheGatewayError


FIXTURE = Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json"


class Gateway:
    def __init__(self, response: dict | Exception):
        self.response = response

    async def choose_car(self, _: ScanQuery) -> dict:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def scan_query() -> ScanQuery:
    return ScanQuery(city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
                     pickup_time=datetime(2026, 9, 5, 9), return_time=datetime(2026, 9, 6, 9))


def payload() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.mark.asyncio
async def test_successful_scan_is_persisted_as_one_atomic_history_batch(session):
    repository = ScanRepository(session)
    result = await ScanService(Gateway(payload()), repository).run(scan_query(), ScanTrigger.MANUAL)

    assert result.status == "SUCCESS"
    assert result.department_count == 1
    assert result.offer_count == 2
    assert {event.event_type for event in result.events} == {"FIRST_SEEN"}
    compressed = session.scalar(select(RawPayload.payload))
    department = session.scalar(select(Department).where(Department.zuche_dept_id == 1001))
    assert json.loads(gzip.decompress(compressed))["uid"] == "REDACTED"
    assert department.business_hours == "00:00-23:59"
    assert department.is_open_24h is False
    assert department.self_service_pickup is True
    assert department.self_service_return is True


@pytest.mark.asyncio
async def test_failed_scan_keeps_previous_successful_snapshots(session):
    repository = ScanRepository(session)
    await ScanService(Gateway(payload()), repository).run(scan_query(), ScanTrigger.MANUAL)

    result = await ScanService(Gateway(ZucheGatewayError("服务繁忙")), repository).run(scan_query(), ScanTrigger.MANUAL)

    assert result.status == "FAILED"
    assert repository.count_snapshots() == 2
    assert repository.get_run_status(result.scan_id) == "FAILED"


@pytest.mark.asyncio
async def test_scan_retries_server_errors_but_not_client_errors(session, monkeypatch):
    monkeypatch.setattr("app.scanning.service.asyncio.sleep", lambda _: _done())

    class SequenceGateway:
        def __init__(self, statuses):
            self.statuses = iter(statuses)
            self.attempts = 0

        async def choose_car(self, _):
            self.attempts += 1
            value = next(self.statuses)
            if isinstance(value, int):
                request = httpx.Request("POST", "https://m.zuche.com/api/gw.do")
                response = httpx.Response(value, request=request)
                raise httpx.HTTPStatusError("gateway error", request=request, response=response)
            return value

    server = SequenceGateway([503, 502, payload()])
    succeeded = await ScanService(server, ScanRepository(session)).run(scan_query(), ScanTrigger.MANUAL)
    client = SequenceGateway([400, payload()])
    failed = await ScanService(client, ScanRepository(session)).run(scan_query(), ScanTrigger.MANUAL)

    assert succeeded.status == "SUCCESS"
    assert server.attempts == 3
    assert failed.status == "FAILED"
    assert client.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "expected_code", "expected_message"), [
    (httpx.ReadTimeout("private https://m.zuche.com/api/gw.do"),
     "UPSTREAM_TIMEOUT", "神州接口请求超时，请稍后重试"),
    (httpx.ConnectError("private https://m.zuche.com/api/gw.do"),
     "UPSTREAM_CONNECTION_ERROR", "暂时无法连接神州接口，请稍后重试"),
    (ZucheGatewayError("private-token=https://m.zuche.com"),
     "UPSTREAM_RESPONSE_ERROR", "神州接口返回异常，请稍后重试"),
])
async def test_scan_failure_hides_raw_upstream_details(
        session, monkeypatch, error, expected_code, expected_message):
    monkeypatch.setattr("app.scanning.service.asyncio.sleep", lambda _: _done())

    result = await ScanService(Gateway(error), ScanRepository(session)).run(
        scan_query(), ScanTrigger.MANUAL)
    run = session.get(ScanRun, result.scan_id)

    assert result.status == "FAILED"
    assert result.error_message == expected_message
    assert run.error_code == expected_code
    assert run.error_message == expected_message
    assert "http" not in result.error_message


@pytest.mark.asyncio
async def test_scan_server_failure_uses_stable_safe_message(session, monkeypatch):
    monkeypatch.setattr("app.scanning.service.asyncio.sleep", lambda _: _done())
    request = httpx.Request("POST", "https://m.zuche.com/api/gw.do?secret=value")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("private upstream details", request=request, response=response)

    result = await ScanService(Gateway(error), ScanRepository(session)).run(
        scan_query(), ScanTrigger.MANUAL)
    run = session.get(ScanRun, result.scan_id)

    assert result.error_message == "神州接口暂时不可用，请稍后重试"
    assert run.error_code == "UPSTREAM_UNAVAILABLE"
    assert run.error_message == result.error_message
    assert "secret" not in run.error_message


@pytest.mark.asyncio
async def test_change_events_are_model_level_across_three_scans(session):
    repository = ScanRepository(session)
    original = payload()
    second = copy.deepcopy(original)
    second["content"]["deptHangModels"][0]["models"] = [
        item for item in second["content"]["deptHangModels"][0]["models"] if item["modelId"] != 4665]
    third = copy.deepcopy(original)
    changed = next(item for item in third["content"]["deptHangModels"][0]["models"] if item["modelId"] == 4666)
    changed["packagePrice"] = changed["dailyPrice"] = "108"

    first = await ScanService(Gateway(original), repository).run(scan_query(), ScanTrigger.MANUAL)
    missing = await ScanService(Gateway(second), repository).run(scan_query(), ScanTrigger.MANUAL)
    returned = await ScanService(Gateway(third), repository).run(scan_query(), ScanTrigger.MANUAL)

    assert sorted(event.event_type for event in first.events) == ["FIRST_SEEN", "FIRST_SEEN"]
    assert len(missing.events) == 1
    assert missing.events[0].event_type == "DISAPPEARED"
    assert sorted(event.event_type for event in returned.events) == ["PRICE_CHANGED", "REAPPEARED"]


@pytest.mark.asyncio
async def test_never_seen_model_is_first_seen_even_when_previous_scan_is_not_empty(session):
    repository = ScanRepository(session)
    await ScanService(Gateway(payload()), repository).run(scan_query(), ScanTrigger.MANUAL)
    expanded = copy.deepcopy(payload())
    model = copy.deepcopy(expanded["content"]["deptHangModels"][0]["models"][0])
    model.update({"modelId": 4999, "modelName": "首次车型", "dailyPrice": "120", "packagePrice": "120"})
    expanded["content"]["deptHangModels"][0]["models"].append(model)

    result = await ScanService(Gateway(expanded), repository).run(scan_query(), ScanTrigger.MANUAL)

    assert [event.event_type for event in result.events] == ["FIRST_SEEN"]


@pytest.mark.asyncio
async def test_scheduled_scans_compare_same_time_template_on_different_dates(session):
    zone = ZoneInfo("Asia/Shanghai")
    first_query = scan_query().model_copy(update={"pickup_time": datetime(2026, 9, 5, 9, tzinfo=zone),
                                                  "return_time": datetime(2026, 9, 6, 9, tzinfo=zone)})
    next_query = scan_query().model_copy(update={"pickup_time": datetime(2026, 9, 6, 9, tzinfo=zone),
                                                 "return_time": datetime(2026, 9, 7, 9, tzinfo=zone)})
    await ScanService(Gateway(payload()), ScanRepository(session)).run(first_query, ScanTrigger.SCHEDULED)
    changed_payload = copy.deepcopy(payload())
    changed = changed_payload["content"]["deptHangModels"][0]["models"][0]
    changed["packagePrice"] = changed["dailyPrice"] = "99"

    result = await ScanService(Gateway(changed_payload), ScanRepository(session)).run(next_query, ScanTrigger.SCHEDULED)

    assert [event.event_type for event in result.events] == ["PRICE_CHANGED"]


async def _done():
    return None
