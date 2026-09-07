from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.scanner import ScanService
from app.web import build_web_router
from app.api.router import router as api_router
from app.database import SessionFactory
from app.scheduling import RadarRuntime
from app.settings import get_settings
from app.integrations.baidu_maps import BaiduMapsClient
from app.enrichment.client import OpenAICompatibleClient
from app.zuche.client import ZucheClient


def create_app(scanner: ScanService | None = None, session_factory=SessionFactory, runtime_factory=None) -> FastAPI:
    """创建 Web 应用，供生产启动和测试共用。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = runtime_factory(application) if runtime_factory else RadarRuntime(
            session_factory, get_settings().raw_retention_days,
            gateway_factory=application.state.zuche_client_factory,
            enrichment_client_factory=application.state.enrichment_client_factory)
        application.state.runtime = runtime
        application.state.probe_scheduler = runtime.probe_scheduler
        application.state.department_discovery_service = getattr(
            runtime, "department_discovery_service", None)
        application.state.citywide_scan_service = getattr(
            runtime, "citywide_scan_service", None)
        application.state.vehicle_enrichment_service = getattr(
            runtime, "vehicle_enrichment_service", None)
        application.state.model_search_service = getattr(
            runtime, "model_search_service", None)
        runtime.start()
        try:
            yield
        finally:
            runtime.shutdown()

    app = FastAPI(title="神州车型雷达", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.scanner = scanner
    app.state.baidu_client_factory = BaiduMapsClient
    app.state.enrichment_client_factory = OpenAICompatibleClient
    app.state.zuche_client_factory = ZucheClient
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(api_router)
    app.include_router(build_web_router())

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        with app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
        return {"service": "zuche-radar", "status": "ok", "database": "ok"}

    return app


app = create_app()
