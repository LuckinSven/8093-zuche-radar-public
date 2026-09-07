import httpx
import pytest
import re
from uuid import uuid4

from app.main import create_app
from app.scanning.service import ScanResult


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "marker"), [
    ("/", 'id="discovery-filters"'),
    ("/models/4666", 'id="personal-state-form"'),
    ("/history", 'id="history-list"'),
    ("/data", 'id="data-table"'),
    ("/admin", 'id="probe-form"'),
    ("/settings", 'id="baidu-settings-form"'),
    ("/settings/map-cache", 'id="map-cache-list"'),
    ("/settings/shenzhou", 'id="shenzhou-catalog-sync"'),
    ("/settings/departments", 'id="department-list"'),
    ("/scan-runs", 'id="citywide-run-list"'),
    ("/settings/api", 'id="api-catalog"'),
    ("/settings/zuche-apis", 'id="zuche-upstream-catalog"'),
    ("/bills", 'id="bill-feature-roadmap"'),
])
async def test_each_primary_page_contains_its_interactive_workspace(path, marker):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert marker in response.text
    assert "非最终结算价" in response.text


@pytest.mark.asyncio
async def test_discovery_page_has_all_time_presets_and_guangzhou_default():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/")
    assert "鱼珠地铁站服务点" in response.text
    assert 'id="location-picker"' in response.text
    assert '<details id="location-picker"' in response.text
    assert 'name="location_name"' not in response.text
    assert 'name="latitude"' not in response.text
    assert 'name="longitude"' not in response.text
    assert 'id="scan-city"' not in response.text
    assert 'id="saved-probe"' not in response.text
    for preset in ("this_saturday", "next_saturday", "this_weekend", "custom"):
        assert f'data-preset="{preset}"' in response.text

    for field in ("department_id", "max_price", "body_style", "energy_type", "bookable", "seat_count"):
        assert f'name="{field}"' in response.text
    assert '<option value="UNTRIED">未租过</option>' in response.text
    assert 'id="more-filters"' in response.text
    assert 'id="fish-model-count"' in response.text
    assert 'id="nearby-model-count"' in response.text
    assert 'id="new-model-count"' in response.text
    assert 'id="latest-scan-summary"' in response.text
    assert 'id="view-data-table"' in response.text
    assert 'id="results"' not in response.text


@pytest.mark.asyncio
async def test_data_page_exposes_spreadsheet_controls_and_export():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        response = await client.get("/data")

    assert response.status_code == 200
    assert 'data-page="data"' in response.text
    assert 'id="data-filter"' in response.text
    assert 'id="data-scan"' in response.text
    assert 'id="data-department"' in response.text
    assert 'name="max_price"' in response.text
    assert 'name="body_style"' in response.text
    assert 'id="data-table"' in response.text
    assert 'id="data-export"' in response.text
    assert 'id="data-rental-period"' in response.text
    assert 'class="data-page-wide"' in response.text
    assert 'id="data-prev"' in response.text
    assert 'id="data-next"' in response.text
    assert "报价明细" in response.text


@pytest.mark.asyncio
async def test_primary_navigation_prioritizes_citywide_models_library_departments_and_settings():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        response = await client.get("/")

    assert '<a href="/">找车</a>' in response.text
    assert '<a href="/scan-runs">扫描记录</a>' in response.text
    assert '<a href="/citywide">全城车型</a>' in response.text
    assert '<a href="/library">车型库</a>' in response.text
    assert '<a href="/settings/departments">网点</a>' in response.text
    assert '<a href="/settings">设置</a>' in response.text
    assert '<a href="/bills">账单</a>' in response.text
    assert '<a href="/history">历史</a>' not in response.text
    assert '<a href="/admin">管理</a>' not in response.text
    assert '<a href="/data">' not in response.text


@pytest.mark.asyncio
async def test_bill_page_is_visible_but_does_not_pretend_upload_is_available():
    """若占位页出现可用上传控件，用户会误以为账单已经能够保存。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        response = await client.get("/bills")

    assert response.status_code == 200
    assert "历史账单统计" in response.text
    assert "功能规划中" in response.text
    assert "上传账单图片" in response.text
    assert "AI 识别" in response.text
    assert "人工确认" in response.text
    assert "累计花费" in response.text
    assert "平均单次费用" in response.text
    assert 'id="bill-upload-placeholder"' in response.text
    assert "暂不可用" in response.text
    assert '<input type="file"' not in response.text


@pytest.mark.asyncio
async def test_manual_scan_exposes_accessible_progress_feedback():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'id="manual-scan-submit"' in response.text
    assert 'id="manual-scan-submit-label"' in response.text
    assert 'id="scan-feedback"' in response.text
    assert 'role="status"' in response.text
    assert 'aria-live="polite"' in response.text
    assert 'id="scan-feedback-title"' in response.text
    assert 'id="scan-feedback-detail"' in response.text


@pytest.mark.asyncio
async def test_model_page_has_source_snapshot_and_price_history_workspaces():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/models/4666")

    assert 'id="source-summary"' in response.text
    assert 'id="offer-list"' in response.text
    assert 'id="price-history"' in response.text
    assert 'name="verified_at"' in response.text
    assert 'id="manual-energy-form"' in response.text


@pytest.mark.asyncio
async def test_admin_page_exposes_probe_edit_controls():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/admin")

    assert 'id="probe-list"' in response.text
    assert 'id="city-list"' in response.text
    assert 'id="city-edit-id"' in response.text
    assert 'id="probe-edit-id"' in response.text
    assert "启用或停用" in response.text
    assert "删除扫描点" in response.text


@pytest.mark.asyncio
async def test_home_form_fields_can_create_manual_scan():
    class Scanner:
        query = None

        async def run(self, query, _trigger):
            self.query = query
            return ScanResult(scan_id=uuid4(), status="SUCCESS", department_count=1, offer_count=2)

    scanner = Scanner()
    app = create_app(scanner=scanner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/")
        response = await client.post("/api/scans", json={"city_id": "14", "location_name": "鱼珠地铁站服务点",
            "latitude": 23.10161, "longitude": 113.432649, "pickup_time": "2026-09-05T09:00:00+08:00",
            "return_time": "2026-09-06T09:00:00+08:00"})

    assert 'data-lat="23.10161"' in page.text
    assert 'data-lon="113.432649"' in page.text
    assert response.status_code == 201
    assert scanner.query.city_id == "14"
    assert scanner.query.location_name == "鱼珠地铁站服务点"


@pytest.mark.asyncio
async def test_settings_page_has_masked_api_configuration_workspace():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        response = await client.get("/settings")

    assert response.status_code == 200
    assert 'data-page="settings"' in response.text
    assert 'id="baidu-settings-form"' in response.text
    assert 'id="test-baidu"' in response.text
    assert 'id="baidu-test-result"' in response.text
    assert 'type="password"' in response.text
    assert 'href="/settings"' in response.text


@pytest.mark.asyncio
async def test_settings_page_submits_baidu_connection_test_with_post():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        response = await client.get("/settings")

    assert ('<form id="baidu-test-form" method="post" '
            'action="/api/settings/integrations/baidu_maps/test"></form>') in response.text
    assert 'id="test-baidu"' in response.text
    assert 'form="baidu-test-form"' in response.text


@pytest.mark.asyncio
async def test_map_cache_is_a_secondary_settings_page_with_safe_controls():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        settings = await client.get("/settings")
        cache = await client.get("/settings/map-cache")

    assert settings.status_code == 200
    assert 'href="/settings/map-cache"' in settings.text
    assert cache.status_code == 200
    assert 'data-page="settings-map-cache"' in cache.text
    assert 'id="map-cache-list"' in cache.text
    assert 'id="map-cache-filter"' in cache.text
    assert 'id="clear-map-cache"' in cache.text
    assert 'href="/settings"' in cache.text
    assert 'href="/settings/map-cache"' in cache.text
    assert "清空后无法恢复" in cache.text
    assert "onclick=" not in cache.text


@pytest.mark.asyncio
async def test_shenzhou_catalog_is_a_secondary_settings_page_without_authentication_controls():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        settings = await client.get("/settings")
        catalog = await client.get("/settings/shenzhou")

    assert 'href="/settings/shenzhou"' in settings.text
    assert catalog.status_code == 200
    assert 'data-page="settings-shenzhou"' in catalog.text
    assert 'id="shenzhou-catalog-test"' in catalog.text
    assert 'id="shenzhou-catalog-sync"' in catalog.text
    assert "神州匿名开放接口" in catalog.text
    assert "不等于全国网点完整" in catalog.text
    assert "cookie" not in catalog.text.lower()
    assert "登录" not in catalog.text


@pytest.mark.asyncio
async def test_department_page_is_a_secondary_settings_workspace_without_completeness_claims():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url="http://test") as client:
        settings = await client.get("/settings")
        cache = await client.get("/settings/map-cache")
        catalog = await client.get("/settings/shenzhou")
        departments = await client.get("/settings/departments")

    assert departments.status_code == 200
    assert 'data-page="settings-departments"' in departments.text
    assert 'id="department-discovery-form"' not in departments.text
    assert 'id="department-filter"' in departments.text
    assert 'id="department-run-list"' not in departments.text
    assert 'id="department-list"' in departments.text
    assert 'id="department-city-sync"' in departments.text
    assert 'id="department-directory-sync"' in departments.text
    assert "同步广州网点目录" in departments.text
    assert "车型与库存仍由找车扫描返回" in departments.text
    assert "已发现网点" in departments.text
    assert "不能保证完整" in departments.text
    assert "发现任务与进度" not in departments.text
    assert "onclick=" not in departments.text
    for page in (settings, cache, catalog, departments):
        assert 'href="/settings/departments"' in page.text
        assert 'href="/settings/shenzhou"' in page.text
    for page in (settings, cache, catalog, departments):
        assert "全部网点" not in page.text


@pytest.mark.asyncio
async def test_api_catalog_lists_every_openapi_operation_and_ai_find_car_guide():
    app = create_app()
    operation_count = sum(
        1 for route, path in app.openapi()["paths"].items()
        if route.startswith("/api/") or route == "/healthz"
        for method in path if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/settings/api")

    assert response.status_code == 200
    assert f'data-endpoint-count="{operation_count}"' in response.text
    assert 'id="ai-api-brief"' in response.text
    assert 'id="copy-ai-api-brief"' in response.text
    assert "POST /api/scans" in response.text
    assert "GET /api/data/rows" in response.text
    assert "GET /api/departments" in response.text
    assert "读取操作" in response.text
    assert "写入操作" in response.text
    assert "创建立即扫描" in response.text
    assert "Create Scan" not in response.text
    assert 'href="/docs"' in response.text
    assert "服务端 AK" not in response.text
    assert "secret_value" not in response.text


@pytest.mark.asyncio
async def test_every_settings_page_links_to_api_catalog():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        pages = [await client.get(path) for path in (
            "/settings", "/settings/map-cache", "/settings/shenzhou",
            "/settings/departments", "/settings/api", "/settings/zuche-apis")]

    assert all(page.status_code == 200 for page in pages)
    assert all('href="/settings/api"' in page.text for page in pages)


@pytest.mark.asyncio
async def test_upstream_catalog_separates_observed_zuche_uris_from_our_api_catalog():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/settings/zuche-apis")

    assert response.status_code == 200
    assert 'id="zuche-upstream-catalog"' in response.text
    assert 'data-endpoint-count="38"' in response.text
    assert "/resource/carrctapi/order/chooseCar/v1" in response.text
    assert "/resource/carrctapi/order/chooseCar/v3" in response.text
    assert "/action/carrctapi/order/deptList/v1" in response.text
    assert "/action/carrctapi/order/cityList/v1" in response.text
    assert "已实际验证" in response.text
    assert "公开前端资源观察" in response.text
    assert "仅发现线索" in response.text
    assert "神州内部全部私有接口" in response.text
    assert "Cookie" not in response.text
    assert 'href="/settings/zuche-apis"' in response.text


def test_map_cache_browser_rendering_escapes_external_fields():
    script = open("app/static/app.js", encoding="utf-8").read()

    assert "function initMapCache" in script
    assert "esc(item.region)" in script
    assert "esc(item.keyword)" in script
    assert "esc(item.name" in script
    assert "esc(item.address" in script
    assert "esc(item.latitude" in script
    assert "esc(item.longitude" in script
    assert "time(item.last_hit_at)" in script
    assert "confirm('确定清空全部地图缓存？清空后无法恢复。')" in script
    assert "prompt('请输入“清空全部地图缓存”以继续：')" in script
    assert "confirmation!=='清空全部地图缓存'" in script
    assert "currentPage=pageData.page" in script


def test_department_browser_renders_operating_details_safely():
    script = open("app/static/app.js", encoding="utf-8").read()

    assert "esc(item.business_hours" in script
    assert "item.is_open_24h" in script
    assert "item.self_service_pickup" in script
    assert "item.self_service_return" in script


def test_home_location_picker_uses_saved_department_directory_without_coordinates_input():
    script = open("app/static/app.js", encoding="utf-8").read()

    assert "'/api/departments?page_size=100'" in script
    assert "location-district" in script
    assert "location-metro" in script
    assert "scan-department" in script
    assert "data-lat" in open("app/templates/discovery.html", encoding="utf-8").read()
