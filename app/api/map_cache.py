from datetime import datetime
from math import ceil
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import func, update

from app.api.dependencies import request_session
from app.integrations.baidu_maps import BaiduMapsError
from app.integrations.map_cache_repository import (
    MapCacheRepository,
    normalize_cache_text,
    normalize_coordinate,
)
from app.integrations.map_service import MapLocationService
from app.integrations.repository import IntegrationSettingsRepository
from app.models import CoordinateCache, MapSearchCache


CacheKind = Literal["search", "coordinate"]


class CacheItemInput(BaseModel):
    kind: CacheKind
    id: int = Field(gt=0)


class ClearCacheInput(BaseModel):
    confirmation: Literal["清空全部地图缓存"]


def _validation_message(request: Request, error: RequestValidationError) -> str:
    path = request.url.path
    locations = [tuple(item.get("loc", ())) for item in error.errors()]
    if path.endswith("/locations/search"):
        if any(location[-1:] in (("region",), ("keyword",)) for location in locations):
            return "城市和地点关键词不能为空或超过 256 个字符"
    if path.endswith("/settings/map-cache/refresh") or path.endswith(
            "/settings/map-cache/item"):
        if any(location[-1:] == ("kind",) for location in locations):
            return "缓存类型只能是地点或坐标"
        if any(location[-1:] == ("id",) for location in locations):
            return "缓存编号必须是大于 0 的整数"
        return "请求内容必须是 JSON 对象"
    if path.endswith("/settings/map-cache") and request.method == "DELETE":
        return "请输入“清空全部地图缓存”确认清理"
    if path.endswith("/settings/map-cache"):
        if any(location[-1:] == ("kind",) for location in locations):
            return "数据类型只能是全部、地点或坐标"
        if any(location[-1:] == ("page",) for location in locations):
            return "页码必须是大于 0 的整数"
        if any(location[-1:] == ("page_size",) for location in locations):
            return "每页数量必须是 1 到 100 的整数"
        return "城市和关键词不能超过 256 个字符"
    return "请求参数不正确"


class ChineseValidationRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def translated_handler(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as error:
                raise HTTPException(422, _validation_message(request, error)) from error

        return translated_handler


router = APIRouter(route_class=ChineseValidationRoute)


class ShortSessionMapCacheRepository:
    """每次仓储操作使用独立短会话，外部网络等待期间不占用数据库连接。"""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def _detach(self, session, item):
        if item is not None:
            session.expunge(item)
        return item

    def _snapshot_and_commit(self, session, item):
        session.flush()
        session.refresh(item)
        session.expunge(item)
        session.commit()
        return item

    def get_search(self, region: str, keyword: str):
        with self.session_factory() as session:
            return self._detach(
                session, MapCacheRepository(session).get_search(region, keyword))

    def hit_search(self, item: MapSearchCache):
        with self.session_factory() as session:
            current = session.get(MapSearchCache, item.id)
            if current is None:
                return item
            current = MapCacheRepository(session).hit_search(current)
            return self._snapshot_and_commit(session, current)

    def save_search(self, region: str, keyword: str, results: list):
        with self.session_factory() as session:
            item = MapCacheRepository(session).save_search(region, keyword, results)
            return self._snapshot_and_commit(session, item)

    def get_coordinate(self, source_crs: str, target_crs: str,
                       source_latitude: float, source_longitude: float):
        with self.session_factory() as session:
            return self._detach(session, MapCacheRepository(session).get_coordinate(
                source_crs, target_crs, source_latitude, source_longitude))

    def hit_coordinate(self, item: CoordinateCache):
        with self.session_factory() as session:
            current = session.get(CoordinateCache, item.id)
            if current is None:
                return item
            current = MapCacheRepository(session).hit_coordinate(current)
            return self._snapshot_and_commit(session, current)

    def save_coordinate(self, source_crs: str, target_crs: str,
                        source_latitude: float, source_longitude: float,
                        target_latitude: float, target_longitude: float):
        with self.session_factory() as session:
            item = MapCacheRepository(session).save_coordinate(
                source_crs, target_crs, source_latitude, source_longitude,
                target_latitude, target_longitude)
            return self._snapshot_and_commit(session, item)


class CacheRefreshConflict(RuntimeError):
    """按 ID 刷新期间原缓存已不存在，禁止通过 upsert 复活。"""


class ProtectedRefreshMapCacheRepository(ShortSessionMapCacheRepository):
    def __init__(self, session_factory, *, search_key=None, coordinate_key=None) -> None:
        super().__init__(session_factory)
        self.search_key = search_key
        self.coordinate_key = coordinate_key

    def save_search(self, region: str, keyword: str, results: list):
        if self.search_key is None:
            raise CacheRefreshConflict
        item_id, provider, normalized_region, normalized_keyword = self.search_key
        if (normalize_cache_text(region), normalize_cache_text(keyword)) != (
                normalized_region, normalized_keyword):
            raise CacheRefreshConflict
        with self.session_factory() as session:
            saved_id = session.scalar(update(MapSearchCache).where(
                MapSearchCache.id == item_id,
                MapSearchCache.provider == provider,
                MapSearchCache.normalized_region == normalized_region,
                MapSearchCache.normalized_keyword == normalized_keyword,
            ).values(
                results_json=results,
                refreshed_at=func.now(),
            ).returning(MapSearchCache.id))
            if saved_id is None:
                raise CacheRefreshConflict
            item = session.get(MapSearchCache, saved_id, populate_existing=True)
            return self._snapshot_and_commit(session, item)

    def save_coordinate(self, source_crs: str, target_crs: str,
                        source_latitude: float, source_longitude: float,
                        target_latitude: float, target_longitude: float):
        if self.coordinate_key is None:
            raise CacheRefreshConflict
        (item_id, provider, expected_source_crs, expected_target_crs,
         expected_latitude, expected_longitude) = self.coordinate_key
        if (source_crs, target_crs, normalize_coordinate(source_latitude),
                normalize_coordinate(source_longitude)) != (
                expected_source_crs, expected_target_crs,
                expected_latitude, expected_longitude):
            raise CacheRefreshConflict
        with self.session_factory() as session:
            saved_id = session.scalar(update(CoordinateCache).where(
                CoordinateCache.id == item_id,
                CoordinateCache.provider == provider,
                CoordinateCache.source_crs == expected_source_crs,
                CoordinateCache.target_crs == expected_target_crs,
                CoordinateCache.source_latitude == expected_latitude,
                CoordinateCache.source_longitude == expected_longitude,
            ).values(
                target_latitude=normalize_coordinate(target_latitude),
                target_longitude=normalize_coordinate(target_longitude),
                refreshed_at=func.now(),
            ).returning(CoordinateCache.id))
            if saved_id is None:
                raise CacheRefreshConflict
            item = session.get(CoordinateCache, saved_id, populate_existing=True)
            return self._snapshot_and_commit(session, item)


def _lazy_baidu_client_factory(request: Request):
    def create_client():
        with request.app.state.session_factory() as session:
            setting = IntegrationSettingsRepository(session).get("baidu_maps")
            if setting is None or not setting.enabled or not setting.secret_value:
                raise HTTPException(422, "百度地图配置尚未启用")
            secret = setting.secret_value
        return request.app.state.baidu_client_factory(secret)

    return create_client


def _location_service(request: Request, repository=None) -> MapLocationService:
    return MapLocationService(
        repository or ShortSessionMapCacheRepository(
            request.app.state.session_factory),
        _lazy_baidu_client_factory(request),
    )


def _safe_baidu_error() -> HTTPException:
    return HTTPException(502, "百度地图请求失败，请稍后重试或检查配置")


def _serialize_search(item: MapSearchCache) -> dict:
    details = item.results_json if isinstance(item.results_json, list) else []
    return {
        "kind": "search",
        "id": item.id,
        "provider": item.provider,
        "region": item.region,
        "keyword": item.keyword,
        "hit_count": item.hit_count,
        "last_hit_at": item.last_hit_at,
        "first_fetched_at": item.first_fetched_at,
        "refreshed_at": item.refreshed_at,
        "details": details,
    }


def _serialize_coordinate(item: CoordinateCache) -> dict:
    return {
        "kind": "coordinate",
        "id": item.id,
        "provider": item.provider,
        "region": None,
        "keyword": f"{item.source_crs} → {item.target_crs}",
        "hit_count": item.hit_count,
        "last_hit_at": item.last_hit_at,
        "first_fetched_at": item.first_fetched_at,
        "refreshed_at": item.refreshed_at,
        "details": {
            "source_crs": item.source_crs,
            "target_crs": item.target_crs,
            "source_latitude": float(item.source_latitude),
            "source_longitude": float(item.source_longitude),
            "target_latitude": float(item.target_latitude),
            "target_longitude": float(item.target_longitude),
        },
    }


@router.get("/locations/search")
async def search_location(
        request: Request,
        region: str = Query(min_length=1, max_length=256),
        keyword: str = Query(min_length=1, max_length=256)):
    if not region.strip() or not keyword.strip():
        raise HTTPException(422, "城市和地点关键词不能为空")
    try:
        return await _location_service(request).search(region, keyword)
    except BaiduMapsError as error:
        raise _safe_baidu_error() from error


@router.get("/settings/map-cache")
def list_map_cache(
        request: Request,
        kind: Literal["all", "search", "coordinate"] = "all",
        region: str = Query(default="", max_length=256),
        keyword: str = Query(default="", max_length=256),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100)):
    with request_session(request) as session:
        repo = MapCacheRepository(session)
        searches = repo.list_searches()
        coordinates = repo.list_coordinates()
        normalized_region = normalize_cache_text(region)
        normalized_keyword = normalize_cache_text(keyword)

        search_items = [_serialize_search(item) for item in searches
                        if (not normalized_region or normalized_region in item.normalized_region)
                        and (not normalized_keyword or normalized_keyword in item.normalized_keyword)]
        coordinate_items = [_serialize_coordinate(item) for item in coordinates
                            if not normalized_region and (
                                not normalized_keyword
                                or normalized_keyword in normalize_cache_text(
                                    f"{item.source_crs} {item.target_crs} "
                                    f"{item.source_latitude} {item.source_longitude}"))]
        if kind == "search":
            items = search_items
        elif kind == "coordinate":
            items = coordinate_items
        else:
            items = search_items + coordinate_items
        items.sort(key=lambda item: (item["refreshed_at"], item["id"]), reverse=True)
        total = len(items)
        pages = ceil(total / page_size) if total else 0
        actual_page = min(page, pages or 1)
        start = (actual_page - 1) * page_size
        latest_values: list[datetime] = [item.refreshed_at for item in searches + coordinates]
        stats = repo.stats()
        return {
            "stats": {
                "search_count": stats["search_count"],
                "coordinate_count": stats["coordinate_count"],
                "total_hit_count": stats["search_hit_count"] + sum(
                    item.hit_count for item in coordinates),
                "latest_refreshed_at": max(latest_values) if latest_values else None,
            },
            "items": items[start:start + page_size],
            "pagination": {
                "page": actual_page,
                "page_size": page_size,
                "total": total,
                "pages": pages,
            },
        }


@router.post("/settings/map-cache/refresh")
async def refresh_map_cache(payload: CacheItemInput, request: Request):
    with request.app.state.session_factory() as session:
        if payload.kind == "search":
            item = session.get(MapSearchCache, payload.id)
            if item is None:
                raise HTTPException(404, "没有找到这条地图缓存")
            search_input = (item.region, item.keyword)
            repository = ProtectedRefreshMapCacheRepository(
                request.app.state.session_factory,
                search_key=(item.id, item.provider, item.normalized_region,
                            item.normalized_keyword),
            )
        else:
            item = session.get(CoordinateCache, payload.id)
            if item is None:
                raise HTTPException(404, "没有找到这条地图缓存")
            coordinate_input = (float(item.source_latitude), float(item.source_longitude))
            search_input = None
            repository = ProtectedRefreshMapCacheRepository(
                request.app.state.session_factory,
                coordinate_key=(item.id, item.provider, item.source_crs,
                                item.target_crs, item.source_latitude,
                                item.source_longitude),
            )
    try:
        service = _location_service(request, repository)
        if search_input is not None:
            return await service.search(*search_input, force_refresh=True)
        return await service.convert(*coordinate_input, force_refresh=True)
    except CacheRefreshConflict as error:
        raise HTTPException(409, "缓存已被删除，刷新结果未保存") from error
    except BaiduMapsError as error:
        raise _safe_baidu_error() from error


@router.delete("/settings/map-cache/item", status_code=204)
def delete_map_cache_item(payload: CacheItemInput, request: Request):
    with request_session(request) as session:
        repo = MapCacheRepository(session)
        model = MapSearchCache if payload.kind == "search" else CoordinateCache
        if session.get(model, payload.id) is None:
            raise HTTPException(404, "没有找到这条地图缓存")
        if payload.kind == "search":
            repo.delete_search(payload.id)
        else:
            repo.delete_coordinate(payload.id)
        return Response(status_code=204)


@router.delete("/settings/map-cache", status_code=204)
def clear_map_cache(payload: ClearCacheInput, request: Request):
    with request_session(request) as session:
        MapCacheRepository(session).clear()
        return Response(status_code=204)
