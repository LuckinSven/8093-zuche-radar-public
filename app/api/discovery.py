from typing import Annotated

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from app.api.dependencies import request_session
from app.discovery import DiscoveryFilters, DiscoveryService
from app.models import AvailabilitySnapshot, ChangeEvent, Department, ScanRun

router = APIRouter()


@router.get("/discovery")
def discovery(request: Request, filters: Annotated[DiscoveryFilters, Query()]):
    with request_session(request) as session:
        service = DiscoveryService(session)
        return {"items": [x.model_dump(mode="json") for x in service.search(filters)],
                "groups": service.list_groups(filters.scan_id)}


@router.get("/discovery/summary")
def discovery_summary(scan_id: UUID, request: Request, primary_department_id: int = 79340):
    with request_session(request) as session:
        run = session.get(ScanRun, scan_id)
        if run is None:
            raise HTTPException(404, "扫描记录不存在")
        offers = session.execute(select(
            AvailabilitySnapshot.vehicle_model_id, Department.zuche_dept_id, Department.name)
            .join(Department, Department.id == AvailabilitySnapshot.department_id)
            .where(AvailabilitySnapshot.scan_run_id == scan_id,
                   AvailabilitySnapshot.book_flag.is_(True))).all()
        primary_models = {row[0] for row in offers if row[1] == primary_department_id}
        nearby_models = {row[0] for row in offers if row[1] != primary_department_id}
        primary_name = next((row[2] for row in offers if row[1] == primary_department_id),
                            "鱼珠地铁站服务点")
        changed_count = session.scalar(select(func.count(func.distinct(ChangeEvent.vehicle_model_id)))
            .where(ChangeEvent.scan_run_id == scan_id,
                   ChangeEvent.event_type.in_(("FIRST_SEEN", "REAPPEARED")))) or 0
        return {
            "scan_id": str(run.id),
            "primary_department_id": primary_department_id,
            "primary_department_name": primary_name,
            "primary_model_count": len(primary_models),
            "supplemental_model_count": len(nearby_models - primary_models),
            "new_or_reappeared_count": changed_count,
            "started_at": run.started_at,
            "pickup_time": run.pickup_time,
            "return_time": run.return_time,
        }


@router.get("/models/{model_id}")
def model_detail(model_id: int, request: Request):
    with request_session(request) as session:
        result = DiscoveryService(session).get_model_detail(model_id)
        if result is None:
            raise HTTPException(404, "车型不存在")
        return result
