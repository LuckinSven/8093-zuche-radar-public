import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_ai_enrichment_settings_page_exposes_complete_configuration(engine):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/settings/ai-enrichment")

    assert response.status_code == 200
    assert 'id="ai-enrichment-settings-form"' in response.text
    assert 'name="api_key"' in response.text
    assert 'name="base_url"' in response.text
    assert 'name="model"' in response.text
    assert 'name="batch_size" type="hidden" value="1"' in response.text
    assert "逐车型处理" in response.text
    assert "每次仅处理 1 款车型" in response.text
    assert 'name="timeout_seconds"' in response.text
    assert 'name="max_retries"' in response.text
    assert 'name="timeout_seconds" type="number" value="30"' in response.text
    assert 'name="max_retries" type="number" value="1"' in response.text
    assert 'id="test-ai-enrichment"' in response.text
    assert 'id="clear-ai-enrichment-secret"' in response.text
    assert 'href="/settings/ai-enrichment"' in response.text


@pytest.mark.asyncio
async def test_library_exposes_ai_enrichment_without_cluttering_filters(engine):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        library = await client.get("/library")
        history = await client.get("/library/ai-runs")

    assert library.status_code == 200
    assert 'id="start-ai-enrichment"' in library.text
    assert 'id="ai-enrichment-progress"' in library.text
    assert 'id="start-ai-enrichment-all"' in library.text
    assert "重新识别全部车型" in library.text
    assert 'href="/library/ai-runs"' in library.text
    assert 'id="ai-enrichment-run-list"' in history.text
    assert 'id="ai-enrichment-run-pages"' in history.text
    assert "删除" in history.text
    assert 'id="manual-energy-resolution-template"' in history.text
    assert 'name="energy_type"' in history.text
    assert 'name="energy_subtype"' in history.text
    assert 'name="note"' in history.text
