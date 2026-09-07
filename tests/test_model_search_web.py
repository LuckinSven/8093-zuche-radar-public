from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_home_focuses_on_citywide_scan_and_model_search():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "广州全城车型扫描" in response.text
    assert 'id="fish-quick-scan"' in response.text
    assert 'id="model-search-form"' in response.text
    assert 'id="model-search-input"' in response.text
    assert 'id="model-search-selected"' in response.text
    assert 'id="model-search-progress"' in response.text
    assert "找车条件" not in response.text


@pytest.mark.asyncio
async def test_scan_history_exposes_both_task_types():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/scan-runs")

    assert response.status_code == 200
    assert 'data-run-tab="citywide"' in response.text
    assert 'data-run-tab="model-search"' in response.text
    assert 'id="citywide-run-list"' in response.text
    assert 'id="model-search-run-list"' in response.text


@pytest.mark.asyncio
async def test_model_search_result_page_explains_result_confidence():
    run_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get(f"/model-search/{run_id}")

    assert response.status_code == 200
    assert 'id="model-search-results"' in response.text
    assert 'id="model-search-availability"' in response.text
    assert 'id="model-search-period-availability"' in response.text
    assert "车型总体结果" in response.text
    assert "只看可租租期" in response.text
    assert "扫描不完整" in response.text
    assert "同城还车" in response.text


def test_model_search_script_supports_selection_progress_and_period_details():
    script = Path("app/static/citywide.js").read_text(encoding="utf-8")

    assert "/api/model-library/options" in script
    assert "/api/model-search-runs" in script
    assert "/periods?" in script
    assert "modelSearchRunId" in script
