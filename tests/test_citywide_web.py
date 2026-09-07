from pathlib import Path
import re

import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_citywide_navigation_and_pages_expose_accessible_controls():
    """若核心页面或导航缺失，用户仍会被迫从旧报价表找车型。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        home = await client.get("/")
        citywide = await client.get("/citywide")
        library = await client.get("/library")
        runs = await client.get("/scan-runs")

    assert 'id="citywide-scan-form"' in home.text
    assert 'id="citywide-scan-progress"' in home.text
    assert '<details id="fish-quick-scan"' in home.text
    assert 'id="manual-scan-form"' in home.text
    assert 'id="citywide-model-filters"' in citywide.text
    assert 'id="citywide-model-list"' in citywide.text
    assert 'href="/data"' in citywide.text
    assert 'id="model-library-filters"' in library.text
    assert 'id="model-library-list"' in library.text
    assert runs.status_code == 200
    assert 'id="citywide-run-list"' in runs.text
    assert 'id="citywide-run-pages"' in runs.text
    assert 'href="/scan-runs"' in home.text
    for text in (home.text, citywide.text, library.text, runs.text):
        assert '<a href="/">找车</a>' in text
        assert '<a href="/scan-runs">扫描记录</a>' in text
        assert '<a href="/citywide">全城车型</a>' in text
        assert '<a href="/library">车型库</a>' in text
        assert '<a href="/settings/departments">网点</a>' in text
        assert '<a href="/settings">设置</a>' in text
        assert '<a href="/data">报价明细</a>' not in text


@pytest.mark.asyncio
async def test_model_detail_exposes_citywide_status_history_and_offer_containers():
    """若详情没有全城容器，车型库无法显示租期三态和实际网点价。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/models/4952")

    assert 'id="citywide-model-status"' in response.text
    assert 'id="citywide-offer-list"' in response.text
    assert 'id="citywide-summary-history"' in response.text


@pytest.mark.asyncio
async def test_citywide_results_expose_variant_details_and_no_period_guidance():
    """若全城结果缺少差异详情或无租期提示，同名细节车型仍无法分辨。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        citywide = await client.get("/citywide")

    assert citywide.status_code == 200
    assert 'id="model-variant-detail-template"' in citywide.text
    assert "神州车型 ID" in citywide.text
    assert "能源来源" in citywide.text
    assert "判断置信度" in citywide.text
    assert "能源更新时间" in citywide.text
    assert "原始车型描述" in citywide.text


@pytest.mark.asyncio
async def test_model_library_primary_controls_focus_on_cars_not_rental_periods():
    """若车型库首屏仍选择租期或铺开日期条件，就偏离了浏览车型的目的。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/library")

    assert response.status_code == 200
    assert 'id="model-library-summary"' in response.text
    assert 'name="q"' in response.text
    assert 'name="energy_type"' in response.text
    assert 'name="personal_state"' in response.text
    assert 'name="sort"' in response.text
    assert 'id="model-library-advanced-filters"' in response.text
    assert 'name="run_id"' not in response.text
    assert "参考租期" not in response.text


@pytest.mark.asyncio
async def test_model_library_filter_scrolls_with_page_instead_of_covering_rows():
    """车型库筛选栏若恢复 sticky，滚动后仍会遮挡车型列表。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        stylesheet = await client.get("/static/citywide.css")

    desktop_css = stylesheet.text.split("@media", 1)[0]
    declarations = [
        body for selectors, body in re.findall(r"([^{}]+)\{([^{}]+)\}", desktop_css)
        if "#model-library-filters" in selectors
    ]
    positions = re.findall(r"position\s*:\s*([\w-]+)", ";".join(declarations))
    assert positions[-1] == "static"


@pytest.mark.asyncio
async def test_advanced_data_page_is_named_quote_details():
    """若旧数据表仍占用核心命名，页面层级会继续混乱。"""
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/data")

    assert response.status_code == 200
    assert "报价明细" in response.text


def test_citywide_script_cancels_stale_filters_and_lazy_loads_offers():
    """若筛选请求不能取消或报价预加载，页面会卡顿并浪费数据。"""
    script = Path("app/static/citywide.js").read_text(encoding="utf-8")
    assert "new AbortController()" in script
    assert "controller.abort()" in script
    assert "/offers" in script
    assert "visibilitychange" in script
    assert "offerCache" in script


def test_citywide_script_renders_model_pagination_and_scan_run_counts():
    script = Path("app/static/citywide.js").read_text(encoding="utf-8")

    assert "renderPagination" in script
    assert "上一页" in script
    assert "下一页" in script
    assert "params.set('page'" in script
    assert "/api/citywide-scans?page=" in script
    for field in (
        "available_model_count", "new_model_count", "completed_point_count",
        "failed_point_count", "request_count",
    ):
        assert field in script


@pytest.mark.asyncio
async def test_department_page_keeps_directory_but_hides_discovery_task_workspace():
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/settings/departments")

    assert response.status_code == 200
    assert 'id="department-directory-sync"' in response.text
    assert 'id="department-list"' in response.text
    assert 'id="department-discovery-form"' not in response.text
    assert 'id="department-run-list"' not in response.text
    assert "发现任务与进度" not in response.text


def test_citywide_styles_are_lightweight_responsive_and_reduce_motion_safe():
    css = Path("app/static/citywide.css").read_text(encoding="utf-8")
    theme = Path("app/static/theme-refresh.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@supports (backdrop-filter:" in theme
    assert "min-width:1480px" not in css
    assert "100vh" not in css


def test_model_library_outer_filter_grid_keeps_sections_on_separate_rows():
    """若外层表单继承旧四列网格，高级筛选会覆盖主筛选栏。"""
    css = Path("app/static/citywide.css").read_text(encoding="utf-8")
    rule = re.search(r"\.model-library-filter-panel\{([^}]*)\}", css)
    assert rule is not None
    declarations = dict(
        declaration.split(":", 1)
        for declaration in rule.group(1).split(";")
        if ":" in declaration
    )

    assert declarations.get("grid-template-columns") == "minmax(0,1fr)!important"
