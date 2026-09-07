"""已发现神州网点和渐进发现任务的固定 API。"""

from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import request_session
from app.models import (
    City,
    Department,
    DepartmentActivityState,
    DepartmentDiscoveryPoint,
    DepartmentDiscoveryPointStatus,
    DepartmentDiscoveryRun,
    DepartmentDiscoveryStatus,
)
from app.repositories.departments import InvalidDiscoveryTransition
from app.zuche.departments import (
    DepartmentDirectoryCityNotFound,
    DepartmentDirectoryError,
    DepartmentDirectoryService,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
DiscoveryPresetName = Literal["quick", "standard", "deep"]
DEEP_DISCOVERY_CONFIRMATION = "确认启动深度发现"

ACTIVITY_LABELS = {
    "DISCOVERED": "已发现",
    "RECENTLY_SEEN": "近期出现",
    "LONG_UNSEEN": "长期未见",
    "MANUALLY_DISABLED": "人工停用",
}
RUN_STATUS_LABELS = {
    "PENDING": "等待中",
    "RUNNING": "运行中",
    "STOPPED": "已停止",
    "INTERRUPTED": "已中断",
    "COMPLETED_LIMIT": "已达请求上限",
    "COMPLETED_NO_NEW": "已完成",
    "FAILED": "失败",
}
PRESET_LABELS = {"quick": "快速", "standard": "标准", "deep": "深度"}
SOURCE_LABELS = {
    "CHOOSE_CAR": "选车接口",
    "DIRECTORY": "网点目录",
    "MANUAL": "人工补充",
}


class CreateDiscoveryRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city_id: Annotated[int, Field(gt=0, strict=True)]
    preset: DiscoveryPresetName = "quick"
    pickup_date: date | None = None
    return_date: date | None = None
    confirmation: Literal["确认启动深度发现"] | None = None


class DiscoveryRunControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID


class SyncDepartmentDirectoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city_id: Annotated[int, Field(gt=0, strict=True)]


def _validation_message(request: Request, error: RequestValidationError) -> str:
    errors = error.errors()
    locations = [tuple(item.get("loc", ())) for item in errors]
    if any(item.get("type") == "extra_forbidden" for item in errors):
        return "请求内容包含不支持的字段"
    if request.url.path.endswith(("/stop", "/resume")):
        return "发现任务编号必须是有效 UUID"
    if request.url.path.endswith("/discovery-runs"):
        if request.method == "GET":
            if any(location[-1:] == ("run_id",) for location in locations):
                return "发现任务编号必须是有效 UUID"
            if any(location[-1:] == ("city_id",) for location in locations):
                return "城市编号必须是大于 0 的整数"
            if any(location[-1:] == ("page",) for location in locations):
                return "页码必须是大于 0 的整数"
            if any(location[-1:] == ("page_size",) for location in locations):
                return "每页数量必须是 1 到 100 的整数"
        if any(location[-1:] == ("city_id",) for location in locations):
            return "城市编号必须是大于 0 的整数"
        if any(location[-1:] == ("preset",) for location in locations):
            return "发现预设只能是快速、标准或深度"
        if any(location[-1:] == ("confirmation",) for location in locations):
            return "深度发现必须提交“确认启动深度发现”"
        if any(location[-1:] in (("pickup_date",), ("return_date",))
               for location in locations):
            return "取车和还车日期格式必须为 YYYY-MM-DD"
        return "请求内容必须是 JSON 对象"
    if request.url.path.endswith("/summary"):
        return "城市编号必须是大于 0 的整数"
    if any(location[-1:] == ("status",) for location in locations):
        return "网点状态不正确"
    if any(location[-1:] == ("city_id",) for location in locations):
        return "城市编号必须是大于 0 的整数"
    if any(location[-1:] == ("page",) for location in locations):
        return "页码必须是大于 0 的整数"
    if any(location[-1:] == ("page_size",) for location in locations):
        return "每页数量必须是 1 到 100 的整数"
    return "网点筛选参数不正确"


class ChineseValidationRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def translated_handler(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=422,
                    detail=_validation_message(request, error),
                ) from error

        return translated_handler


router = APIRouter(route_class=ChineseValidationRoute)


@router.get("/departments")
def list_departments(
        request: Request,
        city_id: int | None = Query(default=None, gt=0),
        district: str = Query(default="", max_length=128),
        keyword: str = Query(default="", max_length=256),
        status: DepartmentActivityState | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100)):
    filters = _department_filters(city_id, district, keyword, status)
    try:
        with request_session(request) as session:
            total = int(session.scalar(
                select(func.count()).select_from(Department).where(*filters)) or 0)
            actual_page, pages = _actual_page(page, page_size, total)
            items = session.scalars(select(Department).where(*filters).order_by(
                Department.last_seen_at.desc(), Department.id.desc()).offset(
                    (actual_page - 1) * page_size).limit(page_size)).all()
            return {
                "items": [_serialize_department(item) for item in items],
                "pagination": _pagination(actual_page, page_size, total, pages),
            }
    except SQLAlchemyError as error:
        raise HTTPException(500, "网点数据读取失败，请稍后重试") from error


@router.get("/departments/summary")
def department_summary(
        request: Request,
        city_id: int | None = Query(default=None, gt=0)):
    filters = [Department.city_id == city_id] if city_id is not None else []
    coordinate_filter = (
        Department.latitude.is_not(None) & Department.longitude.is_not(None))
    try:
        with request_session(request) as session:
            discovered_count, coordinate_count, latest_seen_at = session.execute(select(
                func.count(Department.id),
                func.count(Department.id).filter(coordinate_filter),
                func.max(Department.last_seen_at),
            ).where(*filters)).one()
            district_rows = session.execute(select(
                Department.district, func.count(Department.id).label("department_count"),
            ).where(
                *filters,
                Department.district.is_not(None),
                Department.district != "",
            ).group_by(Department.district).order_by(
                func.count(Department.id).desc(), Department.district.asc())).all()
            return {
                "discovered_count": int(discovered_count or 0),
                "with_coordinates_count": int(coordinate_count or 0),
                "districts": [
                    {"district": district, "count": int(count)}
                    for district, count in district_rows
                ],
                "latest_seen_at": _datetime(latest_seen_at),
            }
    except SQLAlchemyError as error:
        raise HTTPException(500, "网点统计读取失败，请稍后重试") from error


@router.post("/zuche/departments/sync")
async def sync_department_directory(payload: SyncDepartmentDirectoryInput, request: Request):
    service = DepartmentDirectoryService(
        request.app.state.session_factory,
        request.app.state.zuche_client_factory,
    )
    try:
        return await service.sync(payload.city_id)
    except DepartmentDirectoryCityNotFound as error:
        raise HTTPException(422, "城市不存在") from error
    except DepartmentDirectoryError as error:
        raise HTTPException(502, str(error)) from error


@router.get("/departments/discovery-runs")
def list_discovery_runs(
        request: Request,
        run_id: UUID | None = None,
        city_id: int | None = Query(default=None, gt=0),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100)):
    filters = []
    if run_id is not None:
        filters.append(DepartmentDiscoveryRun.id == run_id)
    if city_id is not None:
        filters.append(DepartmentDiscoveryRun.city_id == city_id)
    try:
        with request_session(request) as session:
            total = int(session.scalar(select(func.count()).select_from(
                DepartmentDiscoveryRun).where(*filters)) or 0)
            if run_id is not None and total == 0:
                raise HTTPException(404, "发现任务不存在")
            actual_page, pages = _actual_page(page, page_size, total)
            rows = session.execute(select(
                DepartmentDiscoveryRun, City.name,
            ).join(City, City.id == DepartmentDiscoveryRun.city_id).where(
                *filters).order_by(
                    DepartmentDiscoveryRun.created_at.desc(),
                    DepartmentDiscoveryRun.id.desc(),
                ).offset((actual_page - 1) * page_size).limit(page_size)).all()
            run_ids = [run.id for run, _city_name in rows]
            rounds = _round_progress(session, run_ids)
            items = [
                _serialize_run(run, city_name=city_name, rounds=rounds.get(run.id, []))
                for run, city_name in rows
            ]
            if run_id is not None:
                return {"item": items[0]}
            return {
                "items": items,
                "pagination": _pagination(actual_page, page_size, total, pages),
            }
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise HTTPException(500, "发现任务读取失败，请稍后重试") from error


@router.post("/departments/discovery-runs", status_code=201)
def create_discovery_run(payload: CreateDiscoveryRunInput, request: Request):
    if (payload.preset == "deep"
            and payload.confirmation != DEEP_DISCOVERY_CONFIRMATION):
        raise HTTPException(422, "深度发现必须提交“确认启动深度发现”")
    pickup_time, return_time = _discovery_period(
        payload.pickup_date, payload.return_date)
    try:
        with request_session(request) as session:
            city = session.get(City, payload.city_id)
            if city is None:
                raise HTTPException(422, "城市不存在")
            if not city.enabled:
                raise HTTPException(422, "城市尚未启用")
            if not city.catalog_active:
                raise HTTPException(422, "城市当前不在开放目录")
            if city.latitude is None or city.longitude is None:
                raise HTTPException(422, "城市缺少中心坐标，请先补充")
            session.expunge(city)
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise HTTPException(500, "城市数据读取失败，请稍后重试") from error

    try:
        run = _service(request).create_run(
            city,
            preset=payload.preset,
            pickup_time=pickup_time,
            return_time=return_time,
        )
        return _serialize_run(run)
    except InvalidDiscoveryTransition as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, "发现任务参数不正确") from error
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "发现任务创建失败，请稍后重试") from None


@router.post("/departments/discovery-runs/stop")
def stop_discovery_run(payload: DiscoveryRunControlInput, request: Request):
    _require_transition_state(request, payload.run_id, operation="stop")
    return _transition(request, payload.run_id, operation="stop")


@router.post("/departments/discovery-runs/resume")
def resume_discovery_run(payload: DiscoveryRunControlInput, request: Request):
    _require_transition_state(request, payload.run_id, operation="resume")
    return _transition(request, payload.run_id, operation="resume")


def _service(request: Request):
    service = getattr(request.app.state, "department_discovery_service", None)
    if service is None:
        raise HTTPException(503, "网点发现服务暂不可用")
    return service


def _transition(request: Request, run_id: UUID, *, operation: str):
    try:
        service = _service(request)
        run = service.stop(run_id) if operation == "stop" else service.resume(run_id)
        return _serialize_run(run)
    except InvalidDiscoveryTransition as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(404, "发现任务不存在") from error
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "发现任务操作失败，请稍后重试") from None


def _require_transition_state(
        request: Request, run_id: UUID, *, operation: str) -> None:
    try:
        with request_session(request) as session:
            status = session.scalar(select(DepartmentDiscoveryRun.status).where(
                DepartmentDiscoveryRun.id == run_id))
    except SQLAlchemyError as error:
        raise HTTPException(500, "发现任务读取失败，请稍后重试") from error
    if status is None:
        raise HTTPException(404, "发现任务不存在")
    status_value = _enum_value(status)
    if operation == "stop" and status_value != "RUNNING":
        raise HTTPException(409, "只有运行中的发现任务可以停止")
    if operation == "resume" and status_value not in {"STOPPED", "INTERRUPTED"}:
        raise HTTPException(409, "只有已停止或已中断的发现任务可以继续")


def _discovery_period(
        pickup_date: date | None, return_date: date | None) -> tuple[datetime, datetime]:
    today = datetime.now(SHANGHAI).date()
    if pickup_date is None and return_date is None:
        pickup_date = today + timedelta(days=1)
        return_date = pickup_date + timedelta(days=1)
    elif pickup_date is None or return_date is None:
        raise HTTPException(422, "取车和还车日期必须同时提供")
    if pickup_date < today:
        raise HTTPException(422, "取车日期不能早于今天")
    if return_date <= pickup_date:
        raise HTTPException(422, "还车日期必须晚于取车日期")
    if return_date - pickup_date > timedelta(days=30):
        raise HTTPException(422, "租期最多为 30 天")
    return (
        datetime.combine(pickup_date, time(hour=9), SHANGHAI),
        datetime.combine(return_date, time(hour=9), SHANGHAI),
    )


def _department_filters(city_id, district, keyword, status):
    filters = []
    if city_id is not None:
        filters.append(Department.city_id == city_id)
    if district.strip():
        filters.append(Department.district == district.strip())
    if keyword.strip():
        pattern = f"%{keyword.strip()}%"
        filters.append(or_(
            Department.name.ilike(pattern),
            Department.address.ilike(pattern),
            Department.district.ilike(pattern),
        ))
    if status is not None:
        filters.append(Department.active_state == status)
    return filters


def _round_progress(session, run_ids):
    if not run_ids:
        return {}
    completed = DepartmentDiscoveryPoint.status.in_((
        DepartmentDiscoveryPointStatus.COMPLETED,
        DepartmentDiscoveryPointStatus.FAILED,
    ))
    rows = session.execute(select(
        DepartmentDiscoveryPoint.run_id,
        DepartmentDiscoveryPoint.round_number,
        func.count(DepartmentDiscoveryPoint.id),
        func.count(DepartmentDiscoveryPoint.id).filter(completed),
        func.coalesce(func.sum(DepartmentDiscoveryPoint.new_department_count), 0),
    ).where(DepartmentDiscoveryPoint.run_id.in_(run_ids)).group_by(
        DepartmentDiscoveryPoint.run_id,
        DepartmentDiscoveryPoint.round_number,
    ).order_by(
        DepartmentDiscoveryPoint.run_id,
        DepartmentDiscoveryPoint.round_number,
    )).all()
    result = {}
    for run_id, round_number, planned, complete_count, new_count in rows:
        result.setdefault(run_id, []).append({
            "round_number": round_number,
            "planned_point_count": int(planned),
            "completed_point_count": int(complete_count),
            "new_department_count": int(new_count),
        })
    return result


def _serialize_department(item: Department) -> dict:
    state = _enum_value(item.active_state)
    source = item.discovery_source
    return {
        "id": item.id,
        "zuche_dept_id": item.zuche_dept_id,
        "city_id": item.city_id,
        "name": item.name,
        "address": item.address,
        "district": item.district,
        "latitude": _float(item.latitude),
        "longitude": _float(item.longitude),
        "business_hours": item.business_hours,
        "is_open_24h": item.is_open_24h,
        "self_service_pickup": item.self_service_pickup,
        "self_service_return": item.self_service_return,
        "first_seen_at": _datetime(item.first_seen_at),
        "last_seen_at": _datetime(item.last_seen_at),
        "last_synced_at": _datetime(item.last_synced_at),
        "discovery_source": source,
        "discovery_source_label": SOURCE_LABELS.get(source, "其他来源"),
        "active_state": state,
        "active_state_label": ACTIVITY_LABELS.get(state, "状态未知"),
    }


def _serialize_run(run: DepartmentDiscoveryRun, *, city_name=None, rounds=None) -> dict:
    status = _enum_value(run.status)
    return {
        "id": str(run.id),
        "city_id": run.city_id,
        "city_name": city_name,
        "preset": run.preset,
        "preset_label": PRESET_LABELS.get(run.preset, "未知预设"),
        "radius_km": _float(run.radius_km),
        "spacing_km": _float(run.spacing_km),
        "max_requests": run.max_requests,
        "pickup_time": _datetime(run.pickup_time),
        "return_time": _datetime(run.return_time),
        "status": status,
        "status_label": RUN_STATUS_LABELS.get(status, "状态未知"),
        "planned_point_count": run.planned_point_count,
        "completed_point_count": run.completed_point_count,
        "request_count": run.request_count,
        "new_department_count": run.new_department_count,
        "updated_department_count": run.updated_department_count,
        "created_at": _datetime(run.created_at),
        "started_at": _datetime(run.started_at),
        "completed_at": _datetime(run.completed_at),
        "last_error_summary": run.last_error_summary,
        "rounds": rounds or [],
    }


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _float(value) -> float | None:
    return float(value) if value is not None else None


def _actual_page(page: int, page_size: int, total: int) -> tuple[int, int]:
    pages = ceil(total / page_size) if total else 0
    return min(page, pages or 1), pages


def _pagination(page: int, page_size: int, total: int, pages: int) -> dict:
    return {"page": page, "page_size": page_size, "total": total, "pages": pages}
