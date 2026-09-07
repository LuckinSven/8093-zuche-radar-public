from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.model_search.planner import future_weekend_windows, hourly_windows


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_future_weekend_windows_create_four_weekends_with_two_exact_durations():
    """若周末计算少一天或跨错周，反向找车会查询错误租期。"""
    now = datetime(2026, 9, 2, 12, 0, tzinfo=SHANGHAI)

    windows = future_weekend_windows(now)

    assert len(windows) == 8
    assert windows[0].pickup == datetime(2026, 9, 5, 9, 0, tzinfo=SHANGHAI)
    assert windows[0].return_time == datetime(2026, 9, 6, 9, 0, tzinfo=SHANGHAI)
    assert windows[1].pickup == datetime(2026, 9, 5, 9, 0, tzinfo=SHANGHAI)
    assert windows[1].return_time == datetime(2026, 9, 7, 9, 0, tzinfo=SHANGHAI)
    assert windows[-1].pickup == datetime(2026, 9, 26, 9, 0, tzinfo=SHANGHAI)
    assert windows[-1].return_time == datetime(2026, 9, 28, 9, 0, tzinfo=SHANGHAI)


def test_future_weekend_windows_skip_saturday_after_scan_start_time():
    """周六九点之后启动时，已经开始的租期不能被加入任务。"""
    now = datetime(2026, 9, 5, 9, 1, tzinfo=SHANGHAI)

    windows = future_weekend_windows(now, weekend_count=1)

    assert windows[0].pickup == datetime(2026, 9, 12, 9, 0, tzinfo=SHANGHAI)


def test_hourly_windows_cover_08_to_20_for_twenty_four_and_forty_eight_hours():
    """少采一个整点或还车未同步平移都会产生错误的可租结论。"""
    windows = hourly_windows(date(2026, 9, 5))

    assert len(windows) == 26
    assert {item.pickup.hour for item in windows} == set(range(8, 21))
    assert {item.duration_hours for item in windows} == {24, 48}
    assert windows[0].pickup == datetime(2026, 9, 5, 8, 0, tzinfo=SHANGHAI)
    assert windows[0].return_time == datetime(2026, 9, 6, 8, 0, tzinfo=SHANGHAI)
    assert windows[-1].pickup == datetime(2026, 9, 5, 20, 0, tzinfo=SHANGHAI)
    assert windows[-1].return_time == datetime(2026, 9, 7, 20, 0, tzinfo=SHANGHAI)


@pytest.mark.parametrize("weekend_count", [0, -1])
def test_future_weekend_windows_reject_nonpositive_count(weekend_count):
    with pytest.raises(ValueError, match="周末数量"):
        future_weekend_windows(
            datetime(2026, 9, 2, 12, 0, tzinfo=SHANGHAI),
            weekend_count=weekend_count,
        )
