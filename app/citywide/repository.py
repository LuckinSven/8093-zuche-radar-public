"""全城扫描任务、点位领取和报价聚合的事务边界。"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.citywide.domain import CitywideClaimBatch, CitywidePointClaim
from app.departments.planner import haversine_km
from app.models import (
    City,
    CitywideModelSummary,
    CitywideOffer,
    CitywidePointStatus,
    CitywideScanPoint,
    CitywideScanRun,
    CitywideScanStatus,
    Department,
    DepartmentActivityState,
)


YUZHU_COORDINATES = (23.101610, 113.432649)
MAX_CONCURRENCY = 3


class InvalidCitywideTransition(ValueError):
    """全城扫描状态变更不符合既定状态机。"""


class CitywideRepository:
    """通过短事务保存可停止、可恢复的全城扫描。"""

    _ACTIVE_STATUSES = (CitywideScanStatus.PENDING, CitywideScanStatus.RUNNING)

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(
        self,
        *,
        city_id: int,
        pickup_time: datetime,
        return_time: datetime,
    ) -> CitywideScanRun:
        _require_aware_period(pickup_time, return_time)
        self._acquire_city_lock(city_id)
        city = self.session.get(City, city_id)
        if city is None:
            raise ValueError("城市不存在")
        if self.session.scalar(select(CitywideScanRun.id).where(
                CitywideScanRun.city_id == city_id,
                CitywideScanRun.status.in_(self._ACTIVE_STATUSES)).limit(1)) is not None:
            raise InvalidCitywideTransition("该城市已有活动全城扫描任务")

        departments = list(self.session.scalars(select(Department).where(
            Department.city_id == city_id,
            Department.latitude.is_not(None),
            Department.longitude.is_not(None),
            Department.active_state != DepartmentActivityState.MANUALLY_DISABLED,
        ).order_by(Department.id)))
        if not departments:
            raise ValueError("该城市没有可扫描的有效坐标网点")

        run = CitywideScanRun(
            city_id=city_id,
            pickup_time=pickup_time,
            return_time=return_time,
            planned_point_count=len(departments),
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
                self.session.add_all([
                    CitywideScanPoint(run_id=run.id, department_id=department.id)
                    for department in departments
                ])
                self.session.flush()
        except IntegrityError as error:
            raise InvalidCitywideTransition("该城市已有活动全城扫描任务") from error
        return run

    def get_run(self, run_id: UUID) -> CitywideScanRun | None:
        return self.session.get(CitywideScanRun, run_id)

    def active_run_id(self) -> UUID | None:
        return self.session.scalar(select(CitywideScanRun.id).where(
            CitywideScanRun.status.in_(self._ACTIVE_STATUSES),
        ).order_by(CitywideScanRun.created_at, CitywideScanRun.id).limit(1))

    def claim_points(self, run_id: UUID, *, limit: int = MAX_CONCURRENCY) -> CitywideClaimBatch:
        claim_limit = min(MAX_CONCURRENCY, max(1, int(limit)))
        run = self.session.scalar(select(CitywideScanRun).where(
            CitywideScanRun.id == run_id).with_for_update())
        if run is None:
            return CitywideClaimBatch("NOT_FOUND")
        if run.status == CitywideScanStatus.PENDING:
            run.status = CitywideScanStatus.RUNNING
            run.started_at = datetime.now(UTC)
        elif run.status != CitywideScanStatus.RUNNING:
            return CitywideClaimBatch("INACTIVE")

        statement = select(CitywideScanPoint).where(
            CitywideScanPoint.run_id == run.id,
            CitywideScanPoint.status == CitywidePointStatus.PENDING,
        ).order_by(CitywideScanPoint.id).limit(claim_limit)
        if self.session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        points = list(self.session.scalars(statement))
        if not points:
            self._finish_if_exhausted(run)
            return CitywideClaimBatch("EMPTY")

        city = self.session.get(City, run.city_id)
        if city is None:
            raise InvalidCitywideTransition("全城扫描关联城市不存在")
        now = datetime.now(UTC)
        claims: list[CitywidePointClaim] = []
        for point in points:
            department = self.session.get(Department, point.department_id)
            if department is None or department.latitude is None or department.longitude is None:
                point.status = CitywidePointStatus.FAILED
                point.error_summary = "扫描网点不存在或缺少坐标"
                point.completed_at = now
                run.failed_point_count += 1
                continue
            point.status = CitywidePointStatus.RUNNING
            point.claim_token = uuid4()
            point.attempt_count += 1
            point.started_at = now
            run.request_count += 1
            claims.append(CitywidePointClaim(
                run_id=run.id,
                point_id=point.id,
                claim_token=point.claim_token,
                city_id=city.id,
                zuche_city_id=city.zuche_city_id,
                anchor_department_id=department.id,
                anchor_zuche_dept_id=department.zuche_dept_id,
                anchor_name=department.name,
                latitude=float(department.latitude),
                longitude=float(department.longitude),
                pickup_time=run.pickup_time,
                return_time=run.return_time,
            ))
        self.session.flush()
        return CitywideClaimBatch("CLAIMED" if claims else "EMPTY", tuple(claims))

    def complete_point(
        self,
        point_id: int,
        *,
        claim_token: UUID,
        response_department_count: int,
        response_model_count: int,
    ) -> CitywideScanRun:
        run, point = self._locked_claim(point_id, claim_token)
        point.status = CitywidePointStatus.COMPLETED
        point.claim_token = None
        point.response_department_count = max(0, response_department_count)
        point.response_model_count = max(0, response_model_count)
        point.completed_at = datetime.now(UTC)
        point.error_summary = None
        run.completed_point_count += 1
        self._finish_if_exhausted(run)
        self.session.flush()
        return run

    def fail_point(
        self,
        point_id: int,
        *,
        claim_token: UUID,
        error_summary: str,
        retryable: bool,
    ) -> CitywideScanRun:
        run, point = self._locked_claim(point_id, claim_token)
        point.claim_token = None
        point.error_summary = error_summary
        run.last_error_summary = error_summary
        if retryable and point.attempt_count < 3 and run.status == CitywideScanStatus.RUNNING:
            point.status = CitywidePointStatus.PENDING
        else:
            point.status = CitywidePointStatus.FAILED
            point.completed_at = datetime.now(UTC)
            run.failed_point_count += 1
        self._finish_if_exhausted(run)
        self.session.flush()
        return run

    def stop(self, run_id: UUID) -> CitywideScanRun:
        run = self._locked_run(run_id)
        if run.status != CitywideScanStatus.RUNNING:
            raise InvalidCitywideTransition("只有运行中的全城扫描可以停止")
        run.status = CitywideScanStatus.STOPPED
        run.stopped_at = datetime.now(UTC)
        self.session.flush()
        return run

    def resume(self, run_id: UUID) -> CitywideScanRun:
        run = self._locked_run(run_id)
        if run.status not in (CitywideScanStatus.STOPPED, CitywideScanStatus.INTERRUPTED):
            raise InvalidCitywideTransition("只有已停止或已中断的全城扫描可以继续")
        self._acquire_city_lock(run.city_id)
        other = self.session.scalar(select(CitywideScanRun.id).where(
            CitywideScanRun.city_id == run.city_id,
            CitywideScanRun.id != run.id,
            CitywideScanRun.status.in_(self._ACTIVE_STATUSES),
        ).limit(1))
        if other is not None:
            raise InvalidCitywideTransition("该城市已有活动全城扫描任务，不能继续")
        run.status = CitywideScanStatus.RUNNING
        run.stopped_at = None
        run.completed_at = None
        self.session.flush()
        return run

    def retry_failed(self, run_id: UUID) -> CitywideScanRun:
        run = self._locked_run(run_id)
        if run.status != CitywideScanStatus.PARTIAL:
            raise InvalidCitywideTransition("只有部分完成的全城扫描可以重试失败点")
        self._acquire_city_lock(run.city_id)
        self.session.execute(update(CitywideScanPoint).where(
            CitywideScanPoint.run_id == run.id,
            CitywideScanPoint.status == CitywidePointStatus.FAILED,
        ).values(
            status=CitywidePointStatus.PENDING,
            claim_token=None,
            completed_at=None,
        ).execution_options(synchronize_session="fetch"))
        run.status = CitywideScanStatus.RUNNING
        run.failed_point_count = 0
        run.completed_at = None
        self.session.flush()
        return run

    def interrupt_stale_runs(self) -> int:
        stale_ids = select(CitywideScanRun.id).where(
            CitywideScanRun.status.in_(self._ACTIVE_STATUSES))
        self.session.execute(update(CitywideScanPoint).where(
            CitywideScanPoint.run_id.in_(stale_ids),
            CitywideScanPoint.status == CitywidePointStatus.RUNNING,
        ).values(
            status=CitywidePointStatus.PENDING,
            claim_token=None,
        ).execution_options(synchronize_session="fetch"))
        result = self.session.execute(update(CitywideScanRun).where(
            CitywideScanRun.status.in_(self._ACTIVE_STATUSES),
        ).values(status=CitywideScanStatus.INTERRUPTED).execution_options(
            synchronize_session="fetch"))
        self.session.flush()
        return int(result.rowcount or 0)

    def upsert_offer(
        self,
        *,
        run_id: UUID,
        vehicle_model_id: int,
        department_id: int,
        source_point_id: int,
        source_is_self: bool,
        source_distance_km: float | Decimal | None,
        daily_price: float | Decimal | None = None,
        package_price: float | Decimal | None = None,
        book_flag: bool = False,
        inventory_type: int | None = None,
        model_description: str | None = None,
    ) -> CitywideOffer:
        now = datetime.now(UTC)
        distance_from_yuzhu = self._distance_from_yuzhu(department_id)
        values = {
            "run_id": run_id,
            "vehicle_model_id": vehicle_model_id,
            "department_id": department_id,
            "source_point_id": source_point_id,
            "source_is_self": source_is_self,
            "source_distance_km": source_distance_km,
            "distance_from_yuzhu_km": distance_from_yuzhu,
            "daily_price": daily_price,
            "package_price": package_price,
            "book_flag": book_flag,
            "inventory_type": inventory_type,
            "model_description": model_description,
            "first_observed_at": now,
            "last_observed_at": now,
        }
        statement = _insert_for_session(self.session, CitywideOffer).values(**values)
        excluded = statement.excluded
        existing = CitywideOffer.__table__.c
        better_source = (
            (excluded.source_is_self.is_(True) & existing.source_is_self.is_(False))
            | (
                excluded.source_is_self.is_(False)
                & existing.source_is_self.is_(False)
                & (
                    func.coalesce(excluded.source_distance_km, 1_000_000)
                    < func.coalesce(existing.source_distance_km, 1_000_000)
                )
            )
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                CitywideOffer.run_id,
                CitywideOffer.vehicle_model_id,
                CitywideOffer.department_id,
            ],
            set_={
                "source_point_id": excluded.source_point_id,
                "source_is_self": excluded.source_is_self,
                "source_distance_km": excluded.source_distance_km,
                "distance_from_yuzhu_km": excluded.distance_from_yuzhu_km,
                "daily_price": excluded.daily_price,
                "package_price": excluded.package_price,
                "book_flag": excluded.book_flag,
                "inventory_type": excluded.inventory_type,
                "model_description": excluded.model_description,
                "last_observed_at": excluded.last_observed_at,
            },
            where=better_source,
        )
        self.session.execute(statement)
        self._refresh_model_summary(run_id, vehicle_model_id)
        offer = self.session.scalar(select(CitywideOffer).where(
            CitywideOffer.run_id == run_id,
            CitywideOffer.vehicle_model_id == vehicle_model_id,
            CitywideOffer.department_id == department_id,
        ))
        if offer is None:  # pragma: no cover - insert and lookup share one transaction
            raise RuntimeError("全城报价写入后无法读取")
        return offer

    def _refresh_model_summary(self, run_id: UUID, vehicle_model_id: int) -> None:
        price = func.coalesce(CitywideOffer.package_price, CitywideOffer.daily_price)
        stats = self.session.execute(select(
            func.count(CitywideOffer.id),
            func.avg(price),
            func.min(price),
            func.max(price),
            func.min(CitywideOffer.distance_from_yuzhu_km),
            func.min(CitywideOffer.first_observed_at),
            func.max(CitywideOffer.last_observed_at),
        ).where(
            CitywideOffer.run_id == run_id,
            CitywideOffer.vehicle_model_id == vehicle_model_id,
        )).one()
        nearest_department_id = self.session.scalar(select(CitywideOffer.department_id).where(
            CitywideOffer.run_id == run_id,
            CitywideOffer.vehicle_model_id == vehicle_model_id,
            CitywideOffer.distance_from_yuzhu_km.is_not(None),
        ).order_by(CitywideOffer.distance_from_yuzhu_km, CitywideOffer.department_id).limit(1))
        values = {
            "run_id": run_id,
            "vehicle_model_id": vehicle_model_id,
            "available_department_count": int(stats[0] or 0),
            "average_price": stats[1],
            "minimum_price": stats[2],
            "maximum_price": stats[3],
            "nearest_distance_km": stats[4],
            "nearest_department_id": nearest_department_id,
            "first_observed_at": stats[5],
            "last_observed_at": stats[6],
        }
        statement = _insert_for_session(self.session, CitywideModelSummary).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[CitywideModelSummary.run_id, CitywideModelSummary.vehicle_model_id],
            set_={key: getattr(statement.excluded, key) for key in values
                  if key not in {"run_id", "vehicle_model_id"}},
        )
        self.session.execute(statement)
        run = self.session.get(CitywideScanRun, run_id)
        if run is not None:
            run.available_model_count = int(self.session.scalar(select(func.count()).select_from(
                CitywideModelSummary).where(CitywideModelSummary.run_id == run_id)) or 0)
        self.session.flush()

    def _locked_run(self, run_id: UUID) -> CitywideScanRun:
        run = self.session.scalar(select(CitywideScanRun).where(
            CitywideScanRun.id == run_id).with_for_update())
        if run is None:
            raise InvalidCitywideTransition("全城扫描任务不存在")
        return run

    def _locked_claim(
        self, point_id: int, claim_token: UUID,
    ) -> tuple[CitywideScanRun, CitywideScanPoint]:
        point = self.session.scalar(select(CitywideScanPoint).where(
            CitywideScanPoint.id == point_id).with_for_update())
        if (point is None or point.status != CitywidePointStatus.RUNNING
                or point.claim_token != claim_token):
            raise InvalidCitywideTransition("扫描点领取已失效")
        run = self._locked_run(point.run_id)
        return run, point

    def _finish_if_exhausted(self, run: CitywideScanRun) -> None:
        if run.status != CitywideScanStatus.RUNNING:
            return
        remaining = self.session.scalar(select(CitywideScanPoint.id).where(
            CitywideScanPoint.run_id == run.id,
            CitywideScanPoint.status.in_((
                CitywidePointStatus.PENDING,
                CitywidePointStatus.RUNNING,
            )),
        ).limit(1))
        if remaining is not None:
            return
        run.status = (
            CitywideScanStatus.COMPLETED
            if run.failed_point_count == 0
            else CitywideScanStatus.PARTIAL
        )
        run.completed_at = datetime.now(UTC)

    def _distance_from_yuzhu(self, department_id: int) -> Decimal | None:
        department = self.session.get(Department, department_id)
        if department is None or department.latitude is None or department.longitude is None:
            return None
        distance = haversine_km(
            YUZHU_COORDINATES,
            (float(department.latitude), float(department.longitude)),
        )
        return Decimal(str(distance)).quantize(Decimal("0.001"))

    def _acquire_city_lock(self, city_id: int) -> None:
        if self.session.bind.dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": 913_000_000 + city_id},
            )


def _insert_for_session(session: Session, model):
    if session.bind.dialect.name == "postgresql":
        return postgres_insert(model)
    return sqlite_insert(model)


def _require_aware_period(pickup_time: datetime, return_time: datetime) -> None:
    if pickup_time.tzinfo is None or pickup_time.utcoffset() is None:
        raise ValueError("取车时间必须带时区")
    if return_time.tzinfo is None or return_time.utcoffset() is None:
        raise ValueError("还车时间必须带时区")
    if return_time <= pickup_time:
        raise ValueError("还车时间必须晚于取车时间")
