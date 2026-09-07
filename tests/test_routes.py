import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from app.main import create_app
from app.repositories import ScanRepository
from app.scanner import ScanService
from app.schemas import ScanRequest


class FixtureClient:
    async def choose_car(self, _: ScanRequest) -> dict:
        return json.loads((Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json").read_text())


@pytest.mark.asyncio
async def test_manual_scan_endpoint_returns_counts(session):
    app = create_app(scanner=ScanService(FixtureClient(), ScanRepository(session)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/scans", json={
            "city_id": "14", "location_name": "广州中心", "latitude": 23.1291, "longitude": 113.2644,
            "pickup_time": "2026-09-05T09:00:00", "return_time": "2026-09-06T09:00:00"
        })

    assert response.status_code == 201
    assert response.json()["department_count"] == 1
