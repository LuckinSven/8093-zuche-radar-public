"""广州按车型找车的严格中文 API。"""

import logging
from datetime import datetime, timedelta
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
from app.model_search.catalog import ModelSearchCatalog
from app.model_search.domain import RentalWindow
from app.model_search.repository import InvalidModelSearchTransition
from app.models import City, ModelSearchRun, ModelSearchTarget, VehicleModel


logger = logging.getLogger(__name__)
STATUS_LABELS = {
    "PENDING": "等待中",
    "RUNNING": "扫描中",
    "STOPPED": "已停止",
    "INTERRUPTED": "已中断",
    "COMPLETED": "已完成",
    "PARTIAL": "部分完成",
    "BUDGET_EXCEEDED": "请求量超出安全上限",
}
PHASE_LABELS = {"BASE": "全城基础扫描", "FINE": "候选网点小时精扫"}


class CreateModelSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_names: Annotated[list[str], Field(min_length=1, max_length=20)]


class CrossCityWindowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pickup_time: datetime
    return_time: datetime

    @model_validator(mode="after")
    def validate_exact_duration(self) -> "CrossCityWindowInput":
        if self.pickup_time.tzinfo is None or self.return_time.tzinfo is None:
            raise ValueError("取车和还车时间必须包含时区")
        if self.return_time - self.pickup_time != timedelta(days=14):
            raise ValueError("每组租期必须严格为 14 天")
        return self


class CreateCrossCitySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zuche_model_id: Annotated[int, Field(gt=0, strict=True)]
    pickup_city_ids: Annotated[list[int], Field(min_length=1, max_length=30)]
    return_city_id: Annotated[int, Field(gt=0, strict=True)]
    return_location_name: Annotated[str, Field(min_length=1, max_length=255)]
    windows: Annotated[list[CrossCityWindowInput], Field(min_length=1, max_length=4)]
    rail_costs: dict[str, Annotated[float, Field(ge=0, le=10000)]] = Field(
        default_factory=dict)


class ChineseModelSearchRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def translated(request: Request):
            try:
                return await original(request)
            except RequestValidationError as error:
                messages = " ".join(
                    str(item.get("ctx", {}).get("error", ""))
                    for item in error.errors()
                )
                if "14 天" in messages:
                    message = "每组租期必须严格为 14 天"
                elif "时区" in messages:
                    message = "取车和还车时间必须包含时区"
                elif any(item.get("type") == "extra_forbidden" for item in error.errors()):
                    message = "请求内容包含不支持的字段"
                else:
                    message = "请从车型库选择一至二十款车型"
                raise HTTPException(422, message) from error

        return translated


router = APIRouter(route_class=ChineseModelSearchRoute)


@router.post("/cross-city-search-runs", status_code=201)
def create_cross_city_search(payload: CreateCrossCitySearchInput, request: Request):
    try:
        run = request.app.state.model_search_service.create_cross_city_run(
            zuche_model_id=payload.zuche_model_id,
            pickup_city_ids=payload.pickup_city_ids,
            return_city_id=payload.return_city_id,
            return_location_name=payload.return_location_name,
            windows=tuple(RentalWindow(item.pickup_time, item.return_time)
                          for item in payload.windows),
            rail_costs=payload.rail_costs,
        )
        return _serialize_run(run)
    except InvalidModelSearchTransition as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        logger.exception("创建跨城找车任务失败")
        raise HTTPException(500, "跨城找车任务创建失败，请稍后重试") from error


@router.get("/cross-city-search-runs")
def list_cross_city_searches(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    try:
        with request_session(request) as session:
            condition = ModelSearchRun.search_kind == "CROSS_CITY"
            total = int(session.scalar(select(func.count()).select_from(
                ModelSearchRun).where(condition)) or 0)
            pages = max(1, ceil(total / page_size))
            actual_page = min(page, pages)
            rows = list(session.scalars(select(ModelSearchRun).where(
                condition).order_by(
                    ModelSearchRun.created_at.desc(), ModelSearchRun.id.desc(),
                ).offset((actual_page - 1) * page_size).limit(page_size)))
            return {
                "items": [_serialize_run(run) for run in rows],
                "pagination": {"page": actual_page, "page_size": page_size,
                               "total": total, "pages": pages},
            }
    except SQLAlchemyError as error:
        raise HTTPException(500, "跨城找车任务读取失败，请稍后重试") from error


@router.get("/cross-city-search-runs/{run_id}/results")
def get_cross_city_search_results(run_id: UUID, request: Request):
    return _catalog(request, lambda item: item.cross_city_results(run_id))


@router.post("/cross-city-search-runs/{run_id}/stop")
def stop_cross_city_search(run_id: UUID, request: Request):
    return _control(request, "stop", run_id)


@router.post("/cross-city-search-runs/{run_id}/resume")
def resume_cross_city_search(run_id: UUID, request: Request):
    return _control(request, "resume", run_id)


@router.get("/cross-city-search-runs/{run_id}")
def get_cross_city_search(run_id: UUID, request: Request):
    try:
        with request_session(request) as session:
            run = session.get(ModelSearchRun, run_id)
            if run is None or run.search_kind != "CROSS_CITY":
                raise HTTPException(404, "跨城找车任务不存在")
            target = session.scalar(select(VehicleModel).join(
                ModelSearchTarget,
                ModelSearchTarget.vehicle_model_id == VehicleModel.id,
            ).where(ModelSearchTarget.run_id == run.id).limit(1))
            cities = list(session.scalars(select(City).where(
                City.id.in_(list(run.pickup_city_ids or []))).order_by(City.name)))
            return_city = session.get(City, run.return_city_id or run.city_id)
            result = _serialize_run(run)
            result.update({
                "vehicle_model": ({
                    "id": target.id,
                    "zuche_model_id": target.zuche_model_id,
                    "name": target.name,
                    "description": target.latest_description,
                } if target else None),
                "pickup_cities": [{"id": city.id, "name": city.name,
                                    "zuche_city_id": city.zuche_city_id}
                                   for city in cities],
                "return_city": ({"id": return_city.id, "name": return_city.name,
                                 "zuche_city_id": return_city.zuche_city_id}
                                if return_city else None),
                "return_location_name": run.return_location_name,
                "rental_windows": list(run.rental_windows or []),
                "rail_costs": dict(run.rail_costs or {}),
            })
            return result
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise HTTPException(500, "跨城找车任务读取失败，请稍后重试") from error


@router.post("/model-search-runs", status_code=201)
def create_model_search(payload: CreateModelSearchInput, request: Request):
    try:
        with request_session(request) as session:
            city_id = session.scalar(select(City.id).where(City.name == "广州").limit(1))
        if city_id is None:
            raise HTTPException(422, "系统中没有广州城市资料")
        now_provider = getattr(request.app.state, "model_search_now", None)
        run = request.app.state.model_search_service.create_run(
            city_id,
            payload.model_names,
            now=now_provider() if now_provider else None,
        )
        return _serialize_run(run)
    except HTTPException:
        raise
    except InvalidModelSearchTransition as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        logger.exception("创建按车型找车任务失败")
        raise HTTPException(500, "按车型找车任务创建失败，请稍后重试") from error


@router.get("/model-search-runs")
def list_model_search_runs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    try:
        with request_session(request) as session:
            condition = ModelSearchRun.search_kind == "WEEKEND"
            total = int(session.scalar(select(func.count()).select_from(
                ModelSearchRun).where(condition)) or 0)
            pages = max(1, ceil(total / page_size))
            actual_page = min(page, pages)
            rows = session.scalars(select(ModelSearchRun).where(condition).order_by(
                ModelSearchRun.created_at.desc(), ModelSearchRun.id.desc(),
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
        raise HTTPException(500, "按车型找车任务读取失败，请稍后重试") from error


@router.get("/model-search-runs/{run_id}")
def get_model_search_run(run_id: UUID, request: Request):
    try:
        with request_session(request) as session:
            run = session.get(ModelSearchRun, run_id)
            if run is None:
                raise HTTPException(404, "按车型找车任务不存在")
            return _serialize_run(run)
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise HTTPException(500, "按车型找车任务读取失败，请稍后重试") from error


@router.post("/model-search-runs/{run_id}/stop")
def stop_model_search_run(run_id: UUID, request: Request):
    return _control(request, "stop", run_id)


@router.post("/model-search-runs/{run_id}/resume")
def resume_model_search_run(run_id: UUID, request: Request):
    return _control(request, "resume", run_id)


@router.get("/model-search-runs/{run_id}/results")
def get_model_search_results(
    run_id: UUID,
    request: Request,
    availability: Literal["AVAILABLE", "NOT_FOUND", "INCOMPLETE", "ALL"] = "AVAILABLE",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return _catalog(request, lambda item: item.results(
        run_id, availability=availability, page=page, page_size=page_size))


@router.get("/model-search-runs/{run_id}/periods")
def get_model_search_periods(
    run_id: UUID,
    request: Request,
    model_name: str = Query(min_length=1, max_length=255),
    availability: Literal["AVAILABLE", "NOT_FOUND", "INCOMPLETE", "ALL"] = "ALL",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
):
    return _catalog(request, lambda item: item.periods(
        run_id, model_name=model_name, availability=availability,
        page=page, page_size=page_size))


@router.get("/model-library/options")
def get_model_library_options(
    request: Request,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
):
    return _catalog(request, lambda item: {"items": item.options(q=q, limit=limit)})


def _catalog(request: Request, callback):
    try:
        with request_session(request) as session:
            return callback(ModelSearchCatalog(session))
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(500, "按车型找车结果读取失败，请稍后重试") from error


def _control(request: Request, action: str, run_id: UUID):
    try:
        run = getattr(request.app.state.model_search_service, action)(run_id)
        return _serialize_run(run)
    except InvalidModelSearchTransition as error:
        message = str(error)
        raise HTTPException(404 if "不存在" in message else 409, message) from error
    except Exception as error:
        logger.exception("控制按车型找车任务失败")
        raise HTTPException(500, "任务操作失败，请稍后重试") from error


def _serialize_run(run: ModelSearchRun) -> dict:
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    phase = run.phase.value if hasattr(run.phase, "value") else str(run.phase)
    final_success = (
        status == "COMPLETED"
        and run.failed_sample_count == 0
        and run.completed_sample_count >= run.planned_sample_count
    )
    attempted_samples = max(
        0,
        run.completed_sample_count - run.cache_hit_count + run.failed_sample_count,
    )
    retry_count = max(0, run.request_count - attempted_samples)
    return {
        "id": str(run.id),
        "city_id": run.city_id,
        "search_kind": run.search_kind,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "phase": phase,
        "phase_label": PHASE_LABELS.get(phase, phase),
        "requested_names": list(run.requested_names or []),
        "estimated_request_count": run.estimated_request_count,
        "planned_sample_count": run.planned_sample_count,
        "completed_sample_count": run.completed_sample_count,
        "failed_sample_count": run.failed_sample_count,
        "cache_hit_count": run.cache_hit_count,
        "request_count": run.request_count,
        "candidate_department_count": run.candidate_department_count,
        "found_variant_count": run.found_variant_count,
        "available_department_count": run.available_department_count,
        "execution_concurrency": run.execution_concurrency,
        "final_success": final_success,
        "retry_count": retry_count,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "stopped_at": _iso(run.stopped_at),
        "completed_at": _iso(run.completed_at),
        "last_error_summary": None if final_success else run.last_error_summary,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
