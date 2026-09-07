import gzip
import json
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain import ParsedScan, ScanQuery
from app.models import (AvailabilitySnapshot, ChangeEvent, City, Department, DepartmentActivityState,
                        ModelGroupMembership, RawPayload, ScanRun, ScanStatus, VehicleModel)
from app.repositories.departments import DepartmentRepository


class ScanRepository:
    """扫描聚合的唯一写入边界。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(self, query: ScanQuery, trigger: str) -> ScanRun:
        run = ScanRun(trigger=trigger, status=ScanStatus.PENDING, zuche_city_id=query.city_id,
                      location_name=query.location_name,
                      latitude=query.latitude, longitude=query.longitude, pickup_time=query.pickup_time,
                      return_time=query.return_time)
        self.session.add(run)
        self.session.flush()
        return run

    def persist_success(self, run: ScanRun, raw: dict, parsed: ParsedScan) -> list[ChangeEvent]:
        previous = self._previous_snapshots(run)
        historical_model_ids = self._historical_model_ids(run)
        self.create_raw_payload(datetime.now(UTC), gzip.compress(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()), run.id)

        city = self.session.scalar(select(City).where(City.zuche_city_id == run.zuche_city_id))
        departments = {x.department_id: self._upsert_department(x, city) for x in parsed.departments}
        models: dict[int, VehicleModel] = {}
        snapshots: list[AvailabilitySnapshot] = []
        for offer in parsed.offers:
            department = departments.get(offer.department_id)
            if department is None:
                continue
            model = models.get(offer.model_id) or self.upsert_vehicle_model(offer.model_id, offer.model_name)
            models[offer.model_id] = model
            snapshot = AvailabilitySnapshot(scan_run_id=run.id, department_id=department.id, vehicle_model_id=model.id,
                daily_price=offer.daily_price, package_price=offer.package_price, model_desc=offer.model_desc,
                book_flag=offer.bookable, inventory_type=offer.inventory_type, department_distance=offer.distance_text)
            snapshot.department_distance_km = offer.distance_km
            self.session.add(snapshot)
            snapshots.append(snapshot)
        self.session.flush()
        for group in parsed.groups:
            model = models.get(group.model_id)
            if model:
                self.session.add(ModelGroupMembership(scan_run_id=run.id, vehicle_model_id=model.id,
                                                      group_id=group.group_id, group_name=group.group_name))
        events = self._events(run, snapshots, previous, historical_model_ids)
        run.status, run.completed_at = ScanStatus.SUCCESS, datetime.now(UTC)
        self.session.flush()
        return events

    def mark_failed(self, run: ScanRun, error_code: str, error_message: str) -> None:
        run.status, run.completed_at = ScanStatus.FAILED, datetime.now(UTC)
        run.error_code, run.error_message = error_code[:64], error_message[:1000]
        self.session.flush()

    def upsert_vehicle_model(self, model_id: int, model_name: str) -> VehicleModel:
        item_id = self.session.scalar(
            pg_insert(VehicleModel).values(zuche_model_id=model_id, name=model_name)
            .on_conflict_do_update(index_elements=[VehicleModel.zuche_model_id], set_={"name": model_name})
            .returning(VehicleModel.id)
        )
        item = self.session.get(VehicleModel, item_id)
        self.session.refresh(item)
        return item

    def create_raw_payload(self, captured_at: datetime, payload: bytes, scan_run_id: UUID | None = None) -> RawPayload:
        item = RawPayload(captured_at=captured_at, payload=payload, scan_run_id=scan_run_id)
        self.session.add(item)
        return item

    def purge_expired_raw_payloads(self, cutoff: datetime) -> int:
        return int(self.session.execute(delete(RawPayload).where(RawPayload.captured_at < cutoff)).rowcount or 0)

    def count_vehicle_models(self) -> int: return int(self.session.scalar(select(func.count()).select_from(VehicleModel)) or 0)
    def count_raw_payloads(self) -> int: return int(self.session.scalar(select(func.count()).select_from(RawPayload)) or 0)
    def count_snapshots(self) -> int: return int(self.session.scalar(select(func.count()).select_from(AvailabilitySnapshot)) or 0)
    def get_run_status(self, run_id: UUID) -> str | None:
        value = self.session.scalar(select(ScanRun.status).where(ScanRun.id == run_id))
        return str(value) if value else None

    def _upsert_department(self, source, city: City | None) -> Department:
        return DepartmentRepository(self.session).upsert(
            city,
            {"deptId": source.department_id, "deptName": source.name, "deptAddress": source.address,
             "lat": source.latitude, "lon": source.longitude,
             "business_hours": source.business_hours, "is_open_24h": source.is_open_24h,
             "self_service_pickup": source.self_service_pickup,
             "self_service_return": source.self_service_return},
            source="SCAN",
            active_state=DepartmentActivityState.DISCOVERED,
        )

    def _previous_snapshots(self, run: ScanRun) -> list[AvailabilitySnapshot]:
        runs = self._comparable_runs(run)
        previous = runs[0] if runs else None
        return list(self.session.scalars(select(AvailabilitySnapshot).where(AvailabilitySnapshot.scan_run_id == previous.id))) if previous else []

    def _historical_model_ids(self, run: ScanRun) -> set[int]:
        run_ids = [item.id for item in self._comparable_runs(run)]
        if not run_ids:
            return set()
        return set(self.session.scalars(select(AvailabilitySnapshot.vehicle_model_id).where(
            AvailabilitySnapshot.scan_run_id.in_(run_ids))).all())

    def _comparable_runs(self, run: ScanRun) -> list[ScanRun]:
        statement = select(ScanRun).where(ScanRun.id != run.id, ScanRun.status == ScanStatus.SUCCESS,
            ScanRun.zuche_city_id == run.zuche_city_id,
            ScanRun.location_name == run.location_name, ScanRun.latitude == run.latitude,
            ScanRun.longitude == run.longitude)
        if run.trigger != "SCHEDULED":
            statement = statement.where(ScanRun.pickup_time == run.pickup_time,
                                        ScanRun.return_time == run.return_time)
            return list(self.session.scalars(statement.order_by(ScanRun.completed_at.desc())))
        candidates = list(self.session.scalars(statement.order_by(ScanRun.completed_at.desc())))
        duration = run.return_time - run.pickup_time
        return [item for item in candidates if item.trigger == "SCHEDULED"
                and item.return_time - item.pickup_time == duration
                and self._local_time(item.pickup_time) == self._local_time(run.pickup_time)
                and self._local_time(item.return_time) == self._local_time(run.return_time)]

    def _events(self, run, snapshots, previous, historical_model_ids):
        current_by_model = self._by_model(snapshots)
        previous_by_model = self._by_model(previous)
        events = []
        for model_id, current in current_by_model.items():
            current_best = min(current, key=self._offer_sort_key)
            old = previous_by_model.get(model_id)
            if old is None:
                kind = "REAPPEARED" if model_id in historical_model_ids else "FIRST_SEEN"
                events.append(self._event(run, current_best, kind))
                continue
            old_best = min(old, key=self._offer_sort_key)
            old_price, new_price = self._price(old_best), self._price(current_best)
            if old_price != new_price:
                events.append(self._event(run, current_best, "PRICE_CHANGED",
                    {"before": str(old_price) if old_price is not None else None,
                     "after": str(new_price) if new_price is not None else None}))
            old_nearest, new_nearest = self._nearest_snapshot(old), self._nearest_snapshot(current)
            if old_nearest and new_nearest and self._distance(new_nearest) < self._distance(old_nearest):
                events.append(self._event(run, new_nearest, "CLOSER_DEPARTMENT",
                    {"before_km": self._distance(old_nearest), "after_km": self._distance(new_nearest)}))
        for model_id, old in previous_by_model.items():
            if model_id not in current_by_model:
                events.append(self._event(run, min(old, key=self._offer_sort_key), "DISAPPEARED",
                                          {"previous_scan_id": str(old[0].scan_run_id)}))
        return events

    def _event(self, run, snapshot, kind, detail=None):
        event = ChangeEvent(scan_run_id=run.id, vehicle_model_id=snapshot.vehicle_model_id,
                            department_id=snapshot.department_id, event_type=kind,
                            detail=json.dumps(detail, ensure_ascii=False) if detail else None)
        self.session.add(event); return event

    @staticmethod
    def _by_model(snapshots):
        result = {}
        for snapshot in snapshots:
            result.setdefault(snapshot.vehicle_model_id, []).append(snapshot)
        return result

    @staticmethod
    def _price(snapshot):
        return snapshot.package_price if snapshot.package_price is not None else snapshot.daily_price

    @classmethod
    def _offer_sort_key(cls, snapshot):
        price = cls._price(snapshot)
        return price is None, price or 0, cls._distance(snapshot)

    @staticmethod
    def _distance(snapshot):
        if snapshot.department_distance_km is not None:
            return float(snapshot.department_distance_km)
        try:
            return float((snapshot.department_distance or "").removesuffix("km"))
        except ValueError:
            return float("inf")

    @classmethod
    def _nearest_snapshot(cls, snapshots):
        values = [item for item in snapshots if cls._distance(item) != float("inf")]
        return min(values, key=cls._distance) if values else None

    @staticmethod
    def _local_time(value: datetime):
        if value.tzinfo is not None:
            value = value.astimezone(ZoneInfo("Asia/Shanghai"))
        return value.time().replace(tzinfo=None)
