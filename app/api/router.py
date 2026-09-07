from fastapi import APIRouter

from app.api import admin, citywide, data_table, departments, discovery, enrichment, map_cache, model_search, personal, scans, settings, zuche_catalog, zuche_upstream

router = APIRouter(prefix="/api")
router.include_router(scans.router)
router.include_router(discovery.router)
router.include_router(admin.router)
router.include_router(personal.router)
router.include_router(settings.router)
router.include_router(map_cache.router)
router.include_router(zuche_catalog.router)
router.include_router(zuche_upstream.router)
router.include_router(departments.router)
router.include_router(data_table.router)
router.include_router(model_search.router)
router.include_router(citywide.router)
router.include_router(enrichment.router)
