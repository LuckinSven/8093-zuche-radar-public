import re
from math import ceil
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.departments.planner import haversine_km
from app.energy import classify_energy
from app.models import (AvailabilitySnapshot, ChangeEvent, Department, ModelGroupMembership,
                        PersonalVehicleState, ScanRun, ScanStatus, VehicleModel)


class DataTableFilters(BaseModel):
    scan_id: UUID | None = None
    department_id: int | None = None
    q: str | None = Field(default=None, max_length=100)
    energy_type: str | None = None
    body_style: str | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    max_distance_km: float | None = Field(default=None, ge=0)
    seat_count: int | None = Field(default=None, ge=1)
    group_id: int | None = None
    personal_state: str | None = None
    change_type: str | None = None
    bookable: bool | None = None
    sort: Literal["started_at", "model_name", "department_name", "shenzhou_distance_km",
                  "coordinate_distance_km", "daily_price", "package_price", "bookable"] = "model_name"
    order: Literal["asc", "desc"] = "asc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class DataTableService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_scans(self, limit: int = 100) -> list[dict]:
        statement = (select(
            ScanRun.id, ScanRun.started_at, ScanRun.location_name, ScanRun.pickup_time,
            ScanRun.return_time, ScanRun.trigger, func.count(AvailabilitySnapshot.id),
            func.count(func.distinct(AvailabilitySnapshot.vehicle_model_id)),
            func.count(func.distinct(AvailabilitySnapshot.department_id)),
        ).join(AvailabilitySnapshot, AvailabilitySnapshot.scan_run_id == ScanRun.id)
         .where(ScanRun.status == ScanStatus.SUCCESS)
         .group_by(ScanRun.id)
         .order_by(ScanRun.started_at.desc()).limit(limit))
        return [{"id": str(row[0]), "started_at": row[1], "location_name": row[2],
                 "pickup_time": row[3], "return_time": row[4],
                 "pickup_time_label": _rental_time_label(row[3], row[5]),
                 "return_time_label": _rental_time_label(row[4], row[5]),
                 "offer_count": row[6], "model_count": row[7], "department_count": row[8]}
                for row in self.session.execute(statement)]

    def search(self, filters: DataTableFilters, paginate: bool = True) -> dict:
        scan_id = filters.scan_id or self._latest_scan_id()
        if scan_id is None:
            return {"scan_id": None, "total": 0, "page": 1, "page_size": filters.page_size,
                    "pages": 0, "departments": [], "items": []}
        rows = self._rows(scan_id)
        departments = sorted({(row["department_id"], row["department_name"]) for row in rows},
                             key=lambda item: item[1])
        if filters.department_id is not None:
            rows = [row for row in rows if row["department_id"] == filters.department_id]
        if filters.q:
            needle = filters.q.strip().casefold()
            rows = [row for row in rows if needle in " ".join(str(row.get(field) or "") for field in (
                "scan_location", "model_id", "model_name", "model_desc", "native_groups",
                "department_id", "department_name", "department_address")).casefold()]
        if filters.energy_type:
            rows = [row for row in rows if row["energy_type"] == filters.energy_type]
        if filters.body_style:
            rows = [row for row in rows if row["body_style"] == filters.body_style]
        if filters.min_price is not None:
            rows = [row for row in rows if _row_price(row) is not None
                    and _row_price(row) >= filters.min_price]
        if filters.max_price is not None:
            rows = [row for row in rows if _row_price(row) is not None
                    and _row_price(row) <= filters.max_price]
        if filters.max_distance_km is not None:
            rows = [row for row in rows if row["shenzhou_distance_km"] is not None
                    and row["shenzhou_distance_km"] <= filters.max_distance_km]
        if filters.seat_count is not None:
            rows = [row for row in rows if row["seat_count"] == filters.seat_count]
        if filters.group_id is not None:
            rows = [row for row in rows if filters.group_id in row["native_group_ids"]]
        if filters.personal_state:
            rows = [row for row in rows if row["personal_state"] == filters.personal_state]
        if filters.change_type:
            rows = [row for row in rows if filters.change_type in row["changes"]]
        if filters.bookable is not None:
            rows = [row for row in rows if row["bookable"] is filters.bookable]
        present = [row for row in rows if row.get(filters.sort) is not None]
        missing = [row for row in rows if row.get(filters.sort) is None]
        rows = sorted(present, key=lambda row: row[filters.sort], reverse=filters.order == "desc") + missing
        total = len(rows)
        if paginate:
            start = (filters.page - 1) * filters.page_size
            rows = rows[start:start + filters.page_size]
        return {"scan_id": str(scan_id), "total": total, "page": filters.page,
                "page_size": filters.page_size, "pages": ceil(total / filters.page_size) if total else 0,
                "departments": [{"id": item[0], "name": item[1]} for item in departments],
                "items": rows}

    def _latest_scan_id(self) -> UUID | None:
        return self.session.scalar(select(ScanRun.id)
            .join(AvailabilitySnapshot, AvailabilitySnapshot.scan_run_id == ScanRun.id)
            .where(ScanRun.status == ScanStatus.SUCCESS)
            .order_by(ScanRun.started_at.desc()).limit(1))

    def _rows(self, scan_id: UUID) -> list[dict]:
        result = self.session.execute(select(AvailabilitySnapshot, VehicleModel, Department, ScanRun)
            .join(VehicleModel, VehicleModel.id == AvailabilitySnapshot.vehicle_model_id)
            .join(Department, Department.id == AvailabilitySnapshot.department_id)
            .join(ScanRun, ScanRun.id == AvailabilitySnapshot.scan_run_id)
            .where(AvailabilitySnapshot.scan_run_id == scan_id)).all()
        group_rows = self.session.execute(select(
            ModelGroupMembership.vehicle_model_id, ModelGroupMembership.group_id,
            ModelGroupMembership.group_name)
            .where(ModelGroupMembership.scan_run_id == scan_id)).all()
        groups: dict[int, list[tuple[int, str]]] = {}
        for model_id, group_id, name in group_rows:
            if (group_id, name) not in groups.setdefault(model_id, []):
                groups[model_id].append((group_id, name))
        personal = {row[0]: row[1].value for row in self.session.execute(select(
            PersonalVehicleState.vehicle_model_id, PersonalVehicleState.state)).all()}
        changes: dict[int, list[str]] = {}
        for model_id, event_type in self.session.execute(select(
                ChangeEvent.vehicle_model_id, ChangeEvent.event_type)
                .where(ChangeEvent.scan_run_id == scan_id)).all():
            if event_type not in changes.setdefault(model_id, []):
                changes[model_id].append(event_type)
        rows = []
        for snapshot, model, department, run in result:
            memberships = groups.get(model.id, [])
            names = [item[1] for item in memberships]
            body_style, seat_count = _vehicle_shape(snapshot.model_desc)
            coordinate_distance = None
            if department.latitude is not None and department.longitude is not None:
                coordinate_distance = round(haversine_km(
                    (float(run.latitude), float(run.longitude)),
                    (float(department.latitude), float(department.longitude))), 3)
            rows.append({
                "scan_id": str(run.id), "started_at": run.started_at, "scan_location": run.location_name,
                "pickup_time": run.pickup_time, "return_time": run.return_time,
                "model_id": model.zuche_model_id, "model_name": model.name,
                "model_desc": snapshot.model_desc, "native_groups": names,
                "native_group_ids": [item[0] for item in memberships],
                "energy_type": classify_energy(snapshot.model_desc, model.name, names),
                "body_style": body_style, "seat_count": seat_count,
                "personal_state": personal.get(model.id, "UNTRIED"),
                "changes": changes.get(model.id, []),
                "department_id": department.zuche_dept_id, "department_name": department.name,
                "department_address": department.address,
                "shenzhou_distance_km": float(snapshot.department_distance_km)
                    if snapshot.department_distance_km is not None else _text_distance(snapshot.department_distance),
                "coordinate_distance_km": coordinate_distance,
                "daily_price": float(snapshot.daily_price) if snapshot.daily_price is not None else None,
                "package_price": float(snapshot.package_price) if snapshot.package_price is not None else None,
                "bookable": snapshot.book_flag, "price_is_final": False,
            })
        return rows


def _vehicle_shape(description: str | None) -> tuple[str | None, int | None]:
    body = re.search(r"(SUV|MPV|两厢|三厢|跑车|皮卡)(?=\d座|\s|$)", description or "", re.I)
    seats = re.search(r"(\d)座", description or "")
    return (body.group(1).upper() if body else None, int(seats.group(1)) if seats else None)


def _text_distance(value: str | None) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    if not match:
        return None
    number = float(match.group())
    return number / 1000 if "m" in (value or "").lower() and "km" not in (value or "").lower() else number


def _row_price(row: dict) -> float | None:
    return row["package_price"] if row["package_price"] is not None else row["daily_price"]


def _rental_time_label(value, trigger: str) -> str:
    if trigger == "SCHEDULED" and value.tzinfo is not None:
        value = value.astimezone(ZoneInfo("Asia/Shanghai"))
    return value.strftime("%Y-%m-%d %H:%M")
