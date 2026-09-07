"""Deterministic, offline planning for department discovery coverage."""

from dataclasses import dataclass
from math import asin, ceil, cos, isfinite, radians, sin, sqrt
from types import MappingProxyType
from typing import Mapping


KM_PER_LATITUDE_DEGREE = 111.32
EARTH_RADIUS_KM = 6_371.0088
COORDINATE_DECIMALS = 6
MAX_ABS_CENTER_LATITUDE = 85.0
MAX_RADIUS_KM = 150.0
MAX_GRID_CANDIDATES = 10_000
RADIUS_TOLERANCE_KM = 1e-9


@dataclass(frozen=True, slots=True)
class DiscoveryPreset:
    radius_km: float
    spacing_km: float
    max_requests: int


DISCOVERY_PRESETS: Mapping[str, DiscoveryPreset] = MappingProxyType({
    "quick": DiscoveryPreset(radius_km=35, spacing_km=10, max_requests=80),
    "standard": DiscoveryPreset(radius_km=70, spacing_km=10, max_requests=180),
    "deep": DiscoveryPreset(radius_km=90, spacing_km=7.5, max_requests=500),
})


def build_grid(
    center_lat: float,
    center_lon: float,
    radius_km: float,
    spacing_km: float,
) -> list[tuple[float, float]]:
    """Build six-decimal coverage points, ordered for repeatable resume behaviour."""
    center_lat = _finite_number(center_lat, "中心纬度")
    center_lon = _finite_number(center_lon, "中心经度")
    radius_km = _finite_number(radius_km, "覆盖半径")
    spacing_km = _finite_number(spacing_km, "网格间距")
    _validate_bounds(center_lat, center_lon, radius_km, spacing_km)

    original_center = (center_lat, center_lon)
    center = (_quantize(center_lat), _quantize(center_lon))
    if haversine_km(original_center, center) > radius_km + RADIUS_TOLERANCE_KM:
        raise ValueError("覆盖半径小于6位坐标精度，无法包含量化中心")

    steps = _grid_steps(radius_km, spacing_km)
    points = {center}
    latitude_step = spacing_km / KM_PER_LATITUDE_DEGREE
    longitude_step = spacing_km / (KM_PER_LATITUDE_DEGREE * cos(radians(center_lat)))

    for latitude_index in range(-steps, steps + 1):
        latitude = center_lat + latitude_index * latitude_step
        if not -90 <= latitude <= 90:
            continue
        for longitude_index in range(-steps, steps + 1):
            if latitude_index == 0 and longitude_index == 0:
                continue
            longitude = _normalize_longitude(center_lon + longitude_index * longitude_step)
            point = (_quantize(latitude), _quantize(longitude))
            if haversine_km(original_center, point) <= radius_km + RADIUS_TOLERANCE_KM:
                points.add(point)

    remaining = sorted(
        points - {center},
        key=lambda point: (_stable_distance(original_center, point), point[0], point[1]),
    )
    return [center, *remaining]


def haversine_km(
    start: tuple[float, float], end: tuple[float, float],
) -> float:
    """Return the great-circle distance for two latitude/longitude pairs."""
    start_lat, start_lon = map(radians, start)
    end_lat, end_lon = map(radians, end)
    latitude_delta = end_lat - start_lat
    longitude_delta = end_lon - start_lon
    haversine = sin(latitude_delta / 2) ** 2 + cos(start_lat) * cos(end_lat) * sin(longitude_delta / 2) ** 2
    return EARTH_RADIUS_KM * 2 * asin(min(1.0, sqrt(haversine)))


def _finite_number(value: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}必须是有限数字") from error
    if not isfinite(number):
        raise ValueError(f"{name}必须是有限数字")
    return number


def _validate_bounds(center_lat: float, center_lon: float, radius_km: float, spacing_km: float) -> None:
    if not -MAX_ABS_CENTER_LATITUDE < center_lat < MAX_ABS_CENTER_LATITUDE:
        raise ValueError("中心纬度必须在 -85 到 85 度之间，不能位于极区")
    if not -180 <= center_lon <= 180:
        raise ValueError("中心经度必须在 -180 到 180 度之间")
    if radius_km <= 0:
        raise ValueError("覆盖半径必须大于 0")
    if radius_km > MAX_RADIUS_KM:
        raise ValueError(f"覆盖半径不能超过 {MAX_RADIUS_KM:g} 公里")
    if spacing_km <= 0:
        raise ValueError("网格间距必须大于 0")


def _grid_steps(radius_km: float, spacing_km: float) -> int:
    ratio = radius_km / spacing_km
    if not isfinite(ratio):
        raise ValueError("网格候选点过多，请增大网格间距")
    steps = ceil(ratio)
    candidate_count = (2 * steps + 1) ** 2
    if candidate_count > MAX_GRID_CANDIDATES:
        raise ValueError("网格候选点过多，请增大网格间距或缩小覆盖半径")
    return steps


def _normalize_longitude(longitude: float) -> float:
    if longitude == 180:
        return 180.0
    return (longitude + 180) % 360 - 180


def _quantize(value: float) -> float:
    return round(value, COORDINATE_DECIMALS)


def _stable_distance(center: tuple[float, float], point: tuple[float, float]) -> float:
    return round(haversine_km(center, point), COORDINATE_DECIMALS)
