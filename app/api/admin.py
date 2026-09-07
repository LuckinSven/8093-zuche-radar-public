from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import request_session
from app.models import City, Probe
from app.scheduling import validate_schedule

router = APIRouter()


class CityInput(BaseModel):
    zuche_city_id: str
    name: str = Field(min_length=1, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ProbeInput(BaseModel):
    city_id: int
    name: str = Field(min_length=1, max_length=128)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    enabled: bool = False
    schedule: str | None = None


class CityUpdate(BaseModel):
    zuche_city_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=64)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    enabled: bool | None = None


class ProbeUpdate(BaseModel):
    city_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    enabled: bool | None = None
    schedule: str | None = None


@router.get("/cities")
def list_cities(request: Request):
    with request_session(request) as session:
        return {"items": [_city(x) for x in session.scalars(select(City).order_by(City.name))]}


@router.post("/cities", status_code=201)
def create_city(payload: CityInput, request: Request):
    with request_session(request) as session:
        item = City(**payload.model_dump())
        session.add(item)
        try:
            session.flush()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "神州城市 ID 已存在") from error
        return _city(item)


@router.get("/probes")
def list_probes(request: Request):
    with request_session(request) as session:
        return {"items": [_probe(x) for x in session.scalars(select(Probe).order_by(Probe.name))]}


@router.post("/probes", status_code=201)
def create_probe(payload: ProbeInput, request: Request):
    with request_session(request) as session:
        _require_city(session, payload.city_id)
        _validate_probe_schedule(payload.enabled, payload.schedule)
        item = Probe(**payload.model_dump())
        session.add(item); session.flush()
        if item.enabled:
            session.commit()
            _reconcile(request)
        return _probe(item)


@router.patch("/cities/{city_id}")
def update_city(city_id: int, payload: CityUpdate, request: Request):
    with request_session(request) as session:
        item = session.get(City, city_id)
        if item is None:
            raise HTTPException(404, "城市不存在")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        try:
            session.flush()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "神州城市 ID 已存在") from error
        return _city(item)


@router.patch("/probes/{probe_id}")
def update_probe(probe_id: int, payload: ProbeUpdate, request: Request):
    with request_session(request) as session:
        item = session.get(Probe, probe_id)
        if item is None:
            raise HTTPException(404, "扫描点不存在")
        values = payload.model_dump(exclude_unset=True)
        if "city_id" in values:
            _require_city(session, values["city_id"])
        for key, value in values.items():
            setattr(item, key, value)
        _validate_probe_schedule(item.enabled, item.schedule)
        session.commit()
        _reconcile(request)
        return _probe(item)


@router.delete("/probes/{probe_id}", status_code=204)
def delete_probe(probe_id: int, request: Request):
    with request_session(request) as session:
        item = session.get(Probe, probe_id)
        if item is None:
            raise HTTPException(404, "扫描点不存在")
        session.delete(item)
        session.commit()
        _reconcile(request)
        return Response(status_code=204)


@router.delete("/cities/{city_id}", status_code=204)
def delete_city(city_id: int, request: Request):
    with request_session(request) as session:
        item = session.get(City, city_id)
        if item is None:
            raise HTTPException(404, "城市不存在")
        if session.scalar(select(Probe.id).where(Probe.city_id == city_id).limit(1)) is not None:
            raise HTTPException(409, "请先删除该城市下的扫描点")
        session.delete(item)
        return Response(status_code=204)


def _reconcile(request: Request) -> None:
    scheduler = getattr(request.app.state, "probe_scheduler", None)
    if scheduler is not None:
        scheduler.reconcile()


def _require_city(session, city_id: int) -> City:
    city = session.get(City, city_id)
    if city is None:
        raise HTTPException(422, "城市不存在")
    if not city.enabled:
        raise HTTPException(422, "城市已停用")
    return city


def _validate_probe_schedule(enabled: bool, schedule: str | None) -> None:
    if enabled and not schedule:
        raise HTTPException(422, "开启自动扫描时必须设置计划")
    if schedule:
        try:
            validate_schedule(schedule)
        except (TypeError, ValueError) as error:
            raise HTTPException(422, str(error)) from error


def _city(x):
    return {"id": x.id, "zuche_city_id": x.zuche_city_id, "name": x.name,
            "latitude": float(x.latitude) if x.latitude is not None else None,
            "longitude": float(x.longitude) if x.longitude is not None else None,
            "enabled": x.enabled, "catalog_active": x.catalog_active}


def _probe(x):
    return {"id": x.id, "city_id": x.city_id, "name": x.name, "latitude": float(x.latitude),
            "longitude": float(x.longitude), "enabled": x.enabled, "schedule": x.schedule}
