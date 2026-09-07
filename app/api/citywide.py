"""手动全城扫描任务的严格中文 API。"""

import logging
from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import request_session
from app.citywide.catalog import CitywideCatalog, CitywideModelFilters, ModelLibraryFilters
from app.citywide.repository import InvalidCitywideTransition
from app.models import CitywideScanRun, PersonalState


logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "PENDING": "等待中",
    "RUNNING": "扫描中",
    "STOPPED": "已停止",
    "INTERRUPTED": "已中断",
    "COMPLETED": "已完成",
    "PARTIAL": "部分完成",
}


class CreateCitywideScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city_id: Annotated[int, Field(gt=0, strict=True)]
    pickup_time: datetime
    return_time: datetime

    @model_validator(mode="after")
    def validate_period(self) -> "CreateCitywideScanInput":
        for value, label in (
            (self.pickup_time, "取车时间"),
            (self.return_time, "还车时间"),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label}必须包含时区")
        if self.return_time <= self.pickup_time:
            raise ValueError("还车时间必须晚于取车时间")
        if self.return_time - self.pickup_time > timedelta(days=30):
            raise ValueError("租期不能超过 30 天")
        if self.pickup_time.astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("取车时间必须晚于当前时间")
        return self


def _validation_message(error: RequestValidationError) -> str:
    errors = error.errors()
    if any(item.get("type") == "extra_forbidden" for item in errors):
        return "请求内容包含不支持的字段"
    if any(tuple(item.get("loc", ())) [-1:] == ("city_id",) for item in errors):
        return "城市编号必须是大于 0 的整数"
    messages = " ".join(str(item.get("ctx", {}).get("error", "")) for item in errors)
    if "30 天" in messages:
        return "租期不能超过 30 天"
    if "当前时间" in messages:
        return "取车时间必须晚于当前时间"
    if "还车时间" in messages:
        return "还车时间必须晚于取车时间"
    if "时区" in messages:
        return "取车和还车时间必须包含时区"
    return "取车和还车时间格式不正确"


class ChineseCitywideValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def translated(request: Request):
            try:
                return await original(request)
            except RequestValidationError as error:
                raise HTTPException(422, _validation_message(error)) from error

        return translated


router = APIRouter(route_class=ChineseCitywideValidationRoute)


@router.post("/citywide-scans", status_code=201)
def create_citywide_scan(payload: CreateCitywideScanInput, request: Request):
    try:
        run = request.app.state.citywide_scan_service.create_run(
            payload.city_id,
            payload.pickup_time,
            payload.return_time,
        )
        return _serialize_run(run)
    except InvalidCitywideTransition as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        logger.exception("创建全城扫描任务失败")
        raise HTTPException(500, "全城扫描任务创建失败，请稍后重试") from error


@router.get("/citywide-scans")
def list_citywide_scans(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    try:
        with request_session(request) as session:
            total = int(session.scalar(
                select(func.count()).select_from(CitywideScanRun)) or 0)
            pages = max(1, ceil(total / page_size))
            actual_page = min(page, pages)
            rows = session.scalars(select(CitywideScanRun).order_by(
                CitywideScanRun.created_at.desc(),
                CitywideScanRun.id.desc(),
            ).offset((actual_page - 1) * page_size).limit(page_size)).all()
            return {
                "items": [_serialize_run(run) for run in rows],
                "pagination": {
                    "page": actual_page,
                    "page_size": page_size,
                    "total": total,
                    "pages": pages,
                },
            }
    except SQLAlchemyError as error:
        raise HTTPException(500, "全城扫描任务读取失败，请稍后重试") from error


@router.get("/citywide-scans/{run_id}")
def get_citywide_scan(run_id: UUID, request: Request):
    try:
        with request_session(request) as session:
            run = session.get(CitywideScanRun, run_id)
            if run is None:
                raise HTTPException(404, "全城扫描任务不存在")
            return _serialize_run(run)
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise HTTPException(500, "全城扫描任务读取失败，请稍后重试") from error


@router.post("/citywide-scans/{run_id}/stop")
def stop_citywide_scan(run_id: UUID, request: Request):
    return _control(request, "stop", run_id)


@router.post("/citywide-scans/{run_id}/resume")
def resume_citywide_scan(run_id: UUID, request: Request):
    return _control(request, "resume", run_id)


@router.post("/citywide-scans/{run_id}/retry-failed")
def retry_failed_citywide_scan(run_id: UUID, request: Request):
    return _control(request, "retry_failed", run_id)


@router.get("/citywide-models")
def list_citywide_models(
    request: Request,
    run_id: UUID,
    q: str = Query(default="", max_length=100),
    department_id: int | None = Query(default=None, gt=0),
    max_fish_distance_km: float | None = Query(default=None, ge=0),
    availability: Literal["AVAILABLE", "NOT_FOUND", "INCOMPLETE"] | None = None,
    first_seen_from: date | None = None,
    last_seen_from: date | None = None,
    energy_type: str = Query(default="", max_length=64),
    personal_state: PersonalState | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    filters = CitywideModelFilters(
        run_id=run_id,
        q=q,
        department_id=department_id,
        max_fish_distance_km=max_fish_distance_km,
        availability=availability,
        first_seen_from=first_seen_from,
        last_seen_from=last_seen_from,
        energy_type=energy_type,
        personal_state=personal_state,
        page=page,
        page_size=page_size,
    )
    return _catalog_read(request, lambda catalog: catalog.search(filters))


@router.get("/citywide-models/{model_id}/offers")
def list_citywide_model_offers(model_id: int, run_id: UUID, request: Request):
    items = _catalog_read(
        request,
        lambda catalog: catalog.offers(run_id, model_id),
    )
    return {"items": items}


@router.get("/model-library")
def list_model_library(
    request: Request,
    run_id: UUID | None = None,
    q: str = Query(default="", max_length=100),
    energy_type: str = Query(default="", max_length=64),
    personal_state: PersonalState | None = None,
    first_seen_from: date | None = None,
    last_seen_from: date | None = None,
    sort: Literal["recent", "first_seen", "name"] = "recent",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    filters = ModelLibraryFilters(
        run_id=run_id,
        q=q,
        energy_type=energy_type,
        personal_state=personal_state,
        first_seen_from=first_seen_from,
        last_seen_from=last_seen_from,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return _catalog_read(request, lambda catalog: catalog.library(filters))


@router.get("/model-library/{model_id}")
def get_model_library_detail(model_id: int, request: Request, run_id: UUID | None = None):
    return _catalog_read(
        request,
        lambda catalog: catalog.detail(model_id, run_id),
    )


def _control(request: Request, action: str, run_id: UUID):
    try:
        run = getattr(request.app.state.citywide_scan_service, action)(run_id)
        return _serialize_run(run)
    except InvalidCitywideTransition as error:
        message = str(error)
        status_code = 404 if "不存在" in message else 409
        raise HTTPException(status_code, message) from error
    except Exception as error:
        logger.exception("控制全城扫描任务失败")
        raise HTTPException(500, "全城扫描任务操作失败，请稍后重试") from error


def _catalog_read(request: Request, operation):
    try:
        with request_session(request) as session:
            return operation(CitywideCatalog(session))
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(500, "车型数据读取失败，请稍后重试") from error


def _serialize_run(run: CitywideScanRun) -> dict:
    status = str(run.status)
    return {
        "id": str(run.id),
        "city_id": run.city_id,
        "pickup_time": run.pickup_time.isoformat(),
        "return_time": run.return_time.isoformat(),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "planned_point_count": run.planned_point_count,
        "completed_point_count": run.completed_point_count,
        "failed_point_count": run.failed_point_count,
        "request_count": run.request_count,
        "available_model_count": run.available_model_count,
        "new_model_count": run.new_model_count,
        "created_at": _datetime(run.created_at),
        "started_at": _datetime(run.started_at),
        "stopped_at": _datetime(run.stopped_at),
        "completed_at": _datetime(run.completed_at),
        "last_error_summary": run.last_error_summary,
    }


def _datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
