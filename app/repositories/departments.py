"""神州网点目录和渐进发现任务的事务边界。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    City,
    Department,
    DepartmentActivityState,
    DepartmentDiscoveryPoint,
    DepartmentDiscoveryPointStatus,
    DepartmentDiscoveryRun,
    DepartmentDiscoveryStatus,
)


class InvalidDiscoveryTransition(ValueError):
    """发现任务的状态变更不符合既定状态机。"""


# 必须明显长于 ZucheClient 的 20 秒 HTTP 超时，避免正常慢请求被重复领取。
DISCOVERY_POINT_LEASE_SECONDS = 120


@dataclass(frozen=True, slots=True)
class DiscoveryPointClaim:
    """领取事务关闭后仍可安全用于外呼的不可变快照。"""

    run_id: UUID
    point_id: int
    claim_token: UUID
    city_id: int
    zuche_city_id: str
    city_name: str
    latitude: float
    longitude: float
    pickup_time: datetime
    return_time: datetime


@dataclass(frozen=True, slots=True)
class DiscoveryClaimResult:
    state: str
    claim: DiscoveryPointClaim | None = None


class DepartmentRepository:
    """按神州全局稳定 deptId 原子写入网点的仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, city: City | int | None, payload: dict, *, source: str = "CHOOSE_CAR",
               active_state: DepartmentActivityState = DepartmentActivityState.RECENTLY_SEEN) -> Department:
        dept_id = _required_int(payload.get("deptId"), "deptId")
        city_id = city.id if isinstance(city, City) else city
        now = datetime.now(UTC)
        values = {
            "zuche_dept_id": dept_id,
            "city_id": city_id,
            "name": _text(payload.get("deptName")) or "未命名网点",
            "address": _text(payload.get("deptAddress", payload.get("address"))),
            "latitude": _coordinate(payload.get("lat", payload.get("latitude"))),
            "longitude": _coordinate(payload.get("lon", payload.get("longitude"))),
            "district": _text(payload.get("district", payload.get("districtName"))),
            "business_hours": _text(payload.get("businessHours", payload.get("business_hours"))),
            "is_open_24h": _optional_bool(payload.get("is24Hour", payload.get("is_open_24h"))),
            "self_service_pickup": _optional_bool(payload.get("selfServicePickup", payload.get("self_service_pickup"))),
            "self_service_return": _optional_bool(payload.get("selfServiceReturn", payload.get("self_service_return"))),
            "first_seen_at": now,
            "last_seen_at": now,
            "last_synced_at": now,
            "discovery_source": source,
            "active_state": active_state,
        }
        statement = _insert_for_session(self.session, Department).values(**values)
        update_values = {
            key: value for key, value in values.items()
            if key not in {"zuche_dept_id", "first_seen_at"} and value is not None
        }
        statement = statement.on_conflict_do_update(
            index_elements=[Department.zuche_dept_id], set_=update_values)
        if self.session.bind.dialect.insert_returning:
            item_id = self.session.scalar(statement.returning(Department.id))
        else:
            self.session.execute(statement)
            item_id = self.session.scalar(select(Department.id).where(Department.zuche_dept_id == dept_id))
        item = self.session.get(Department, item_id)
        if item is None:  # pragma: no cover - returning is supported by supported database versions
            raise RuntimeError("网点写入后无法读取")
        self.session.refresh(item)
        return item

    def existing_zuche_ids(self, department_ids: set[int]) -> set[int]:
        if not department_ids:
            return set()
        return set(self.session.scalars(select(Department.zuche_dept_id).where(
            Department.zuche_dept_id.in_(department_ids))))


class DepartmentDiscoveryRepository:
    """保存可停止、可恢复但绝不自动续跑的发现任务。"""

    _ACTIVE_STATUSES = (DepartmentDiscoveryStatus.PENDING, DepartmentDiscoveryStatus.RUNNING)
    _RESUMABLE_STATUSES = (DepartmentDiscoveryStatus.PENDING, DepartmentDiscoveryStatus.STOPPED,
                           DepartmentDiscoveryStatus.INTERRUPTED)

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(self, *, city_id: int, preset: str, radius_km: float, spacing_km: float,
                   max_requests: int, pickup_time: datetime, return_time: datetime) -> DepartmentDiscoveryRun:
        _require_positive("覆盖半径", radius_km)
        _require_positive("点位间距", spacing_km)
        _require_positive("请求上限", max_requests)
        _require_aware_period(pickup_time, return_time)
        self._acquire_city_lock(city_id)
        if self.session.scalar(select(DepartmentDiscoveryRun.id).where(
                DepartmentDiscoveryRun.city_id == city_id,
                DepartmentDiscoveryRun.status.in_(self._ACTIVE_STATUSES)).limit(1)) is not None:
            raise InvalidDiscoveryTransition("该城市已有活动发现任务，请先停止或等待其结束")
        item = DepartmentDiscoveryRun(
            city_id=city_id,
            preset=preset,
            radius_km=radius_km,
            spacing_km=spacing_km,
            max_requests=max_requests,
            pickup_time=pickup_time,
            return_time=return_time,
        )
        try:
            with self.session.begin_nested():
                self.session.add(item)
                self.session.flush()
        except IntegrityError as error:
            raise InvalidDiscoveryTransition("该城市已有活动发现任务，请先停止或等待其结束") from error
        return item

    # 与设计初稿的调用名兼容；后续服务统一使用 create_run。
    create = create_run

    def get_run(self, run_id: UUID) -> DepartmentDiscoveryRun | None:
        return self.session.get(DepartmentDiscoveryRun, run_id)

    def get_run_for_update(self, run_id: UUID) -> DepartmentDiscoveryRun | None:
        return self.session.scalar(select(DepartmentDiscoveryRun).where(
            DepartmentDiscoveryRun.id == run_id).with_for_update())

    def claim_next_point(
        self, run_id: UUID, *, now: datetime | None = None,
    ) -> DiscoveryClaimResult:
        """串行领取一个点；事务提交后调用方必须关闭本会话再外呼。"""
        attempt_started_at = now or datetime.now(UTC)
        statement = select(DepartmentDiscoveryRun).where(
            DepartmentDiscoveryRun.id == run_id).with_for_update()
        run = self.session.scalar(statement)
        if run is None:
            return DiscoveryClaimResult("NOT_FOUND")
        if run.status != DepartmentDiscoveryStatus.RUNNING:
            return DiscoveryClaimResult("INACTIVE")
        lease_cutoff = attempt_started_at - timedelta(seconds=DISCOVERY_POINT_LEASE_SECONDS)
        expired_points = list(self.session.scalars(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.RUNNING,
            or_(
                DepartmentDiscoveryPoint.last_scanned_at.is_(None),
                DepartmentDiscoveryPoint.last_scanned_at <= lease_cutoff,
            ),
        ).with_for_update()))
        if expired_points and run.request_count >= run.max_requests:
            summary = "发现点领取超时且请求额度已用完"
            for point in expired_points:
                point.status = DepartmentDiscoveryPointStatus.FAILED
                point.claim_token = None
                point.last_scanned_at = attempt_started_at
                point.error_summary = summary
            run.completed_point_count += len(expired_points)
            run.last_error_summary = summary
            self._complete(run, DepartmentDiscoveryStatus.COMPLETED_LIMIT)
            self.session.flush()
            return DiscoveryClaimResult("LIMIT")
        for point in expired_points:
            point.status = DepartmentDiscoveryPointStatus.PENDING
            point.claim_token = None

        running_point_id = self.session.scalar(select(DepartmentDiscoveryPoint.id).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.RUNNING,
        ).limit(1))
        if running_point_id is not None:
            return DiscoveryClaimResult("BUSY")
        if run.request_count >= run.max_requests:
            self._complete(run, DepartmentDiscoveryStatus.COMPLETED_LIMIT)
            return DiscoveryClaimResult("LIMIT")

        point = self._next_pending_point(run.id, now=attempt_started_at)
        if point is None:
            self._complete(run, DepartmentDiscoveryStatus.COMPLETED_NO_NEW)
            return DiscoveryClaimResult("EMPTY")
        # 领取即保守占用一次匿名请求额度；后续任何失败都不退还。
        run.request_count += 1
        city = self.session.get(City, run.city_id)
        if city is None:
            point.status = DepartmentDiscoveryPointStatus.FAILED
            point.error_summary = "发现任务关联城市不存在"
            run.completed_point_count += 1
            run.status = DepartmentDiscoveryStatus.FAILED
            run.completed_at = datetime.now(UTC)
            run.last_error_summary = point.error_summary
            self.session.flush()
            return DiscoveryClaimResult("FAILED")
        self.session.flush()
        return DiscoveryClaimResult("CLAIMED", DiscoveryPointClaim(
            run_id=run.id,
            point_id=point.id,
            claim_token=point.claim_token,
            city_id=city.id,
            zuche_city_id=city.zuche_city_id,
            city_name=city.name,
            latitude=float(point.latitude),
            longitude=float(point.longitude),
            pickup_time=run.pickup_time,
            return_time=run.return_time,
        ))

    def add_point(self, run_id: UUID, *, latitude: float, longitude: float, source: str,
                  round_number: int = 1) -> DepartmentDiscoveryPoint:
        values = {
            "run_id": run_id,
            "latitude": _quantized_coordinate(latitude),
            "longitude": _quantized_coordinate(longitude),
            "source": source,
            "round_number": round_number,
        }
        statement = _insert_for_session(self.session, DepartmentDiscoveryPoint).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=[DepartmentDiscoveryPoint.run_id, DepartmentDiscoveryPoint.latitude,
                            DepartmentDiscoveryPoint.longitude])
        if self.session.bind.dialect.insert_returning:
            point_id = self.session.scalar(statement.returning(DepartmentDiscoveryPoint.id))
        else:
            self.session.execute(statement)
            point_id = None
        if point_id is None:
            point_id = self.session.scalar(select(DepartmentDiscoveryPoint.id).where(
                DepartmentDiscoveryPoint.run_id == run_id,
                DepartmentDiscoveryPoint.latitude == values["latitude"],
                DepartmentDiscoveryPoint.longitude == values["longitude"],
            ))
        point = self.session.get(DepartmentDiscoveryPoint, point_id)
        if point is None:  # pragma: no cover - conflict row is visible after the insert resolves
            raise RuntimeError("发现点写入后无法读取")
        return point

    def next_pending_point(
        self, run_id: UUID, *, now: datetime | None = None,
    ) -> DepartmentDiscoveryPoint | None:
        result = self.claim_next_point(run_id, now=now)
        if result.claim is None:
            return None
        return self.session.get(DepartmentDiscoveryPoint, result.claim.point_id)

    def reserve_retry(
        self, point_id: int, *, claim_token: UUID, now: datetime | None = None,
    ) -> str:
        """为同一点的下一次真实外呼原子预占额度。"""
        point = self.session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.id == point_id).with_for_update())
        if point is None or point.claim_token != claim_token:
            return "STALE"
        run = self.session.scalar(select(DepartmentDiscoveryRun).where(
            DepartmentDiscoveryRun.id == point.run_id).with_for_update())
        if run is None:
            return "STALE"
        if (point.status != DepartmentDiscoveryPointStatus.RUNNING
                or run.status != DepartmentDiscoveryStatus.RUNNING):
            return "INACTIVE"
        if run.request_count >= run.max_requests:
            return "LIMIT"
        run.request_count += 1
        point.attempt_count += 1
        point.last_scanned_at = now or datetime.now(UTC)
        self.session.flush()
        return "RESERVED"

    def _next_pending_point(
        self, run_id: UUID, *, now: datetime,
    ) -> DepartmentDiscoveryPoint | None:
        statement = select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run_id,
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.PENDING,
        ).order_by(DepartmentDiscoveryPoint.round_number, DepartmentDiscoveryPoint.id).limit(1)
        if self.session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        point = self.session.scalar(statement)
        if point is None:
            return None
        point.status = DepartmentDiscoveryPointStatus.RUNNING
        point.claim_token = uuid4()
        point.attempt_count += 1
        # RUNNING 时表示本次领取/请求开始时间，也是异常恢复的租约起点。
        point.last_scanned_at = now or datetime.now(UTC)
        self.session.flush()
        return point

    def set_planned_point_count(self, run: DepartmentDiscoveryRun) -> int:
        count = int(self.session.scalar(select(func.count()).select_from(
            DepartmentDiscoveryPoint).where(DepartmentDiscoveryPoint.run_id == run.id)) or 0)
        run.planned_point_count = count
        self.session.flush()
        return count

    def finalize_point(
        self,
        point_id: int,
        *,
        claim_token: UUID,
        succeeded: bool,
        response_department_count: int = 0,
        new_department_count: int = 0,
        updated_department_count: int = 0,
        error_summary: str | None = None,
        requeue: bool = False,
    ) -> DepartmentDiscoveryRun:
        """按点位 ID 条件落库，避免过期 worker 覆盖已恢复任务。"""
        run_id = self.session.scalar(select(DepartmentDiscoveryPoint.run_id).where(
            DepartmentDiscoveryPoint.id == point_id))
        if run_id is None:
            raise InvalidDiscoveryTransition("发现点不存在")
        run = self.session.scalar(select(DepartmentDiscoveryRun).where(
            DepartmentDiscoveryRun.id == run_id).with_for_update())
        if run is None:
            raise InvalidDiscoveryTransition("发现任务不存在")
        point = self.session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.id == point_id).with_for_update())
        if (point is None
                or point.status != DepartmentDiscoveryPointStatus.RUNNING
                or point.claim_token != claim_token):
            raise InvalidDiscoveryTransition("发现点领取已失效")

        point.last_scanned_at = datetime.now(UTC)
        point.response_department_count = max(0, response_department_count)
        point.new_department_count = max(0, new_department_count)
        point.error_summary = error_summary
        should_requeue = requeue and run.request_count < run.max_requests
        point.claim_token = None
        if should_requeue:
            point.status = DepartmentDiscoveryPointStatus.PENDING
        else:
            point.status = (DepartmentDiscoveryPointStatus.COMPLETED if succeeded
                            else DepartmentDiscoveryPointStatus.FAILED)
            run.completed_point_count += 1
            run.new_department_count += max(0, new_department_count)
            run.updated_department_count += max(0, updated_department_count)
        if error_summary:
            run.last_error_summary = error_summary

        if run.status == DepartmentDiscoveryStatus.RUNNING:
            if run.request_count >= run.max_requests:
                self._complete(run, DepartmentDiscoveryStatus.COMPLETED_LIMIT)
            elif not should_requeue and self.session.scalar(select(DepartmentDiscoveryPoint.id).where(
                    DepartmentDiscoveryPoint.run_id == run.id,
                    DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.PENDING,
                ).limit(1)) is None:
                self._complete(run, DepartmentDiscoveryStatus.COMPLETED_NO_NEW)
        self.session.flush()
        return run

    def mark_running(self, run: DepartmentDiscoveryRun) -> DepartmentDiscoveryRun:
        if run.status == DepartmentDiscoveryStatus.RUNNING:
            return run
        if run.status not in self._RESUMABLE_STATUSES:
            raise InvalidDiscoveryTransition("已完成或失败的发现任务不能恢复")
        self._acquire_city_lock(run.city_id)
        if self.session.scalar(select(DepartmentDiscoveryRun.id).where(
                DepartmentDiscoveryRun.city_id == run.city_id,
                DepartmentDiscoveryRun.id != run.id,
                DepartmentDiscoveryRun.status.in_(self._ACTIVE_STATUSES)).limit(1)) is not None:
            raise InvalidDiscoveryTransition("该城市已有活动发现任务，不能恢复当前任务")
        try:
            with self.session.begin_nested():
                run.status = DepartmentDiscoveryStatus.RUNNING
                if run.started_at is None:
                    run.started_at = datetime.now(UTC)
                self.session.flush()
        except IntegrityError as error:
            raise InvalidDiscoveryTransition("该城市已有活动发现任务，不能恢复当前任务") from error
        return run

    def mark_stopped(self, run: DepartmentDiscoveryRun) -> DepartmentDiscoveryRun:
        if run.status == DepartmentDiscoveryStatus.STOPPED:
            return run
        if run.status != DepartmentDiscoveryStatus.RUNNING:
            raise InvalidDiscoveryTransition("只有运行中的发现任务可以停止")
        run.status = DepartmentDiscoveryStatus.STOPPED
        self.session.flush()
        return run

    def mark_interrupted(self, run: DepartmentDiscoveryRun) -> DepartmentDiscoveryRun:
        if run.status == DepartmentDiscoveryStatus.INTERRUPTED:
            return run
        if run.status != DepartmentDiscoveryStatus.RUNNING:
            raise InvalidDiscoveryTransition("只有运行中的发现任务可以中断")
        run.status = DepartmentDiscoveryStatus.INTERRUPTED
        self.session.flush()
        return run

    def interrupt_stale_runs(self) -> int:
        stale_run_ids = select(DepartmentDiscoveryRun.id).where(
            DepartmentDiscoveryRun.status == DepartmentDiscoveryStatus.RUNNING,
        )
        self.session.execute(update(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id.in_(stale_run_ids),
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.RUNNING,
        ).values(
            status=DepartmentDiscoveryPointStatus.PENDING,
            claim_token=None,
        ).execution_options(
            synchronize_session="fetch"))
        result = self.session.execute(update(DepartmentDiscoveryRun).where(
            DepartmentDiscoveryRun.status == DepartmentDiscoveryStatus.RUNNING,
        ).values(status=DepartmentDiscoveryStatus.INTERRUPTED).execution_options(
            synchronize_session="fetch"))
        return int(result.rowcount or 0)

    @staticmethod
    def _complete(run: DepartmentDiscoveryRun, status: DepartmentDiscoveryStatus) -> None:
        run.status = status
        run.completed_at = datetime.now(UTC)

    def _acquire_city_lock(self, city_id: int) -> None:
        if self.session.bind.dialect.name == "postgresql":
            self.session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                                 {"lock_id": 912_000_000 + city_id})


def _insert_for_session(session: Session, model):
    return postgres_insert(model) if session.bind.dialect.name == "postgresql" else sqlite_insert(model)


def _required_int(value: object, name: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"网点数据缺少有效 {name}") from error


def _coordinate(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return _quantized_coordinate(value)
    except (ArithmeticError, ValueError) as error:
        raise ValueError("网点坐标格式无效") from error


def _quantized_coordinate(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _text(value: object) -> str | None:
    return str(value).strip() if value not in (None, "") else None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是"}
    return bool(value)


def _require_aware_period(pickup_time: datetime, return_time: datetime) -> None:
    if pickup_time.tzinfo is None or pickup_time.utcoffset() is None:
        raise ValueError("取车时间必须带时区")
    if return_time.tzinfo is None or return_time.utcoffset() is None:
        raise ValueError("还车时间必须带时区")
    if return_time <= pickup_time:
        raise ValueError("还车时间必须晚于取车时间")


def _require_positive(name: str, value: float | int) -> None:
    if value <= 0:
        raise ValueError(f"{name}必须大于 0")
