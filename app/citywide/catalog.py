"""全城车型和永久车型库的数据库侧筛选、聚合与分页。"""

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    CitywideModelSummary,
    CitywideOffer,
    CitywideScanRun,
    CitywideScanStatus,
    Department,
    PersonalState,
    PersonalVehicleState,
    VehicleModel,
)


Availability = Literal["AVAILABLE", "NOT_FOUND", "INCOMPLETE"]
LibrarySort = Literal["recent", "first_seen", "name"]


class CitywideModelFilters(BaseModel):
    run_id: UUID
    q: str = Field(default="", max_length=100)
    department_id: int | None = Field(default=None, gt=0)
    max_fish_distance_km: float | None = Field(default=None, ge=0)
    availability: Availability | None = None
    first_seen_from: date | None = None
    last_seen_from: date | None = None
    energy_type: str = Field(default="", max_length=64)
    personal_state: PersonalState | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class ModelLibraryFilters(BaseModel):
    run_id: UUID | None = None
    q: str = Field(default="", max_length=100)
    energy_type: str = Field(default="", max_length=64)
    personal_state: PersonalState | None = None
    first_seen_from: date | None = None
    last_seen_from: date | None = None
    sort: LibrarySort = "recent"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class CitywideCatalog:
    def __init__(self, session: Session) -> None:
        self.session = session

    def search(self, filters: CitywideModelFilters) -> dict:
        run = self.session.get(CitywideScanRun, filters.run_id)
        if run is None:
            raise LookupError("全城扫描任务不存在")
        complete = _is_complete(run)
        statement = self._base_statement(run.id)
        conditions = self._common_conditions(filters)
        if filters.department_id is not None:
            conditions.append(exists(select(CitywideOffer.id).where(
                CitywideOffer.run_id == run.id,
                CitywideOffer.vehicle_model_id == VehicleModel.id,
                CitywideOffer.department_id == filters.department_id,
            )))
        if filters.max_fish_distance_km is not None:
            conditions.append(
                CitywideModelSummary.nearest_distance_km <= filters.max_fish_distance_km)
        if filters.availability == "AVAILABLE":
            conditions.append(CitywideModelSummary.id.is_not(None))
        elif filters.availability in {"NOT_FOUND", "INCOMPLETE"}:
            expected_missing_status = "NOT_FOUND" if complete else "INCOMPLETE"
            if filters.availability == expected_missing_status:
                conditions.append(CitywideModelSummary.id.is_(None))
            else:
                conditions.append(CitywideModelSummary.id.is_not(None))
                conditions.append(CitywideModelSummary.id.is_(None))
        statement = statement.where(*conditions)
        return self._page(statement, filters.page, filters.page_size, complete)

    def library(self, filters: ModelLibraryFilters) -> dict:
        run = self.session.get(CitywideScanRun, filters.run_id) if filters.run_id else None
        if filters.run_id and run is None:
            raise LookupError("全城扫描任务不存在")
        statement = self._base_statement(run.id if run else None).where(
            *self._common_conditions(filters))
        result = self._page(
            statement,
            filters.page,
            filters.page_size,
            _is_complete(run) if run else False,
            order_by=self._library_order(filters.sort),
        )
        result["summary"] = self._library_summary()
        return result

    def offers(self, run_id: UUID, zuche_model_id: int) -> list[dict]:
        if self.session.get(CitywideScanRun, run_id) is None:
            raise LookupError("全城扫描任务不存在")
        model = self.session.scalar(select(VehicleModel).where(
            VehicleModel.zuche_model_id == zuche_model_id))
        if model is None:
            raise LookupError("车型不存在")
        rows = self.session.execute(select(
            CitywideOffer, Department,
        ).join(
            Department, Department.id == CitywideOffer.department_id,
        ).where(
            CitywideOffer.run_id == run_id,
            CitywideOffer.vehicle_model_id == model.id,
        ).order_by(
            CitywideOffer.distance_from_yuzhu_km.asc().nulls_last(),
            Department.name,
        )).all()
        return [{
            "department_id": department.id,
            "zuche_department_id": department.zuche_dept_id,
            "department_name": department.name,
            "address": department.address,
            "distance_from_fish_km": _number(offer.distance_from_yuzhu_km),
            "daily_price": _number(offer.daily_price),
            "package_price": _number(offer.package_price),
            "bookable": offer.book_flag,
            "inventory_type": offer.inventory_type,
        } for offer, department in rows]

    def detail(self, zuche_model_id: int, run_id: UUID | None) -> dict:
        model = self.session.scalar(select(VehicleModel).where(
            VehicleModel.zuche_model_id == zuche_model_id))
        if model is None:
            raise LookupError("车型不存在")
        run = self.session.get(CitywideScanRun, run_id) if run_id else None
        if run_id and run is None:
            raise LookupError("全城扫描任务不存在")
        summary = self.session.scalar(select(CitywideModelSummary).where(
            CitywideModelSummary.run_id == run_id,
            CitywideModelSummary.vehicle_model_id == model.id,
        )) if run_id else None
        state = self.session.get(PersonalVehicleState, model.id)
        history = self.session.execute(select(
            CitywideModelSummary, CitywideScanRun,
        ).join(
            CitywideScanRun, CitywideScanRun.id == CitywideModelSummary.run_id,
        ).where(
            CitywideModelSummary.vehicle_model_id == model.id,
        ).order_by(CitywideScanRun.created_at.desc())).all()
        result = _serialize_model(model, summary, state, _is_complete(run) if run else False)
        result["history"] = [{
            "run_id": str(history_run.id),
            "pickup_time": history_run.pickup_time.isoformat(),
            "return_time": history_run.return_time.isoformat(),
            "department_count": item.available_department_count,
            "average_price": _number(item.average_price),
            "minimum_price": _number(item.minimum_price),
            "maximum_price": _number(item.maximum_price),
        } for item, history_run in history]
        return result

    @staticmethod
    def _base_statement(run_id: UUID | None):
        join_condition = (
            (CitywideModelSummary.vehicle_model_id == VehicleModel.id)
            & (CitywideModelSummary.run_id == run_id)
        ) if run_id else (CitywideModelSummary.id.is_(None))
        return select(
            VehicleModel,
            CitywideModelSummary,
            PersonalVehicleState,
        ).select_from(VehicleModel).outerjoin(
            CitywideModelSummary, join_condition,
        ).outerjoin(
            PersonalVehicleState,
            PersonalVehicleState.vehicle_model_id == VehicleModel.id,
        )

    @staticmethod
    def _common_conditions(filters) -> list:
        conditions = []
        if filters.q:
            conditions.append(VehicleModel.name.ilike(f"%{filters.q.strip()}%"))
        if filters.energy_type:
            conditions.append(VehicleModel.energy_type == filters.energy_type.strip())
        if filters.personal_state is not None:
            conditions.append(PersonalVehicleState.state == filters.personal_state)
        if filters.first_seen_from is not None:
            conditions.append(func.date(VehicleModel.first_seen_at) >= filters.first_seen_from)
        if filters.last_seen_from is not None:
            conditions.append(func.date(VehicleModel.last_seen_at) >= filters.last_seen_from)
        return conditions

    def _library_summary(self) -> dict[str, int]:
        cutoff = datetime.now(UTC) - timedelta(days=7)
        variant_count, model_name_count, new_last_7_days = self.session.execute(select(
            func.count(VehicleModel.id),
            func.count(func.distinct(VehicleModel.name)),
            func.count(VehicleModel.id).filter(VehicleModel.first_seen_at >= cutoff),
        )).one()
        return {
            "variant_count": int(variant_count or 0),
            "model_name_count": int(model_name_count or 0),
            "new_last_7_days": int(new_last_7_days or 0),
        }

    @staticmethod
    def _library_order(sort: LibrarySort):
        if sort == "name":
            return (
                VehicleModel.name.asc(),
                VehicleModel.latest_description.asc().nulls_last(),
                VehicleModel.zuche_model_id.asc(),
            )
        if sort == "first_seen":
            return (
                VehicleModel.first_seen_at.desc().nulls_last(),
                VehicleModel.name.asc(),
            )
        return (
            VehicleModel.last_seen_at.desc().nulls_last(),
            VehicleModel.name.asc(),
        )

    def _page(
        self,
        statement,
        page: int,
        page_size: int,
        complete: bool,
        order_by=None,
    ) -> dict:
        total = int(self.session.scalar(select(func.count()).select_from(
            statement.with_only_columns(VehicleModel.id).order_by(None).subquery())) or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        actual_page = min(page, pages)
        ordering = order_by or (
            CitywideModelSummary.id.is_(None),
            VehicleModel.last_seen_at.desc().nulls_last(),
            VehicleModel.name,
        )
        rows = self.session.execute(statement.order_by(*ordering).offset(
            (actual_page - 1) * page_size).limit(page_size)).all()
        return {
            "items": [_serialize_model(*row, complete) for row in rows],
            "pagination": {
                "page": actual_page,
                "page_size": page_size,
                "total": total,
                "pages": pages,
            },
        }


def _is_complete(run: CitywideScanRun | None) -> bool:
    return bool(
        run is not None
        and run.status == CitywideScanStatus.COMPLETED
        and run.failed_point_count == 0
        and run.completed_point_count == run.planned_point_count
    )


def _serialize_model(model, summary, state, complete: bool) -> dict:
    availability = "AVAILABLE" if summary is not None else (
        "NOT_FOUND" if complete else "INCOMPLETE")
    minimum = _number(summary.minimum_price) if summary else None
    maximum = _number(summary.maximum_price) if summary else None
    return {
        "model_id": model.zuche_model_id,
        "model_name": model.name,
        "description": model.latest_description,
        "image_url": model.image_url,
        "energy_type": model.energy_type,
        "energy_subtype": model.energy_subtype,
        "energy_source": model.energy_source,
        "energy_confidence": model.energy_confidence,
        "energy_updated_at": (
            model.energy_updated_at.isoformat() if model.energy_updated_at else None),
        "first_seen_at": model.first_seen_at.isoformat() if model.first_seen_at else None,
        "last_seen_at": model.last_seen_at.isoformat() if model.last_seen_at else None,
        "availability": availability,
        "department_count": summary.available_department_count if summary else 0,
        "nearest_fish_distance_km": _number(summary.nearest_distance_km) if summary else None,
        "average_price": _number(summary.average_price) if summary else None,
        "minimum_price": minimum,
        "maximum_price": maximum,
        "has_price_difference": minimum is not None and maximum is not None and minimum != maximum,
        "personal_state": str(state.state) if state else "UNTRIED",
        "personal_note": state.note if state else None,
    }


def _number(value) -> float | None:
    return float(value) if value is not None else None
