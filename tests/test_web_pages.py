import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_discovery_page_exposes_scan_form_and_next_saturday_preset():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert 'data-preset="next_saturday"' in response.text
    assert 'id="manual-scan-form"' in response.text
