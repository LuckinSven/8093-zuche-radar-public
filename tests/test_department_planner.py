import math

import pytest

from app.departments.planner import DISCOVERY_PRESETS, DiscoveryPreset, build_grid, haversine_km


GUANGZHOU_CENTER = (23.1291, 113.2644)
UNQUANTIZED_GUANGZHOU_CENTER = (23.129100499, 113.264400499)


def test_grid_is_deterministic_quantized_and_starts_with_the_center():
    """若中心未首先写入或未量化，恢复任务会改变首个覆盖点。"""
    first = build_grid(*GUANGZHOU_CENTER, radius_km=35, spacing_km=10)
    second = build_grid(*GUANGZHOU_CENTER, radius_km=35, spacing_km=10)

    assert first == second
    assert first[0] == (23.129100, 113.264400)
    assert all(point == (round(point[0], 6), round(point[1], 6)) for point in first)
    assert len(first) == len(set(first))


def test_grid_stays_within_circle_using_haversine_distance():
    """若只以方形边界筛点，半径外的角点会被请求。"""
    radius_km = 35
    points = build_grid(*GUANGZHOU_CENTER, radius_km=radius_km, spacing_km=10)

    assert all(haversine_km(GUANGZHOU_CENTER, point) <= radius_km + 1e-9 for point in points)


def test_grid_orders_remaining_points_by_stable_distance_then_latitude_longitude():
    """若排序直接依赖微小浮点噪声，跨平台恢复顺序会漂移。"""
    points = build_grid(*GUANGZHOU_CENTER, radius_km=12, spacing_km=10)
    remaining = points[1:]
    keys = [
        (round(haversine_km(GUANGZHOU_CENTER, point), 6), point[0], point[1])
        for point in remaining
    ]

    assert keys == sorted(keys)


def test_grid_uses_center_latitude_for_longitude_spacing():
    """若经度未按中心纬度余弦换算，东西覆盖会明显偏离设定间距。"""
    points = build_grid(*GUANGZHOU_CENTER, radius_km=11, spacing_km=10)
    east_or_west = [
        point for point in points
        if point[0] == pytest.approx(GUANGZHOU_CENTER[0], abs=1e-6)
        and point[1] != pytest.approx(GUANGZHOU_CENTER[1], abs=1e-6)
    ]

    assert east_or_west
    assert haversine_km(GUANGZHOU_CENTER, east_or_west[0]) == pytest.approx(10, abs=0.02)


def test_grid_handles_spacing_larger_than_radius_and_a_small_radius():
    """若网格强制至少一个非中心点，会越过细小覆盖圆。"""
    assert build_grid(*GUANGZHOU_CENTER, radius_km=1, spacing_km=10) == [
        (23.129100, 113.264400)
    ]
    assert build_grid(*GUANGZHOU_CENTER, radius_km=0.01, spacing_km=0.1) == [
        (23.129100, 113.264400)
    ]


def test_grid_rejects_a_radius_smaller_than_the_six_decimal_center_error():
    """若量化中心未相对原始中心复核，会返回半径外的第一个点。"""
    with pytest.raises(ValueError, match="半径.*6位"):
        build_grid(*UNQUANTIZED_GUANGZHOU_CENTER, radius_km=0.000001, spacing_km=0.000001)


def test_grid_uses_the_original_center_for_final_radius_checks_and_ordering():
    """若最终校验或排序改回量化中心，细网格会越界或恢复顺序不一致。"""
    radius_km = 0.003
    points = build_grid(*UNQUANTIZED_GUANGZHOU_CENTER, radius_km=radius_km, spacing_km=0.001)

    assert points[0] == (23.129100, 113.264400)
    assert all(haversine_km(UNQUANTIZED_GUANGZHOU_CENTER, point) <= radius_km + 1e-9 for point in points)
    keys = [
        (round(haversine_km(UNQUANTIZED_GUANGZHOU_CENTER, point), 6), point[0], point[1])
        for point in points[1:]
    ]
    assert keys == sorted(keys)


def test_grid_accepts_a_radius_just_larger_than_the_six_decimal_center_error():
    """若把量化误差一律当作非法，小范围的有效覆盖也会被错误拒绝。"""
    points = build_grid(*UNQUANTIZED_GUANGZHOU_CENTER, radius_km=0.00008, spacing_km=0.00008)

    assert points[0] == (23.129100, 113.264400)
    assert all(haversine_km(UNQUANTIZED_GUANGZHOU_CENTER, point) <= 0.00008 + 1e-9 for point in points)


@pytest.mark.parametrize(("field", "value"), [
    ("center_lat", math.nan),
    ("center_lat", math.inf),
    ("center_lat", -89),
    ("center_lon", math.nan),
    ("center_lon", math.inf),
    ("center_lon", 180.1),
    ("radius_km", math.nan),
    ("radius_km", math.inf),
    ("radius_km", 0),
    ("spacing_km", math.nan),
    ("spacing_km", math.inf),
    ("spacing_km", 0),
])
def test_grid_rejects_invalid_inputs_with_a_chinese_value_error(field, value):
    """若非有限或越界参数漏过校验，规划会生成不可恢复的异常点位。"""
    values = {
        "center_lat": GUANGZHOU_CENTER[0],
        "center_lon": GUANGZHOU_CENTER[1],
        "radius_km": 35,
        "spacing_km": 10,
    }
    values[field] = value

    with pytest.raises(ValueError, match="[\u4e00-\u9fff]"):
        build_grid(**values)


def test_grid_rejects_a_request_that_would_create_too_many_candidates():
    """若不先限制候选网格，异常参数可耗尽进程内存。"""
    with pytest.raises(ValueError, match="候选"):
        build_grid(*GUANGZHOU_CENTER, radius_km=90, spacing_km=0.001)


def test_fixed_discovery_presets_are_immutable_and_keep_the_guangzhou_limits():
    """若预设可被调用方篡改，后续任务会绕过已审定的请求上限。"""
    assert DISCOVERY_PRESETS["quick"] == DiscoveryPreset(35, 10, 80)
    assert DISCOVERY_PRESETS["standard"] == DiscoveryPreset(70, 10, 180)
    assert DISCOVERY_PRESETS["deep"] == DiscoveryPreset(90, 7.5, 500)

    with pytest.raises(TypeError):
        DISCOVERY_PRESETS["quick"] = DiscoveryPreset(1, 1, 1)
    with pytest.raises((AttributeError, TypeError)):
        DISCOVERY_PRESETS["quick"].radius_km = 1
