from collections.abc import Callable

from pydantic import ValidationError

from app.integrations.baidu_maps import (
    BaiduMapsClient,
    BaiduMapsError,
    BaiduPlace,
    Gcj02Coordinate,
)
from app.integrations.map_cache_repository import MapCacheRepository


SOURCE_CRS = "BD-09"
TARGET_CRS = "GCJ-02"


class MapLocationService:
    def __init__(self, repository: MapCacheRepository,
                 client_factory: Callable[[], BaiduMapsClient]) -> None:
        self.repository = repository
        self.client_factory = client_factory

    async def search(self, region: str, keyword: str,
                     force_refresh: bool = False) -> dict:
        cached = self.repository.get_search(region, keyword)
        if cached is not None and not force_refresh:
            cached = self.repository.hit_search(cached)
            return {
                "source": "cache",
                "cached_at": cached.refreshed_at,
                "items": cached.results_json,
            }

        async with self.client_factory() as client:
            places = await client.suggest(region, keyword)
        if not places:
            raise BaiduMapsError("百度地图未找到匹配地点")

        items = [self._serialize_place(place) for place in places]
        saved = self.repository.save_search(region, keyword, items)
        return {
            "source": "baidu",
            "cached_at": saved.refreshed_at,
            "items": items,
        }

    async def convert(self, latitude: float, longitude: float,
                      force_refresh: bool = False) -> dict:
        try:
            source = Gcj02Coordinate(latitude=latitude, longitude=longitude)
        except ValidationError as error:
            raise BaiduMapsError("百度源坐标格式异常") from error
        latitude, longitude = source.latitude, source.longitude

        cached = self.repository.get_coordinate(
            SOURCE_CRS, TARGET_CRS, latitude, longitude)
        if cached is not None and not force_refresh:
            cached = self.repository.hit_coordinate(cached)
            return {
                "source": "cache",
                "cached_at": cached.refreshed_at,
                "latitude": float(cached.target_latitude),
                "longitude": float(cached.target_longitude),
            }

        async with self.client_factory() as client:
            coordinate = await client.convert_bd09_to_gcj02(latitude, longitude)
        if not isinstance(coordinate, Gcj02Coordinate):
            raise BaiduMapsError("百度坐标转换响应格式异常")
        try:
            coordinate = Gcj02Coordinate(
                latitude=coordinate.latitude,
                longitude=coordinate.longitude,
            )
        except ValidationError as error:
            raise BaiduMapsError("百度坐标转换响应格式异常") from error

        saved = self.repository.save_coordinate(
            SOURCE_CRS, TARGET_CRS, latitude, longitude,
            coordinate.latitude, coordinate.longitude,
        )
        return {
            "source": "baidu",
            "cached_at": saved.refreshed_at,
            "latitude": float(saved.target_latitude),
            "longitude": float(saved.target_longitude),
        }

    @staticmethod
    def _serialize_place(place: BaiduPlace) -> dict:
        if not isinstance(place, BaiduPlace):
            raise BaiduMapsError("百度地点提示响应格式异常")
        try:
            place = BaiduPlace.model_validate(place.model_dump())
        except ValidationError as error:
            raise BaiduMapsError("百度地点提示响应格式异常") from error
        return place.model_dump(mode="json", exclude_none=True)
