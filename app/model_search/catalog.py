"""按车型找车任务与三级结果的数据库读取。"""

from math import ceil
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    City,
    Department,
    ModelSearchOffer,
    ModelSearchRun,
    ModelSearchRunStatus,
    ModelSearchSample,
    ModelSearchSampleStatus,
    ModelSearchTarget,
    PersonalVehicleState,
    VehicleModel,
)


FINAL_COMPLETE = {ModelSearchRunStatus.COMPLETED}


class ModelSearchCatalog:
    def __init__(self, session: Session) -> None:
        self.session = session

    def results(
        self,
        run_id: UUID,
        *,
        availability: str = "AVAILABLE",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        run = self._run(run_id)
        names = list(self.session.scalars(select(
            ModelSearchTarget.requested_name).where(
                ModelSearchTarget.run_id == run_id,
            ).distinct().order_by(ModelSearchTarget.requested_name)))
        items = [self._name_summary(run, name) for name in names]
        if availability != "ALL":
            items = [item for item in items if item["availability"] == availability]
        total = len(items)
        pages = max(1, ceil(total / page_size))
        actual_page = min(max(1, page), pages)
        start = (actual_page - 1) * page_size
        return {
            "items": items[start:start + page_size],
            "pagination": {
                "page": actual_page,
                "page_size": page_size,
                "total": total,
                "pages": pages,
            },
        }

    def periods(
        self,
        run_id: UUID,
        *,
        model_name: str,
        availability: str = "ALL",
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        run = self._run(run_id)
        model_rows = list(self.session.execute(select(
            VehicleModel.id,
            VehicleModel.zuche_model_id,
            VehicleModel.name,
            VehicleModel.latest_description,
            VehicleModel.energy_type,
            PersonalVehicleState.state,
        ).join(
            ModelSearchTarget,
            ModelSearchTarget.vehicle_model_id == VehicleModel.id,
        ).outerjoin(
            PersonalVehicleState,
            PersonalVehicleState.vehicle_model_id == VehicleModel.id,
        ).where(
            ModelSearchTarget.run_id == run_id,
            ModelSearchTarget.requested_name == model_name,
        ).order_by(VehicleModel.zuche_model_id)))
        if not model_rows:
            raise ValueError("任务中不存在该目标车型")
        model_ids = [row.id for row in model_rows]
        sample_rows = list(self.session.execute(select(
            ModelSearchSample.pickup_time,
            ModelSearchSample.return_time,
            ModelSearchSample.status,
        ).where(
            ModelSearchSample.run_id == run_id,
        ).order_by(
            ModelSearchSample.pickup_time,
            ModelSearchSample.return_time,
        )))
        statuses_by_period: dict[tuple, list] = {}
        for pickup, return_time, status in sample_rows:
            statuses_by_period.setdefault((pickup, return_time), []).append(status)
        available_periods = set(self.session.execute(select(
            ModelSearchSample.pickup_time,
            ModelSearchSample.return_time,
        ).join(
            ModelSearchOffer,
            ModelSearchOffer.sample_id == ModelSearchSample.id,
        ).where(
            ModelSearchSample.run_id == run_id,
            ModelSearchOffer.vehicle_model_id.in_(model_ids),
            ModelSearchOffer.book_flag.is_(True),
        ).distinct()).all())
        periods = []
        for period, statuses in statuses_by_period.items():
            state = (
                "AVAILABLE" if period in available_periods
                else "NOT_FOUND" if all(
                    status == ModelSearchSampleStatus.COMPLETED for status in statuses)
                else "INCOMPLETE"
            )
            if availability == "ALL" or availability == state:
                periods.append(period)
        total = len(periods)
        pages = max(1, ceil(total / page_size))
        actual_page = min(max(1, page), pages)
        selected = periods[(actual_page - 1) * page_size:actual_page * page_size]
        items = [self._period(run, period, model_rows, model_ids) for period in selected]
        return {
            "items": items,
            "pagination": {
                "page": actual_page,
                "page_size": page_size,
                "total": total,
                "pages": pages,
            },
        }

    def options(self, *, q: str = "", limit: int = 20) -> list[dict]:
        statement = select(
            VehicleModel.name,
            func.count(VehicleModel.id),
            func.max(VehicleModel.last_seen_at),
        ).group_by(VehicleModel.name)
        normalized = q.strip()
        if normalized:
            statement = statement.where(VehicleModel.name.ilike(f"%{normalized}%"))
        rows = self.session.execute(statement.order_by(
            func.max(VehicleModel.last_seen_at).desc().nullslast(),
            VehicleModel.name,
        ).limit(limit))
        return [{
            "model_name": row[0],
            "variant_count": int(row[1]),
            "last_seen_at": row[2].isoformat() if row[2] else None,
        } for row in rows]

    def cross_city_results(self, run_id: UUID) -> dict:
        run = self._run(run_id)
        if run.search_kind != "CROSS_CITY":
            raise ValueError("该任务不是跨城找车任务")
        target_ids = list(self.session.scalars(select(
            ModelSearchTarget.vehicle_model_id).where(
                ModelSearchTarget.run_id == run.id)))
        cities = list(self.session.scalars(select(City).where(
            City.id.in_(list(run.pickup_city_ids or []))).order_by(City.name)))
        items = [self._cross_city_item(run, city, target_ids) for city in cities]
        order = {"AVAILABLE": 0, "INCOMPLETE": 1, "NOT_FOUND": 2}
        items.sort(key=lambda item: (
            order[item["availability"]],
            item["estimated_trip_total"] is None,
            item["estimated_trip_total"] or 0,
            item["city_name"],
        ))
        return {
            "run_id": str(run.id),
            "status": run.status.value if hasattr(run.status, "value") else str(run.status),
            "return_location_name": run.return_location_name,
            "items": items,
        }

    def _cross_city_item(
        self,
        run: ModelSearchRun,
        city,
        target_ids: list[int],
    ) -> dict:
        samples = list(self.session.scalars(select(ModelSearchSample).join(
            Department,
            Department.id == ModelSearchSample.anchor_department_id,
        ).where(
            ModelSearchSample.run_id == run.id,
            Department.city_id == city.id,
        ).order_by(ModelSearchSample.pickup_time, ModelSearchSample.return_time)))
        periods: dict[tuple, list[ModelSearchSample]] = {}
        for sample in samples:
            periods.setdefault((sample.pickup_time, sample.return_time), []).append(sample)
        windows = [self._cross_city_window(
            run, period, period_samples, target_ids, city.id)
            for period, period_samples in periods.items()]
        available_windows = [item for item in windows if item["availability"] == "AVAILABLE"]
        if available_windows:
            availability = "AVAILABLE"
        elif samples and all(
                sample.status == ModelSearchSampleStatus.COMPLETED for sample in samples):
            availability = "NOT_FOUND"
        else:
            availability = "INCOMPLETE"
        rates = [item["best_rate"] for item in available_windows
                 if item["best_rate"] is not None]
        best_rate = min(rates) if rates else None
        estimated_rental_total = round(best_rate * 14, 2) if best_rate is not None else None
        raw_rail_cost = dict(run.rail_costs or {}).get(str(city.id))
        rail_cost = float(raw_rail_cost) if raw_rail_cost is not None else None
        estimated_trip_total = (
            round(estimated_rental_total + rail_cost, 2)
            if estimated_rental_total is not None and rail_cost is not None else None
        )
        return {
            "city_id": city.id,
            "zuche_city_id": city.zuche_city_id,
            "city_name": city.name,
            "availability": availability,
            "planned_sample_count": len(samples),
            "completed_sample_count": sum(
                sample.status == ModelSearchSampleStatus.COMPLETED for sample in samples),
            "failed_sample_count": sum(
                sample.status == ModelSearchSampleStatus.FAILED for sample in samples),
            "rail_cost": rail_cost,
            "best_rate": best_rate,
            "estimated_rental_total": estimated_rental_total,
            "estimated_trip_total": estimated_trip_total,
            "windows": windows,
        }

    def _cross_city_window(
        self,
        run: ModelSearchRun,
        period: tuple,
        samples: list[ModelSearchSample],
        target_ids: list[int],
        city_id: int,
    ) -> dict:
        pickup, return_time = period
        sample_ids = [sample.id for sample in samples]
        rows = list(self.session.execute(select(
            ModelSearchOffer,
            Department,
        ).join(
            Department, Department.id == ModelSearchOffer.department_id,
        ).where(
            ModelSearchOffer.run_id == run.id,
            ModelSearchOffer.sample_id.in_(sample_ids),
            ModelSearchOffer.vehicle_model_id.in_(target_ids),
            ModelSearchOffer.book_flag.is_(True),
            Department.city_id == city_id,
        ).order_by(
            ModelSearchOffer.package_price.asc().nullslast(),
            ModelSearchOffer.daily_price.asc().nullslast(),
            Department.name,
        )))
        departments: dict[int, dict] = {}
        for offer, department in rows:
            rate = offer.package_price if offer.package_price is not None else offer.daily_price
            candidate = {
                "department_id": department.zuche_dept_id,
                "name": department.name,
                "address": department.address,
                "daily_price": _number(offer.daily_price),
                "package_price": _number(offer.package_price),
                "estimated_rental_total": (
                    round(float(rate) * 14, 2) if rate is not None else None),
                "verified_at": offer.verified_at.isoformat(),
            }
            previous = departments.get(department.id)
            previous_rate = None if previous is None else (
                previous["package_price"]
                if previous["package_price"] is not None else previous["daily_price"])
            if previous is None or (
                    rate is not None and (previous_rate is None or float(rate) < previous_rate)):
                departments[department.id] = candidate
        values = list(departments.values())
        rates = [item["package_price"] if item["package_price"] is not None
                 else item["daily_price"] for item in values]
        rates = [rate for rate in rates if rate is not None]
        if values:
            availability = "AVAILABLE"
        elif all(sample.status == ModelSearchSampleStatus.COMPLETED for sample in samples):
            availability = "NOT_FOUND"
        else:
            availability = "INCOMPLETE"
        return {
            "pickup_time": pickup.isoformat(),
            "return_time": return_time.isoformat(),
            "duration_hours": int((return_time - pickup).total_seconds() // 3600),
            "availability": availability,
            "sample_count": len(samples),
            "completed_sample_count": sum(
                sample.status == ModelSearchSampleStatus.COMPLETED for sample in samples),
            "failed_sample_count": sum(
                sample.status == ModelSearchSampleStatus.FAILED for sample in samples),
            "best_rate": min(rates) if rates else None,
            "departments": values,
        }

    def _name_summary(self, run: ModelSearchRun, name: str) -> dict:
        target_ids = select(ModelSearchTarget.vehicle_model_id).where(
            ModelSearchTarget.run_id == run.id,
            ModelSearchTarget.requested_name == name,
        )
        price = func.coalesce(ModelSearchOffer.package_price, ModelSearchOffer.daily_price)
        stats = self.session.execute(select(
            func.count(func.distinct(ModelSearchOffer.vehicle_model_id)),
            func.count(func.distinct(ModelSearchOffer.department_id)),
            func.avg(price),
            func.min(price),
            func.max(price),
            func.min(ModelSearchOffer.distance_from_yuzhu_km),
            func.max(ModelSearchOffer.verified_at),
        ).where(
            ModelSearchOffer.run_id == run.id,
            ModelSearchOffer.vehicle_model_id.in_(target_ids),
            ModelSearchOffer.book_flag.is_(True),
        )).one()
        available = int(stats[0] or 0) > 0
        state = (
            "AVAILABLE" if available
            else "NOT_FOUND" if run.status in FINAL_COMPLETE
            else "INCOMPLETE"
        )
        variant_total = int(self.session.scalar(select(func.count()).select_from(
            ModelSearchTarget).where(
                ModelSearchTarget.run_id == run.id,
                ModelSearchTarget.requested_name == name,
            )) or 0)
        return {
            "model_name": name,
            "availability": state,
            "variant_count": variant_total,
            "found_variant_count": int(stats[0] or 0),
            "available_department_count": int(stats[1] or 0),
            "average_price": _number(stats[2]),
            "minimum_price": _number(stats[3]),
            "maximum_price": _number(stats[4]),
            "nearest_distance_km": _number(stats[5]),
            "last_verified_at": stats[6].isoformat() if stats[6] else None,
        }

    def _period(self, run, period, model_rows, model_ids) -> dict:
        pickup, return_time = period
        samples = list(self.session.scalars(select(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id,
            ModelSearchSample.pickup_time == pickup,
            ModelSearchSample.return_time == return_time,
        )))
        sample_ids = [sample.id for sample in samples]
        offer_rows = list(self.session.execute(select(
            ModelSearchOffer,
            VehicleModel,
            Department,
        ).join(
            VehicleModel, VehicleModel.id == ModelSearchOffer.vehicle_model_id,
        ).join(
            Department, Department.id == ModelSearchOffer.department_id,
        ).where(
            ModelSearchOffer.sample_id.in_(sample_ids),
            ModelSearchOffer.vehicle_model_id.in_(model_ids),
            ModelSearchOffer.book_flag.is_(True),
        ).order_by(
            VehicleModel.zuche_model_id,
            ModelSearchOffer.distance_from_yuzhu_km.asc().nullslast(),
            ModelSearchOffer.verified_at.desc(),
        )))
        by_model: dict[int, dict[int, dict]] = {}
        for offer, model, department in offer_rows:
            departments = by_model.setdefault(model.id, {})
            if department.id in departments:
                continue
            departments[department.id] = {
                "department_id": department.zuche_dept_id,
                "name": department.name,
                "address": department.address,
                "distance_from_yuzhu_km": _number(offer.distance_from_yuzhu_km),
                "daily_price": _number(offer.daily_price),
                "package_price": _number(offer.package_price),
                "verified_at": offer.verified_at.isoformat(),
            }
        variants = []
        for row in model_rows:
            departments = list(by_model.get(row.id, {}).values())
            if not departments:
                continue
            variants.append({
                "zuche_model_id": row.zuche_model_id,
                "model_name": row.name,
                "description": row.latest_description,
                "energy_type": row.energy_type,
                "personal_state": row.state.value if row.state else "UNTRIED",
                "departments": departments,
            })
        has_offer = bool(variants)
        all_completed = bool(samples) and all(
            sample.status == ModelSearchSampleStatus.COMPLETED for sample in samples)
        return {
            "pickup_time": pickup.isoformat(),
            "return_time": return_time.isoformat(),
            "duration_hours": int((return_time - pickup).total_seconds() // 3600),
            "availability": (
                "AVAILABLE" if has_offer else "NOT_FOUND" if all_completed else "INCOMPLETE"
            ),
            "sample_count": len(samples),
            "completed_sample_count": sum(
                sample.status == ModelSearchSampleStatus.COMPLETED for sample in samples),
            "failed_sample_count": sum(
                sample.status == ModelSearchSampleStatus.FAILED for sample in samples),
            "variants": variants,
        }

    def _run(self, run_id: UUID) -> ModelSearchRun:
        run = self.session.get(ModelSearchRun, run_id)
        if run is None:
            raise ValueError("按车型找车任务不存在")
        return run


def _number(value):
    return float(value) if value is not None else None
