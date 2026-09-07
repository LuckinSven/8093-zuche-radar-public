"""使用匿名网点目录接口同步单个城市的完整网点快照。"""

import inspect
import math
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError

from app.models import City, DepartmentActivityState
from app.repositories.departments import DepartmentRepository
from app.zuche.client import ZucheGatewayError


class DepartmentDirectoryError(RuntimeError):
    pass


class DepartmentDirectoryCityNotFound(ValueError):
    pass


class DepartmentDirectoryService:
    """外呼期间不持有数据库会话；同步只增改，不删除历史网点。"""

    def __init__(self, session_factory, gateway_factory) -> None:
        self._session_factory = session_factory
        self._gateway_factory = gateway_factory

    async def sync(self, city_id: int) -> dict:
        with self._session_factory() as session:
            city = session.get(City, city_id)
            if city is None:
                raise DepartmentDirectoryCityNotFound("城市不存在")
            city_snapshot = (city.id, city.zuche_city_id, city.name)

        try:
            async with _gateway(self._gateway_factory()) as gateway:
                raw_departments = await gateway.list_departments(city_snapshot[1])
        except ZucheGatewayError as error:
            raise DepartmentDirectoryError("神州匿名网点目录暂时不可用") from error
        except Exception as error:
            raise DepartmentDirectoryError("神州匿名网点目录暂时不可用") from error

        departments = _parse_snapshot(raw_departments)
        try:
            with self._session_factory() as session:
                repository = DepartmentRepository(session)
                department_ids = {item["deptId"] for item in departments}
                existing_ids = repository.existing_zuche_ids(department_ids)
                for department in departments:
                    repository.upsert(
                        city_snapshot[0],
                        department,
                        source="DIRECTORY",
                        active_state=DepartmentActivityState.DISCOVERED,
                    )
                session.commit()
        except SQLAlchemyError as error:
            raise DepartmentDirectoryError("网点目录保存失败，请稍后重试") from error
        return {
            "city_id": city_snapshot[0],
            "city_name": city_snapshot[2],
            "department_count": len(departments),
            "new_count": len(department_ids - existing_ids),
            "updated_count": len(department_ids & existing_ids),
        }


def _parse_snapshot(raw_departments: object) -> list[dict]:
    if not isinstance(raw_departments, list):
        raise DepartmentDirectoryError("神州匿名网点目录响应格式异常")
    seen: dict[int, dict] = {}
    for raw in raw_departments:
        item = _parse_department(raw)
        if item is None:
            raise DepartmentDirectoryError("神州匿名网点目录响应格式异常")
        previous = seen.get(item["deptId"])
        if previous is None:
            seen[item["deptId"]] = item
        elif previous != item:
            raise DepartmentDirectoryError("神州匿名网点目录响应格式异常")
    return list(seen.values())


def _parse_department(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    department_id = _positive_int(raw.get("deptId"))
    name = _text(raw.get("deptName"))
    latitude = _coordinate(raw.get("deptLat"), -90, 90)
    longitude = _coordinate(raw.get("deptLon"), -180, 180)
    if department_id is None or name is None or latitude is None or longitude is None:
        return None
    whole_day = _optional_bool(raw.get("wholeDayFlag"))
    if whole_day is None:
        whole_day = _optional_bool(raw.get("allDayFlag"))
    self_service = _optional_bool(raw.get("selfServiceFlag"))
    return {
        "deptId": department_id,
        "deptName": name,
        "deptAddress": _text(raw.get("deptAddress")),
        "lat": latitude,
        "lon": longitude,
        "districtName": _text(raw.get("districtName")),
        "businessHours": _text(raw.get("workTime")),
        "is24Hour": whole_day,
        "selfServicePickup": self_service,
        "selfServiceReturn": self_service,
    }


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _coordinate(value: object, low: float, high: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return None


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
