"""车型能源 AI 补全任务 API。"""

import logging
from datetime import datetime
from math import ceil
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import request_session
from app.enrichment.domain import EnrichmentScope
from app.enrichment.repository import InvalidEnrichmentTransition
from app.models import VehicleEnrichmentResult, VehicleEnrichmentRun, VehicleModel


logger = logging.getLogger(__name__)
router = APIRouter()

STATUS_LABELS = {
    "PENDING": "等待中",
    "RUNNING": "补全中",
    "STOPPED": "已停止",
    "INTERRUPTED": "已中断",
    "COMPLETED": "已完成",
    "PARTIAL": "部分完成",
    "FAILED": "失败",
}
SCOPE_LABELS = {"PENDING_ONLY": "补全待处理车型", "ALL": "重新识别全部车型"}
STAGE_LABELS = {"BATCH": "逐车型初判", "FOCUSED": "AI 二次识别"}
RESULT_STATUS_LABELS = {
    "PENDING": "等待中",
    "RUNNING": "处理中",
    "APPLIED": "已应用",
    "KEPT_UNKNOWN": "保持未知",
    "FAILED": "失败",
}


class CreateEnrichmentRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["PENDING_ONLY", "ALL"] = "PENDING_ONLY"


class ManualResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    energy_type: Literal["燃油", "新能源", "未知"]
    energy_subtype: Literal["汽油", "柴油", "油电混动", "纯电", "插电混动", "增程", "其他", "未知"]
    note: str = Field(min_length=1, max_length=500)


@router.post("/vehicle-enrichment-runs", status_code=201)
def create_enrichment_run(payload: CreateEnrichmentRunInput, request: Request):
    try:
        run = request.app.state.vehicle_enrichment_service.create_run(
            EnrichmentScope(payload.scope))
        return _serialize_run(run)
    except InvalidEnrichmentTransition as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        logger.exception("创建 AI 补全任务失败")
        raise HTTPException(500, "AI 补全任务创建失败，请稍后重试") from error


@router.get("/vehicle-enrichment-runs")
def list_enrichment_runs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    try:
        with request_session(request) as session:
            total = int(session.scalar(
                select(func.count()).select_from(VehicleEnrichmentRun)) or 0)
            pages = max(1, ceil(total / page_size))
            actual_page = min(page, pages)
            rows = session.scalars(select(VehicleEnrichmentRun).order_by(
                VehicleEnrichmentRun.created_at.desc(),
                VehicleEnrichmentRun.id.desc(),
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
        raise HTTPException(500, "AI 补全任务读取失败，请稍后重试") from error


@router.get("/vehicle-enrichment-runs/{run_id}")
def get_enrichment_run(run_id: UUID, request: Request):
    try:
        with request_session(request) as session:
            run = session.get(VehicleEnrichmentRun, run_id)
            if run is None:
                raise HTTPException(404, "AI 补全任务不存在")
            data = _serialize_run(run)
            rows = session.execute(select(
                VehicleEnrichmentResult,
                VehicleModel,
            ).join(
                VehicleModel,
                VehicleModel.id == VehicleEnrichmentResult.vehicle_model_id,
            ).where(
                VehicleEnrichmentResult.run_id == run.id,
                VehicleEnrichmentResult.status != "APPLIED",
            ).order_by(VehicleEnrichmentResult.id).limit(100)).all()
            data["results"] = [
                _serialize_result(result, model) for result, model in rows
            ]
            return data
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise HTTPException(500, "AI 补全任务读取失败，请稍后重试") from error


@router.post("/vehicle-enrichment-runs/{run_id}/stop")
def stop_enrichment_run(run_id: UUID, request: Request):
    return _control(request, "stop", run_id)


@router.post("/vehicle-enrichment-runs/{run_id}/resume")
def resume_enrichment_run(run_id: UUID, request: Request):
    return _control(request, "resume", run_id)


@router.post("/vehicle-enrichment-runs/{run_id}/retry-failed")
def retry_failed_enrichment_run(run_id: UUID, request: Request):
    return _control(request, "retry_failed", run_id)


@router.put("/vehicle-enrichment-runs/{run_id}/results/{model_id}/manual-resolution")
def manually_resolve_enrichment_result(
    run_id: UUID,
    model_id: int,
    payload: ManualResolutionInput,
    request: Request,
):
    try:
        run = request.app.state.vehicle_enrichment_service.resolve_manually(
            run_id, model_id, **payload.model_dump())
        return _serialize_run(run)
    except InvalidEnrichmentTransition as error:
        status_code = 404 if "不存在" in str(error) else 409
        raise HTTPException(status_code, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        logger.exception("人工处理 AI 补全结果失败")
        raise HTTPException(500, "人工处理失败，请稍后重试") from error


@router.delete("/vehicle-enrichment-runs/{run_id}", status_code=204)
def delete_enrichment_run(run_id: UUID, request: Request):
    try:
        request.app.state.vehicle_enrichment_service.delete_run(run_id)
        return Response(status_code=204)
    except InvalidEnrichmentTransition as error:
        status_code = 404 if "不存在" in str(error) else 409
        raise HTTPException(status_code, str(error)) from error
    except Exception as error:
        logger.exception("删除 AI 补全任务失败")
        raise HTTPException(500, "AI 补全任务删除失败，请稍后重试") from error


def _control(request: Request, action: str, run_id: UUID):
    try:
        run = getattr(request.app.state.vehicle_enrichment_service, action)(run_id)
        return _serialize_run(run)
    except InvalidEnrichmentTransition as error:
        status_code = 404 if "不存在" in str(error) else 409
        raise HTTPException(status_code, str(error)) from error
    except Exception as error:
        logger.exception("控制 AI 补全任务失败")
        raise HTTPException(500, "AI 补全任务操作失败，请稍后重试") from error


def _serialize_run(run: VehicleEnrichmentRun) -> dict:
    status = str(run.status)
    scope = str(run.scope)
    stage = str(run.current_stage)
    return {
        "id": str(run.id),
        "scope": scope,
        "scope_label": SCOPE_LABELS.get(scope, scope),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "current_stage": stage,
        "current_stage_label": STAGE_LABELS.get(stage, stage),
        "model_name": run.model_name,
        "endpoint_label": run.endpoint_label,
        "total_count": run.total_count,
        "processed_count": run.processed_count,
        "updated_count": run.updated_count,
        "unknown_count": run.unknown_count,
        "failed_count": run.failed_count,
        "batch_request_count": run.batch_request_count,
        "focused_request_count": run.focused_request_count,
        "request_count": run.batch_request_count + run.focused_request_count,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "total_tokens": run.total_tokens,
        "created_at": _datetime(run.created_at),
        "started_at": _datetime(run.started_at),
        "stopped_at": _datetime(run.stopped_at),
        "completed_at": _datetime(run.completed_at),
        "updated_at": _datetime(run.updated_at),
        "last_error_summary": run.last_error_summary,
    }


def _serialize_result(result: VehicleEnrichmentResult, model: VehicleModel) -> dict:
    status = str(result.status)
    stage = str(result.stage)
    return {
        "model_id": model.zuche_model_id,
        "model_name": model.name,
        "status": status,
        "status_label": RESULT_STATUS_LABELS.get(status, status),
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "suggested_energy_type": result.suggested_energy_type,
        "suggested_energy_subtype": result.suggested_energy_subtype,
        "suggested_confidence": result.suggested_confidence,
        "rationale": result.rationale,
        "sources": result.sources_json,
        "not_applied_reason": result.not_applied_reason,
        "attempt_count": result.attempt_count,
        "error_summary": result.error_summary,
    }


def _datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
