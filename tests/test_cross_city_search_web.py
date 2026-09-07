import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_cross_city_page_exposes_generic_search_workflow_without_personal_defaults():
    """公开页面应保留跨城能力，但不能写死个人车型、日期和还车点。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://test") as client:
        response = await client.get("/cross-city-search")

    assert response.status_code == 200
    assert 'name="zuche_model_id"' in response.text
    assert 'name="return_location_name"' in response.text
    assert "请选择车型编号" in response.text
    assert "请填写计划还车网点" in response.text
    assert 'name="pickup_time" type="datetime-local" required' in response.text
    assert 'name="return_time" type="datetime-local" required' in response.text
    assert 'id="cross-city-form"' in response.text
    assert 'id="cross-city-sync-progress"' in response.text
    assert 'id="cross-city-run-progress"' in response.text
    assert 'id="cross-city-results"' in response.text
    assert "最终仍需在神州下单页确认" in response.text


@pytest.mark.asyncio
async def test_main_navigation_links_to_cross_city_search():
    """专项功能若没有主导航入口，部署后无法自然找到。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'href="/cross-city-search"' in response.text
    assert ">跨城找车<" in response.text
