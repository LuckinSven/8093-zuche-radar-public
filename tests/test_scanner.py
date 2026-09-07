import json
import gzip
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import RawPayload
from app.repositories import ScanRepository
from app.scanner import ScanService, ScanTrigger
from app.schemas import ScanRequest
from app.zuche_client import ZucheApiError


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json"


class FixtureClient:
    def __init__(self, payload: dict | Exception):
        self.payload = payload

    async def choose_car(self, _: ScanRequest) -> dict:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture
def scan_request() -> ScanRequest:
    return ScanRequest(
        city_id="14",
        location_name="广州中心",
        latitude=23.1291,
        longitude=113.2644,
        pickup_time=datetime(2026, 9, 5, 9, 0),
        return_time=datetime(2026, 9, 6, 9, 0),
    )


def fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_first_successful_offer_creates_first_seen_event_and_gzipped_raw_payload(session, scan_request):
    service = ScanService(FixtureClient(fixture_payload()), ScanRepository(session))

    result = await service.run(scan_request, ScanTrigger.MANUAL)

    assert result.status == "SUCCESS"
    assert result.department_count == 1
    assert result.availability_count == 2
    assert {event.event_type for event in result.events} == {"FIRST_SEEN"}
    assert ScanRepository(session).count_raw_payloads() == 1
    raw_payload = session.scalar(select(RawPayload.payload))
    assert json.loads(gzip.decompress(raw_payload)) ["uid"] == "REDACTED"


@pytest.mark.asyncio
async def test_failed_gateway_call_marks_run_failed_without_deleting_previous_snapshots(session, scan_request):
    repository = ScanRepository(session)
    successful = ScanService(FixtureClient(fixture_payload()), repository)
    await successful.run(scan_request, ScanTrigger.MANUAL)
    failed = ScanService(FixtureClient(ZucheApiError("TIMEOUT")), repository)

    result = await failed.run(scan_request, ScanTrigger.MANUAL)

    assert result.status == "FAILED"
    assert repository.count_snapshots() == 2
    assert repository.get_run_status(result.scan_run_id) == "FAILED"
