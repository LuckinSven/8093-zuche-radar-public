"""按车型找车任务的短事务仓储和状态机。"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.model_search.domain import ModelSearchClaimBatch, ModelSearchSampleClaim
from app.model_search.planner import SHANGHAI, future_weekend_windows, hourly_windows
from app.models import (
    City,
    CitywideOffer,
    Department,
    DepartmentActivityState,
    ModelSearchOffer,
    ModelSearchPhase,
    ModelSearchRun,
    ModelSearchRunStatus,
    ModelSearchSample,
    ModelSearchSampleKind,
    ModelSearchSampleStatus,
    ModelSearchTarget,
    VehicleModel,
)


CACHE_TTL = timedelta(hours=2)
HARD_REQUEST_LIMIT = 5_000
SLOW_REQUEST_THRESHOLD = 2_000
CROSS_CITY_REQUEST_LIMIT = 2_000
REQUEST_VERSION = "choose-car-v3-1"


class InvalidModelSearchTransition(ValueError):
    """按车型找车状态变更不符合状态机。"""


class ModelSearchRepository:
    """创建、领取和推进按车型找车任务。"""

    target_model = ModelSearchTarget
    _ACTIVE = (ModelSearchRunStatus.PENDING, ModelSearchRunStatus.RUNNING)

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(
        self,
        *,
        city_id: int,
        model_names: list[str],
        now: datetime | None = None,
    ) -> ModelSearchRun:
        observed_at = now or datetime.now(UTC)
        names = sorted({name.strip() for name in model_names if name.strip()})
        if not names:
            raise ValueError("请至少选择一款车型库中的车型")
        city = self.session.get(City, city_id)
        if city is None or city.name != "广州":
            raise ValueError("按车型找车当前只支持广州")
        if self.session.scalar(select(ModelSearchRun.id).where(
                ModelSearchRun.city_id == city_id,
                ModelSearchRun.status.in_(self._ACTIVE)).limit(1)) is not None:
            raise InvalidModelSearchTransition("广州已有活动按车型找车任务")

        models = list(self.session.scalars(select(VehicleModel).where(
            VehicleModel.name.in_(names),
        ).order_by(VehicleModel.name, VehicleModel.zuche_model_id)))
        found_names = {item.name for item in models}
        missing = [name for name in names if name not in found_names]
        if missing:
            raise ValueError(f"车型库中不存在：{'、'.join(missing)}")

        departments = list(self.session.scalars(select(Department).where(
            Department.city_id == city_id,
            Department.latitude.is_not(None),
            Department.longitude.is_not(None),
            Department.active_state != DepartmentActivityState.MANUALLY_DISABLED,
        ).order_by(Department.id)))
        if not departments:
            raise ValueError("广州没有可扫描的有效坐标网点")

        model_ids = sorted(item.zuche_model_id for item in models)
        fingerprint = sha256(
            (REQUEST_VERSION + ":" + ",".join(map(str, model_ids))).encode("utf-8")
        ).hexdigest()
        base_windows = future_weekend_windows(observed_at)
        history_candidates = self._historical_candidate_ids(
            city_id, [item.id for item in models])
        estimated = len(departments) * len(base_windows) + len(history_candidates) * 96
        run = ModelSearchRun(
            city_id=city_id,
            search_kind="WEEKEND",
            return_city_id=city_id,
            pickup_city_ids=[city_id],
            rental_windows=[{
                "pickup_time": window.pickup.isoformat(),
                "return_time": window.return_time.isoformat(),
            } for window in base_windows],
            target_fingerprint=fingerprint,
            request_version=REQUEST_VERSION,
            requested_names=names,
            estimated_request_count=estimated,
            planned_sample_count=len(departments) * len(base_windows),
            candidate_department_count=len(history_candidates),
            execution_concurrency=1 if estimated > SLOW_REQUEST_THRESHOLD else 2,
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
                self.session.add_all([
                    ModelSearchTarget(
                        run_id=run.id,
                        vehicle_model_id=model.id,
                        requested_name=model.name,
                        zuche_model_id=model.zuche_model_id,
                    )
                    for model in models
                ])
                self.session.add_all([
                    ModelSearchSample(
                        run_id=run.id,
                        anchor_department_id=department.id,
                        kind=ModelSearchSampleKind.BASE,
                        pickup_time=window.pickup,
                        return_time=window.return_time,
                    )
                    for window in base_windows
                    for department in departments
                ])
                self.session.flush()
        except IntegrityError as error:
            raise InvalidModelSearchTransition(
                "广州已有活动按车型找车任务") from error
        return run

    def create_cross_city_run(
        self,
        *,
        vehicle_model_id: int,
        pickup_city_ids: list[int],
        return_city_id: int,
        return_location_name: str,
        windows: tuple,
        rail_costs: dict[str, int | float],
        now: datetime | None = None,
    ) -> ModelSearchRun:
        observed_at = now or datetime.now(UTC)
        model = self.session.get(VehicleModel, vehicle_model_id)
        if model is None:
            raise ValueError("车型库中不存在所选精确车型")
        return_city = self.session.get(City, return_city_id)
        if return_city is None:
            raise ValueError("还车城市不存在")
        city_ids = sorted({int(item) for item in pickup_city_ids})
        if not city_ids:
            raise ValueError("请至少选择一个取车城市")
        cities = list(self.session.scalars(select(City).where(City.id.in_(city_ids))))
        if len(cities) != len(city_ids):
            raise ValueError("部分取车城市不存在")
        rental_windows = tuple(windows)
        if not rental_windows:
            raise ValueError("请至少提供一组准确租期")
        if any(window.return_time <= window.pickup for window in rental_windows):
            raise ValueError("还车时间必须晚于取车时间")
        if self.session.scalar(select(ModelSearchRun.id).where(
                ModelSearchRun.city_id == return_city_id,
                ModelSearchRun.status.in_(self._ACTIVE)).limit(1)) is not None:
            raise InvalidModelSearchTransition("广州已有活动找车任务")

        departments = list(self.session.scalars(select(Department).where(
            Department.city_id.in_(city_ids),
            Department.latitude.is_not(None),
            Department.longitude.is_not(None),
            Department.active_state != DepartmentActivityState.MANUALLY_DISABLED,
        ).order_by(Department.city_id, Department.id)))
        covered_city_ids = {item.city_id for item in departments}
        missing = [city.name for city in cities if city.id not in covered_city_ids]
        if missing:
            raise ValueError(f"以下城市没有可扫描网点：{'、'.join(sorted(missing))}")
        planned_count = len(departments) * len(rental_windows)
        if planned_count > CROSS_CITY_REQUEST_LIMIT:
            raise ValueError(
                f"预计请求 {planned_count} 次，超过单任务 {CROSS_CITY_REQUEST_LIMIT} 次安全上限")

        fingerprint_source = ":".join((
            REQUEST_VERSION,
            str(model.zuche_model_id),
            ",".join(map(str, city_ids)),
            str(return_city.zuche_city_id),
            ",".join(
                f"{window.pickup.isoformat()}/{window.return_time.isoformat()}"
                for window in rental_windows
            ),
        ))
        run = ModelSearchRun(
            city_id=return_city_id,
            search_kind="CROSS_CITY",
            return_city_id=return_city_id,
            return_location_name=return_location_name.strip() or "广州鱼珠",
            pickup_city_ids=city_ids,
            rental_windows=[{
                "pickup_time": window.pickup.isoformat(),
                "return_time": window.return_time.isoformat(),
            } for window in rental_windows],
            rail_costs={str(key): float(value) for key, value in rail_costs.items()},
            target_fingerprint=sha256(fingerprint_source.encode("utf-8")).hexdigest(),
            request_version=REQUEST_VERSION,
            requested_names=[model.name],
            estimated_request_count=planned_count,
            planned_sample_count=planned_count,
            execution_concurrency=2,
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
                self.session.add(ModelSearchTarget(
                    run_id=run.id,
                    vehicle_model_id=model.id,
                    requested_name=model.name,
                    zuche_model_id=model.zuche_model_id,
                ))
                self.session.add_all([
                    ModelSearchSample(
                        run_id=run.id,
                        anchor_department_id=department.id,
                        kind=ModelSearchSampleKind.BASE,
                        pickup_time=window.pickup,
                        return_time=window.return_time,
                    )
                    for window in rental_windows
                    for department in departments
                ])
                self.session.flush()
        except IntegrityError as error:
            raise InvalidModelSearchTransition("广州已有活动找车任务") from error
        return run

    def active_run_id(self) -> UUID | None:
        return self.session.scalar(select(ModelSearchRun.id).where(
            ModelSearchRun.status.in_(self._ACTIVE),
        ).order_by(ModelSearchRun.created_at, ModelSearchRun.id).limit(1))

    def claim_samples(
        self,
        run_id: UUID,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> ModelSearchClaimBatch:
        observed_at = now or datetime.now(UTC)
        run = self.session.scalar(select(ModelSearchRun).where(
            ModelSearchRun.id == run_id).with_for_update())
        if run is None:
            return ModelSearchClaimBatch("NOT_FOUND")
        if run.status == ModelSearchRunStatus.PENDING:
            run.status = ModelSearchRunStatus.RUNNING
            run.started_at = observed_at
        elif run.status != ModelSearchRunStatus.RUNNING:
            return ModelSearchClaimBatch("INACTIVE")

        claim_limit = min(max(1, int(limit)), run.execution_concurrency)
        statement = select(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id,
            ModelSearchSample.status == ModelSearchSampleStatus.PENDING,
            or_(
                ModelSearchSample.next_attempt_at.is_(None),
                ModelSearchSample.next_attempt_at <= observed_at,
            ),
        ).order_by(ModelSearchSample.pickup_time, ModelSearchSample.anchor_department_id).limit(
            claim_limit)
        if self.session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        samples = list(self.session.scalars(statement))
        if not samples:
            state = self.advance_if_exhausted(run.id, now=observed_at)
            return ModelSearchClaimBatch(state)

        target_rows = list(self.session.scalars(select(ModelSearchTarget).where(
            ModelSearchTarget.run_id == run.id).order_by(ModelSearchTarget.zuche_model_id)))
        target_ids = tuple(item.zuche_model_id for item in target_rows)
        claims: list[ModelSearchSampleClaim] = []
        cache_count = 0
        for sample in samples:
            source = self._recent_cache_source(run, sample, observed_at - CACHE_TTL)
            if source is not None:
                self._reuse_sample(run, sample, source, observed_at)
                cache_count += 1
                continue
            department = self.session.get(Department, sample.anchor_department_id)
            city = self.session.get(City, department.city_id) if department else None
            return_city = self.session.get(City, run.return_city_id or run.city_id)
            if (department is None or city is None or department.latitude is None
                    or department.longitude is None or return_city is None):
                sample.status = ModelSearchSampleStatus.FAILED
                sample.error_summary = "扫描网点不存在或缺少坐标"
                sample.completed_at = observed_at
                run.failed_sample_count += 1
                continue
            sample.status = ModelSearchSampleStatus.RUNNING
            sample.claim_token = uuid4()
            sample.started_at = observed_at
            sample.attempt_count += 1
            run.request_count += 1
            claims.append(ModelSearchSampleClaim(
                run_id=run.id,
                sample_id=sample.id,
                claim_token=sample.claim_token,
                city_id=city.id,
                zuche_city_id=city.zuche_city_id,
                return_zuche_city_id=return_city.zuche_city_id,
                target_model_ids=target_ids,
                target_fingerprint=run.target_fingerprint,
                anchor_department_id=department.id,
                anchor_zuche_dept_id=department.zuche_dept_id,
                anchor_name=department.name,
                latitude=float(department.latitude),
                longitude=float(department.longitude),
                pickup_time=sample.pickup_time,
                return_time=sample.return_time,
            ))
        self.session.flush()
        if claims:
            return ModelSearchClaimBatch("CLAIMED", tuple(claims))
        return ModelSearchClaimBatch("CACHE_ONLY" if cache_count else "EMPTY")

    def add_offer(
        self,
        *,
        run_id: UUID,
        sample_id: int,
        vehicle_model_id: int,
        department_id: int,
        daily_price=None,
        package_price=None,
        book_flag: bool = False,
        inventory_type: int | None = None,
        model_description: str | None = None,
        distance_from_yuzhu_km=None,
        verified_at: datetime | None = None,
    ) -> ModelSearchOffer:
        existing = self.session.scalar(select(ModelSearchOffer).where(
            ModelSearchOffer.sample_id == sample_id,
            ModelSearchOffer.vehicle_model_id == vehicle_model_id,
            ModelSearchOffer.department_id == department_id,
        ))
        if existing is None:
            existing = ModelSearchOffer(
                run_id=run_id,
                sample_id=sample_id,
                vehicle_model_id=vehicle_model_id,
                department_id=department_id,
            )
            self.session.add(existing)
        existing.daily_price = daily_price
        existing.package_price = package_price
        existing.book_flag = book_flag
        existing.inventory_type = inventory_type
        existing.model_description = model_description
        existing.distance_from_yuzhu_km = distance_from_yuzhu_km
        existing.verified_at = verified_at or datetime.now(UTC)
        self.session.flush()
        return existing

    def complete_sample(
        self,
        sample_id: int,
        *,
        claim_token: UUID,
        response_department_count: int,
        response_model_count: int,
        now: datetime | None = None,
    ) -> ModelSearchRun:
        run, sample = self._locked_claim(sample_id, claim_token)
        sample.status = ModelSearchSampleStatus.COMPLETED
        sample.claim_token = None
        sample.next_attempt_at = None
        sample.response_department_count = max(0, response_department_count)
        sample.response_model_count = max(0, response_model_count)
        sample.completed_at = now or datetime.now(UTC)
        sample.error_summary = None
        run.completed_sample_count += 1
        self._refresh_result_counts(run)
        self.session.flush()
        return run

    def fail_sample(
        self,
        sample_id: int,
        *,
        claim_token: UUID,
        error_summary: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> ModelSearchRun:
        observed_at = now or datetime.now(UTC)
        run, sample = self._locked_claim(sample_id, claim_token)
        sample.claim_token = None
        sample.error_summary = error_summary
        run.last_error_summary = error_summary
        if retryable and sample.attempt_count < 3:
            sample.status = ModelSearchSampleStatus.PENDING
            sample.next_attempt_at = observed_at + timedelta(
                seconds=2 ** sample.attempt_count)
        else:
            sample.status = ModelSearchSampleStatus.FAILED
            sample.completed_at = observed_at
            run.failed_sample_count += 1
        self.session.flush()
        return run

    def advance_if_exhausted(
        self,
        run_id: UUID,
        *,
        now: datetime | None = None,
    ) -> str:
        observed_at = now or datetime.now(UTC)
        run = self.session.scalar(select(ModelSearchRun).where(
            ModelSearchRun.id == run_id).with_for_update())
        if run is None:
            return "NOT_FOUND"
        remaining = self.session.scalar(select(func.count()).select_from(
            ModelSearchSample).where(
                ModelSearchSample.run_id == run.id,
                ModelSearchSample.status.in_((
                    ModelSearchSampleStatus.PENDING,
                    ModelSearchSampleStatus.RUNNING,
                )),
            )) or 0
        if remaining:
            return "WAITING"
        if run.phase == ModelSearchPhase.BASE:
            if run.search_kind == "CROSS_CITY":
                self._finish(run, observed_at)
                return "FINISHED"
            return self._seed_fine_samples(run, observed_at)
        self._finish(run, observed_at)
        return "FINISHED"

    def stop(self, run_id: UUID) -> ModelSearchRun:
        run = self._locked_run(run_id)
        if run.status not in self._ACTIVE:
            raise InvalidModelSearchTransition("只有等待或运行中的按车型找车任务可以停止")
        run.status = ModelSearchRunStatus.STOPPED
        run.stopped_at = datetime.now(UTC)
        self.session.flush()
        return run

    def resume(self, run_id: UUID) -> ModelSearchRun:
        run = self._locked_run(run_id)
        if run.status not in (
            ModelSearchRunStatus.STOPPED,
            ModelSearchRunStatus.INTERRUPTED,
        ):
            raise InvalidModelSearchTransition("只有已停止或已中断的任务可以继续")
        if self.session.scalar(select(ModelSearchRun.id).where(
                ModelSearchRun.city_id == run.city_id,
                ModelSearchRun.id != run.id,
                ModelSearchRun.status.in_(self._ACTIVE)).limit(1)) is not None:
            raise InvalidModelSearchTransition("广州已有活动按车型找车任务")
        self.session.execute(update(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id,
            ModelSearchSample.status == ModelSearchSampleStatus.RUNNING,
        ).values(status=ModelSearchSampleStatus.PENDING, claim_token=None))
        run.status = ModelSearchRunStatus.RUNNING
        run.stopped_at = None
        run.completed_at = None
        self.session.flush()
        return run

    def interrupt_stale_runs(self) -> int:
        active_ids = select(ModelSearchRun.id).where(
            ModelSearchRun.status.in_(self._ACTIVE))
        self.session.execute(update(ModelSearchSample).where(
            ModelSearchSample.run_id.in_(active_ids),
            ModelSearchSample.status == ModelSearchSampleStatus.RUNNING,
        ).values(status=ModelSearchSampleStatus.PENDING, claim_token=None))
        result = self.session.execute(update(ModelSearchRun).where(
            ModelSearchRun.status.in_(self._ACTIVE),
        ).values(status=ModelSearchRunStatus.INTERRUPTED))
        self.session.flush()
        return int(result.rowcount or 0)

    def _seed_fine_samples(self, run: ModelSearchRun, now: datetime) -> str:
        target_ids = list(self.session.scalars(select(
            ModelSearchTarget.vehicle_model_id).where(
                ModelSearchTarget.run_id == run.id)))
        candidates = self._historical_candidate_ids(run.city_id, target_ids)
        candidates.update(self.session.scalars(select(
            ModelSearchOffer.department_id).where(
                ModelSearchOffer.run_id == run.id,
                ModelSearchOffer.book_flag.is_(True),
            ).distinct()).all())
        run.candidate_department_count = len(candidates)
        fine_count = len(candidates) * 96
        final_count = run.planned_sample_count + fine_count
        run.estimated_request_count = final_count
        run.execution_concurrency = 1 if final_count > SLOW_REQUEST_THRESHOLD else 2
        if final_count > HARD_REQUEST_LIMIT:
            run.status = ModelSearchRunStatus.BUDGET_EXCEEDED
            run.completed_at = now
            self._refresh_result_counts(run)
            self.session.flush()
            return "BUDGET_EXCEEDED"
        if not candidates:
            self._finish(run, now)
            self.session.flush()
            return "FINISHED"

        saturdays = sorted({
            pickup.astimezone(SHANGHAI).date()
            for pickup in self.session.scalars(select(
                ModelSearchSample.pickup_time).where(
                    ModelSearchSample.run_id == run.id,
                    ModelSearchSample.kind == ModelSearchSampleKind.BASE,
                ).distinct())
        })
        existing_keys = set(self.session.execute(select(
            ModelSearchSample.anchor_department_id,
            ModelSearchSample.pickup_time,
            ModelSearchSample.return_time,
        ).where(ModelSearchSample.run_id == run.id)).all())
        additions = []
        for department_id in sorted(candidates):
            for saturday in saturdays:
                for window in hourly_windows(saturday):
                    key = (department_id, window.pickup, window.return_time)
                    if key in existing_keys:
                        continue
                    additions.append(ModelSearchSample(
                        run_id=run.id,
                        anchor_department_id=department_id,
                        kind=ModelSearchSampleKind.FINE,
                        pickup_time=window.pickup,
                        return_time=window.return_time,
                    ))
        self.session.add_all(additions)
        run.phase = ModelSearchPhase.FINE
        run.planned_sample_count += len(additions)
        self.session.flush()
        return "FINE_SEEDED"

    def _historical_candidate_ids(
        self,
        city_id: int,
        vehicle_model_ids: list[int],
    ) -> set[int]:
        if not vehicle_model_ids:
            return set()
        return set(self.session.scalars(select(CitywideOffer.department_id).join(
            Department, Department.id == CitywideOffer.department_id,
        ).where(
            Department.city_id == city_id,
            CitywideOffer.vehicle_model_id.in_(vehicle_model_ids),
            CitywideOffer.book_flag.is_(True),
        ).distinct()).all())

    def _recent_cache_source(
        self,
        run: ModelSearchRun,
        sample: ModelSearchSample,
        cutoff: datetime,
    ) -> ModelSearchSample | None:
        return self.session.scalar(select(ModelSearchSample).join(
            ModelSearchRun, ModelSearchRun.id == ModelSearchSample.run_id,
        ).where(
            ModelSearchSample.run_id != run.id,
            ModelSearchRun.city_id == run.city_id,
            ModelSearchRun.return_city_id == run.return_city_id,
            ModelSearchRun.target_fingerprint == run.target_fingerprint,
            ModelSearchRun.request_version == run.request_version,
            ModelSearchSample.anchor_department_id == sample.anchor_department_id,
            ModelSearchSample.pickup_time == sample.pickup_time,
            ModelSearchSample.return_time == sample.return_time,
            ModelSearchSample.status == ModelSearchSampleStatus.COMPLETED,
            ModelSearchSample.completed_at >= cutoff,
        ).order_by(ModelSearchSample.completed_at.desc()).limit(1))

    def _reuse_sample(
        self,
        run: ModelSearchRun,
        sample: ModelSearchSample,
        source: ModelSearchSample,
        now: datetime,
    ) -> None:
        for offer in self.session.scalars(select(ModelSearchOffer).where(
                ModelSearchOffer.sample_id == source.id)):
            self.session.add(ModelSearchOffer(
                run_id=run.id,
                sample_id=sample.id,
                vehicle_model_id=offer.vehicle_model_id,
                department_id=offer.department_id,
                daily_price=offer.daily_price,
                package_price=offer.package_price,
                book_flag=offer.book_flag,
                inventory_type=offer.inventory_type,
                model_description=offer.model_description,
                distance_from_yuzhu_km=offer.distance_from_yuzhu_km,
                verified_at=source.completed_at or now,
            ))
        sample.status = ModelSearchSampleStatus.COMPLETED
        sample.cache_source_sample_id = source.id
        sample.completed_at = now
        sample.response_department_count = source.response_department_count
        sample.response_model_count = source.response_model_count
        run.completed_sample_count += 1
        run.cache_hit_count += 1
        self._refresh_result_counts(run)

    def _refresh_result_counts(self, run: ModelSearchRun) -> None:
        run.found_variant_count = int(self.session.scalar(select(
            func.count(func.distinct(ModelSearchOffer.vehicle_model_id))).where(
                ModelSearchOffer.run_id == run.id,
                ModelSearchOffer.book_flag.is_(True),
            )) or 0)
        run.available_department_count = int(self.session.scalar(select(
            func.count(func.distinct(ModelSearchOffer.department_id))).where(
                ModelSearchOffer.run_id == run.id,
                ModelSearchOffer.book_flag.is_(True),
            )) or 0)

    def _finish(self, run: ModelSearchRun, now: datetime) -> None:
        run.status = (
            ModelSearchRunStatus.PARTIAL
            if run.failed_sample_count else ModelSearchRunStatus.COMPLETED
        )
        run.completed_at = now
        self._refresh_result_counts(run)
        self.session.flush()

    def _locked_run(self, run_id: UUID) -> ModelSearchRun:
        run = self.session.scalar(select(ModelSearchRun).where(
            ModelSearchRun.id == run_id).with_for_update())
        if run is None:
            raise InvalidModelSearchTransition("按车型找车任务不存在")
        return run

    def _locked_claim(
        self,
        sample_id: int,
        claim_token: UUID,
    ) -> tuple[ModelSearchRun, ModelSearchSample]:
        sample = self.session.scalar(select(ModelSearchSample).where(
            ModelSearchSample.id == sample_id).with_for_update())
        if (sample is None or sample.status != ModelSearchSampleStatus.RUNNING
                or sample.claim_token != claim_token):
            raise InvalidModelSearchTransition("找车样本领取已失效")
        run = self._locked_run(sample.run_id)
        return run, sample
