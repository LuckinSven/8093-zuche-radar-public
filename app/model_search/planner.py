"""生成广州未来周末的基础租期与整点精扫租期。"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.model_search.domain import RentalWindow


SHANGHAI = ZoneInfo("Asia/Shanghai")


def future_weekend_windows(
    now: datetime,
    weekend_count: int = 4,
) -> tuple[RentalWindow, ...]:
    if weekend_count <= 0:
        raise ValueError("周末数量必须大于 0")
    local_now = now.astimezone(SHANGHAI) if now.tzinfo else now.replace(tzinfo=SHANGHAI)
    days_until_saturday = (5 - local_now.weekday()) % 7
    first_saturday = local_now.date() + timedelta(days=days_until_saturday)
    first_pickup = datetime.combine(first_saturday, time(9), SHANGHAI)
    if first_pickup <= local_now:
        first_saturday += timedelta(days=7)

    windows: list[RentalWindow] = []
    for offset in range(weekend_count):
        saturday = first_saturday + timedelta(days=offset * 7)
        pickup = datetime.combine(saturday, time(9), SHANGHAI)
        windows.extend((
            RentalWindow(pickup, pickup + timedelta(hours=24)),
            RentalWindow(pickup, pickup + timedelta(hours=48)),
        ))
    return tuple(windows)

def hourly_windows(saturday: date) -> tuple[RentalWindow, ...]:
    windows: list[RentalWindow] = []
    for hour in range(8, 21):
        pickup = datetime.combine(saturday, time(hour), SHANGHAI)
        windows.extend((
            RentalWindow(pickup, pickup + timedelta(hours=24)),
            RentalWindow(pickup, pickup + timedelta(hours=48)),
        ))
    return tuple(windows)
