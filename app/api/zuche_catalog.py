from fastapi import APIRouter, HTTPException, Request

from app.api.dependencies import request_session
from app.repositories.cities import CityRepository
from app.zuche.catalog import CityCatalogError, CityCatalogService

router = APIRouter()


def _service(request: Request) -> CityCatalogService:
    def repository_factory() -> CityRepository:
        return CityRepository(request.app.state.session_factory())
    return CityCatalogService(repository_factory, request.app.state.zuche_client_factory)


@router.get("/zuche/cities")
def city_catalog_stats(request: Request):
    with request_session(request) as session:
        return CityRepository(session).stats()


@router.post("/zuche/cities/test")
async def test_city_catalog(request: Request):
    try:
        return await _service(request).test_connection()
    except CityCatalogError as error:
        raise HTTPException(502, str(error)) from error


@router.post("/zuche/cities/sync")
async def sync_city_catalog(request: Request):
    try:
        return await _service(request).sync()
    except CityCatalogError as error:
        raise HTTPException(502, str(error)) from error
