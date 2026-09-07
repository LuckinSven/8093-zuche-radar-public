from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import distinct, func, select

from app.api.dependencies import request_session
from app.domain import ScanQuery
from app.models import AvailabilitySnapshot, ChangeEvent, ScanRun
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger


def _scan_validation_message(error: RequestValidationError) -> str:
    locations = [tuple(item.get("loc", ())) for item in error.errors()]
    if any(location[-1:] == ("latitude",) for location in locations):
        return "纬度必须在 -90 到 90 之间"
    if any(location[-1:] == ("longitude",) for location in locations):
        return "经度必须在 -180 到 180 之间"
    if any(location[-1:] == ("location_name",) for location in locations):
        return "取车地点不能为空且最多 128 个字符"
    if any(location[-1:] in (("pickup_time",), ("return_time",)) for location in locations):
        return "取车和还车时间格式不正确"
    if any(location[-1:] == ("scan_id",) for location in locations):
        return "扫描记录编号格式不正确"
    for item in error.errors():
        context_error = item.get("ctx", {}).get("error")
        if context_error and "还车时间必须晚于取车时间" in str(context_error):
            return "还车时间必须晚于取车时间"
    return "扫描参数不正确"


class ChineseScanValidationRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def translated_handler(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as error:
                raise HTTPException(422, _scan_validation_message(error)) from error

        return translated_handler


router = APIRouter(route_class=ChineseScanValidationRoute)


@router.post("/scans", status_code=201)
async def create_scan(payload: ScanQuery, request: Request):
    injected = getattr(request.app.state, "scanner", None)
    if injected is not None:
        result = await injected.run(payload, ScanTrigger.MANUAL)
    else:
        session = request.app.state.session_factory()
        try:
            async with request.app.state.zuche_client_factory() as gateway:
                result = await ScanService(gateway, ScanRepository(session)).run(payload, ScanTrigger.MANUAL)
        finally:
            session.close()
    return result.model_dump(mode="json")


@router.get("/scans/{scan_id}")
def get_scan(scan_id: UUID, request: Request):
    with request_session(request) as session:
        item = session.get(ScanRun, scan_id)
        if item is None: raise HTTPException(404, "扫描记录不存在")
        return _run(item, session)


@router.get("/history")
def history(request: Request):
    with request_session(request) as session:
        runs = list(session.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(200)))
        run_ids = [item.id for item in runs]
        snapshot_counts = {scan_id: {"offer_count": offer_count, "department_count": department_count,
                                     "model_count": model_count}
            for scan_id, offer_count, department_count, model_count in session.execute(
                select(AvailabilitySnapshot.scan_run_id, func.count(),
                       func.count(distinct(AvailabilitySnapshot.department_id)),
                       func.count(distinct(AvailabilitySnapshot.vehicle_model_id)))
                .where(AvailabilitySnapshot.scan_run_id.in_(run_ids))
                .group_by(AvailabilitySnapshot.scan_run_id))} if run_ids else {}
        events_by_run = {}
        if run_ids:
            for event in session.scalars(select(ChangeEvent).where(ChangeEvent.scan_run_id.in_(run_ids))
                                         .order_by(ChangeEvent.id)):
                events_by_run.setdefault(event.scan_run_id, []).append(
                    {"type": event.event_type, "detail": event.detail})
        items = []
        for run in runs:
            events = events_by_run.get(run.id, [])
            counts = snapshot_counts.get(run.id, {"offer_count": 0, "department_count": 0, "model_count": 0})
            items.append(_run(run) | counts | {"event_count": len(events), "events": events})
        return {"items": items}


def _run(x, session=None):
    counts = {}
    if session is not None:
        event_rows = session.scalars(select(ChangeEvent).where(ChangeEvent.scan_run_id == x.id)
                                     .order_by(ChangeEvent.id)).all()
        counts = {"department_count": int(session.scalar(select(func.count(distinct(AvailabilitySnapshot.department_id))).where(AvailabilitySnapshot.scan_run_id == x.id)) or 0),
                  "offer_count": int(session.scalar(select(func.count()).select_from(AvailabilitySnapshot).where(AvailabilitySnapshot.scan_run_id == x.id)) or 0),
                  "model_count": int(session.scalar(select(func.count(distinct(AvailabilitySnapshot.vehicle_model_id))).where(AvailabilitySnapshot.scan_run_id == x.id)) or 0),
                  "event_count": len(event_rows),
                  "events": [{"type": event.event_type, "detail": event.detail} for event in event_rows]}
    return {"id": str(x.id), "status": str(x.status), "trigger": x.trigger,
            "zuche_city_id": x.zuche_city_id, "location_name": x.location_name,
            "latitude": float(x.latitude), "longitude": float(x.longitude), "pickup_time": x.pickup_time,
            "return_time": x.return_time, "started_at": x.started_at, "completed_at": x.completed_at,
            "error_code": x.error_code, "error_message": x.error_message} | counts
