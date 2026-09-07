import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_healthz_reports_service_name():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"service": "zuche-radar", "status": "ok", "database": "ok"}
