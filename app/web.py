from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api_catalog import build_api_catalog
from app.zuche.upstream_catalog import upstream_catalog

templates = Jinja2Templates(directory="app/templates")


def build_web_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def discovery(request: Request):
        return templates.TemplateResponse(request, "discovery.html", {"title": "神州车型雷达"})

    @router.get("/history", response_class=HTMLResponse)
    async def history(request: Request):
        return templates.TemplateResponse(request, "history.html", {"title": "扫描历史"})

    @router.get("/citywide", response_class=HTMLResponse)
    async def citywide_models(request: Request):
        return templates.TemplateResponse(
            request, "citywide_models.html", {"title": "全城车型"})

    @router.get("/scan-runs", response_class=HTMLResponse)
    async def citywide_scan_runs(request: Request):
        return templates.TemplateResponse(
            request, "scan_runs.html", {"title": "扫描记录"})

    @router.get("/model-search/{run_id}", response_class=HTMLResponse)
    async def model_search_results(request: Request, run_id: str):
        return templates.TemplateResponse(request, "model_search_results.html", {
            "title": "按车型找车结果", "run_id": run_id,
        })

    @router.get("/cross-city-search", response_class=HTMLResponse)
    async def cross_city_search(request: Request):
        return templates.TemplateResponse(request, "cross_city_search.html", {
            "title": "跨城找车",
        })

    @router.get("/library", response_class=HTMLResponse)
    async def model_library(request: Request):
        return templates.TemplateResponse(
            request, "model_library.html", {"title": "车型库"})

    @router.get("/library/ai-runs", response_class=HTMLResponse)
    async def enrichment_runs(request: Request):
        return templates.TemplateResponse(
            request, "enrichment_runs.html", {"title": "AI 补全记录"})

    @router.get("/data", response_class=HTMLResponse)
    async def data_table(request: Request):
        return templates.TemplateResponse(request, "data_table.html", {"title": "报价明细"})

    @router.get("/bills", response_class=HTMLResponse)
    async def bills(request: Request):
        return templates.TemplateResponse(
            request, "bills.html", {"title": "历史账单统计"})

    @router.get("/admin", response_class=HTMLResponse)
    async def admin(request: Request):
        return templates.TemplateResponse(request, "admin.html", {"title": "管理"})

    @router.get("/settings", response_class=HTMLResponse)
    async def settings(request: Request):
        return templates.TemplateResponse(request, "settings.html", {
            "title": "系统设置", "settings_tab": "integrations"})

    @router.get("/settings/ai-enrichment", response_class=HTMLResponse)
    async def settings_ai_enrichment(request: Request):
        return templates.TemplateResponse(request, "settings_ai_enrichment.html", {
            "title": "AI 车型补全", "settings_tab": "ai-enrichment"})

    @router.get("/settings/map-cache", response_class=HTMLResponse)
    async def settings_map_cache(request: Request):
        return templates.TemplateResponse(
            request, "settings_map_cache.html", {"title": "地图缓存", "settings_tab": "map-cache"})

    @router.get("/settings/shenzhou", response_class=HTMLResponse)
    async def settings_shenzhou(request: Request):
        return templates.TemplateResponse(
            request, "settings_shenzhou.html", {"title": "神州开放城市", "settings_tab": "shenzhou"})

    @router.get("/settings/departments", response_class=HTMLResponse)
    async def settings_departments(request: Request):
        return templates.TemplateResponse(
            request, "settings_departments.html", {"title": "已发现网点", "settings_tab": "departments"})

    @router.get("/settings/api", response_class=HTMLResponse)
    async def settings_api(request: Request):
        base_url = str(request.base_url).rstrip("/")
        return templates.TemplateResponse(request, "settings_api.html", {
            "title": "API 接口", "settings_tab": "api", "base_url": base_url,
            "catalog": build_api_catalog(request.app.openapi(), base_url),
        })

    @router.get("/settings/zuche-apis", response_class=HTMLResponse)
    async def settings_zuche_apis(request: Request):
        return templates.TemplateResponse(request, "settings_zuche_apis.html", {
            "title": "神州上游接口", "settings_tab": "zuche-apis",
            "catalog": upstream_catalog(),
        })

    @router.get("/models/{model_id}", response_class=HTMLResponse)
    async def model_detail(request: Request, model_id: int):
        return templates.TemplateResponse(request, "model_detail.html", {"title": "车型详情", "model_id": model_id})
    return router
