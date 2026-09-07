import inspect
import math
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Callable

from app.repositories.cities import CityRepository
from app.zuche.client import ZucheGatewayError


class CityCatalogError(RuntimeError):
    pass


class CityCatalogService:
    """仅使用匿名 cityList 接口，同步前完整校验快照。"""

    def __init__(self, repository_factory: Callable[[], CityRepository], gateway_factory) -> None:
        self._repository_factory = repository_factory
        self._gateway_factory = gateway_factory

    async def sync(self) -> dict[str, int]:
        cities = await self._load_snapshot()
        repository = self._repository_factory()
        try:
            with repository.session.begin():
                repository.acquire_catalog_sync_lock()
                return repository.apply_catalog_snapshot(cities, datetime.now(UTC))
        finally:
            repository.session.close()

    async def test_connection(self) -> dict[str, int]:
        cities = await self._load_snapshot()
        return {"city_count": len(cities)}

    async def _load_snapshot(self) -> list[dict]:
        try:
            async with _gateway(self._gateway_factory()) as gateway:
                raw_cities = await gateway.list_cities()
        except ZucheGatewayError as error:
            raise CityCatalogError("神州匿名开放接口暂时不可用") from error
        except Exception as error:
            raise CityCatalogError("神州匿名开放接口暂时不可用") from error
        snapshot = _parse_snapshot(raw_cities)
        if not snapshot:
            raise CityCatalogError("神州匿名开放接口未返回可用城市")
        return snapshot


@asynccontextmanager
async def _gateway(client):
    if hasattr(client, "__aenter__"):
        async with client as entered:
            yield entered
        return
    try:
        yield client
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result


def _parse_snapshot(raw_cities: object) -> list[dict]:
    if not isinstance(raw_cities, list):
        raise CityCatalogError("神州匿名开放接口响应格式异常")
    seen: dict[str, dict] = {}
    parsed: list[dict] = []
    for raw_city in raw_cities:
        item = _parse_city(raw_city)
        if item is None:
            raise CityCatalogError("神州匿名开放接口响应格式异常")
        previous = seen.get(item["zuche_city_id"])
        if previous is None:
            seen[item["zuche_city_id"]] = item
            parsed.append(item)
        elif previous != item:
            raise CityCatalogError("神州匿名开放接口响应格式异常")
    return parsed


def _parse_city(raw_city: object) -> dict | None:
    if not isinstance(raw_city, dict):
        return None
    if _has_string_conflict(raw_city, "cityCode", "city_code", "code") or _has_string_conflict(
            raw_city, "enName", "en_name", "englishName"):
        return None
    city_id = _string(raw_city, "cityId", "city_id")
    name = _string(raw_city, "cityName", "city_name", "name")
    latitude_keys = ("latitude", "lat", "cityLat", "cityLatitude")
    longitude_keys = ("longitude", "lon", "lng", "cityLon", "cityLongitude")
    latitude_missing = _all_missing(raw_city, *latitude_keys)
    longitude_missing = _all_missing(raw_city, *longitude_keys)
    latitude = None if latitude_missing else _coordinate(
        raw_city, *latitude_keys, low=-90, high=90)
    longitude = None if longitude_missing else _coordinate(
        raw_city, *longitude_keys, low=-180, high=180)
    if (city_id is None or name is None
            or latitude_missing != longitude_missing
            or (not latitude_missing and (latitude is None or longitude is None))):
        return None
    return {"zuche_city_id": city_id, "name": name, "latitude": latitude, "longitude": longitude,
            "code": _string(raw_city, "cityCode", "city_code", "code"),
            "en_name": _string(raw_city, "enName", "en_name", "englishName")}


def _string(value: dict, *keys: str) -> str | None:
    candidates = [value[key].strip() for key in keys if isinstance(value.get(key), str) and value[key].strip()]
    return candidates[0] if candidates and all(item == candidates[0] for item in candidates) else None


def _has_string_conflict(value: dict, *keys: str) -> bool:
    candidates = [value[key].strip() for key in keys if isinstance(value.get(key), str) and value[key].strip()]
    return bool(candidates) and any(item != candidates[0] for item in candidates)


def _all_missing(value: dict, *keys: str) -> bool:
    return all(value.get(key) is None
               or (isinstance(value.get(key), str) and not value[key].strip())
               for key in keys)


def _coordinate(value: dict, *keys: str, low: float, high: float) -> float | None:
    candidates: list[float] = []
    for key in keys:
        raw = value.get(key)
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number) or not low <= number <= high:
            return None
        candidates.append(number)
    return candidates[0] if candidates and all(item == candidates[0] for item in candidates) else None
