"""按车型找车的后台短事务编排和神州匿名请求。"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.departments.planner import haversine_km
from app.domain import ParsedScan, ScanQuery
from app.model_catalog import upsert_permanent_model
from app.model_search.domain import ModelSearchSampleClaim, RentalWindow
from app.model_search.repository import (
    ModelSearchRepository,
    InvalidModelSearchTransition,
)
from app.models import City, Department, ModelSearchRun, VehicleModel
from app.repositories.departments import DepartmentRepository
from app.zuche.client import ZucheGatewayError
from app.zuche.parser import parse_choose_car


NETWORK_ERROR = "神州匿名接口网络请求失败"
FORMAT_ERROR = "神州匿名接口响应格式错误"
SAVE_ERROR = "扫描结果保存失败"
YUZHU_COORDINATES = (23.101610, 113.432649)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelSearchProcessResult:
    state: str
    processed: int = 0
    succeeded: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    claim: ModelSearchSampleClaim
    parsed: ParsedScan | None = None
    error_summary: str | None = None
    retryable: bool = False


class ModelSearchService:
    """每轮按任务预算处理一或两个样本，网络等待不占数据库连接。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        gateway_factory: Callable[[], object],
    ) -> None:
        self.session_factory = session_factory
        self.gateway_factory = gateway_factory

    def create_run(
        self,
        city_id: int,
        model_names: list[str],
        *,
        now: datetime | None = None,
    ) -> ModelSearchRun:
        with self.session_factory() as session:
            run = ModelSearchRepository(session).create_run(
                city_id=city_id,
                model_names=model_names,
                now=now,
            )
            session.commit()
            return run

    def create_cross_city_run(
        self,
        *,
        zuche_model_id: int,
        pickup_city_ids: list[int],
        return_city_id: int,
        return_location_name: str,
        windows: tuple[RentalWindow, ...],
        rail_costs: dict[str, int | float],
        now: datetime | None = None,
    ) -> ModelSearchRun:
        with self.session_factory() as session:
            model = session.scalar(select(VehicleModel).where(
                VehicleModel.zuche_model_id == zuche_model_id))
            if model is None:
                raise ValueError("车型库中不存在所选精确车型")
            run = ModelSearchRepository(session).create_cross_city_run(
                vehicle_model_id=model.id,
                pickup_city_ids=pickup_city_ids,
                return_city_id=return_city_id,
                return_location_name=return_location_name,
                windows=windows,
                rail_costs=rail_costs,
                now=now,
            )
            session.commit()
            return run

    async def process_active_run(
        self,
        *,
        now: datetime | None = None,
    ) -> ModelSearchProcessResult:
        with self.session_factory() as session:
            repository = ModelSearchRepository(session)
            run_id = repository.active_run_id()
            if run_id is None:
                return ModelSearchProcessResult("IDLE")
            batch = repository.claim_samples(run_id, limit=2, now=now)
            session.commit()
        if not batch.claims:
            return ModelSearchProcessResult(batch.state)

        outcomes = await asyncio.gather(*(self._fetch(claim) for claim in batch.claims))
        succeeded = 0
        failed = 0
        for outcome in outcomes:
            if self._persist_outcome(outcome, now=now):
                succeeded += 1
            else:
                failed += 1
        return ModelSearchProcessResult(
            "PROCESSED",
            processed=len(outcomes),
            succeeded=succeeded,
            failed=failed,
        )

    def stop(self, run_id: UUID) -> ModelSearchRun:
        return self._transition("stop", run_id)

    def resume(self, run_id: UUID) -> ModelSearchRun:
        return self._transition("resume", run_id)

    def interrupt_stale_runs(self) -> int:
        with self.session_factory() as session:
            count = ModelSearchRepository(session).interrupt_stale_runs()
            session.commit()
            return count

    def _transition(self, action: str, run_id: UUID) -> ModelSearchRun:
        with self.session_factory() as session:
            run = getattr(ModelSearchRepository(session), action)(run_id)
            session.commit()
            return run

    async def _fetch(self, claim: ModelSearchSampleClaim) -> _FetchOutcome:
        query = ScanQuery(
            city_id=claim.zuche_city_id,
            return_city_id=claim.return_zuche_city_id,
            location_name=claim.anchor_name,
            latitude=claim.latitude,
            longitude=claim.longitude,
            pickup_time=claim.pickup_time,
            return_time=claim.return_time,
        )
        try:
            async with self.gateway_factory() as gateway:
                raw = await gateway.choose_car(query)
            return _FetchOutcome(
                claim=claim,
                parsed=parse_choose_car(raw, query),
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return _FetchOutcome(
                claim=claim, error_summary=NETWORK_ERROR, retryable=True)
        except httpx.HTTPStatusError as error:
            retryable = error.response.status_code == 429 or error.response.status_code >= 500
            return _FetchOutcome(
                claim=claim,
                error_summary=NETWORK_ERROR if retryable else FORMAT_ERROR,
                retryable=retryable,
            )
        except (ZucheGatewayError, ValueError, TypeError):
            return _FetchOutcome(claim=claim, error_summary=FORMAT_ERROR)

    def _persist_outcome(
        self,
        outcome: _FetchOutcome,
        *,
        now: datetime | None = None,
    ) -> bool:
        if outcome.parsed is None:
            self._record_failure(outcome, now=now)
            return False
        try:
            with self.session_factory() as session:
                self._persist_success(session, outcome, now=now)
                session.commit()
            return True
        except InvalidModelSearchTransition:
            return False
        except Exception:
            logger.exception("按车型找车结果保存失败")
            self._record_save_failure(outcome.claim, now=now)
            return False

    def _persist_success(
        self,
        session: Session,
        outcome: _FetchOutcome,
        *,
        now: datetime | None = None,
    ) -> None:
        claim = outcome.claim
        parsed = outcome.parsed
        if parsed is None:
            raise ValueError("成功结果缺少解析数据")
        observed_at = now or datetime.now(UTC)
        repository = ModelSearchRepository(session)
        city = session.get(City, claim.city_id)
        if city is None:
            raise ValueError("扫描城市不存在")
        departments = {
            item.department_id: DepartmentRepository(session).upsert(
                city,
                {
                    "deptId": item.department_id,
                    "deptName": item.name,
                    "deptAddress": item.address,
                    "lat": item.latitude,
                    "lon": item.longitude,
                    "business_hours": item.business_hours,
                    "is_open_24h": item.is_open_24h,
                    "self_service_pickup": item.self_service_pickup,
                    "self_service_return": item.self_service_return,
                },
                source="MODEL_SEARCH",
            )
            for item in parsed.departments
        }
        models = {}
        for group in parsed.groups:
            model, _ = upsert_permanent_model(
                session,
                model_id=group.model_id,
                model_name=group.model_name,
                image_url=group.model_image_url,
                observed_at=observed_at,
            )
            models[group.model_id] = model
        for offer in parsed.offers:
            model, _ = upsert_permanent_model(
                session,
                model_id=offer.model_id,
                model_name=offer.model_name,
                description=offer.model_desc,
                image_url=offer.image_url,
                energy_type=offer.energy_type,
                observed_at=observed_at,
            )
            models[offer.model_id] = model
            if offer.model_id not in claim.target_model_ids:
                continue
            department = departments.get(offer.department_id)
            if department is None:
                continue
            repository.add_offer(
                run_id=claim.run_id,
                sample_id=claim.sample_id,
                vehicle_model_id=model.id,
                department_id=department.id,
                daily_price=offer.daily_price,
                package_price=offer.package_price,
                book_flag=offer.bookable,
                inventory_type=offer.inventory_type,
                model_description=offer.model_desc,
                distance_from_yuzhu_km=_distance_from_yuzhu(department),
                verified_at=observed_at,
            )
        repository.complete_sample(
            claim.sample_id,
            claim_token=claim.claim_token,
            response_department_count=len(parsed.departments),
            response_model_count=len(models),
            now=observed_at,
        )

    def _record_failure(
        self,
        outcome: _FetchOutcome,
        *,
        now: datetime | None = None,
    ) -> None:
        with self.session_factory() as session:
            ModelSearchRepository(session).fail_sample(
                outcome.claim.sample_id,
                claim_token=outcome.claim.claim_token,
                error_summary=outcome.error_summary or FORMAT_ERROR,
                retryable=outcome.retryable,
                now=now,
            )
            session.commit()

    def _record_save_failure(
        self,
        claim: ModelSearchSampleClaim,
        *,
        now: datetime | None = None,
    ) -> None:
        with self.session_factory() as session:
            try:
                ModelSearchRepository(session).fail_sample(
                    claim.sample_id,
                    claim_token=claim.claim_token,
                    error_summary=SAVE_ERROR,
                    retryable=False,
                    now=now,
                )
                session.commit()
            except InvalidModelSearchTransition:
                session.rollback()


def _distance_from_yuzhu(department: Department) -> float | None:
    if department.latitude is None or department.longitude is None:
        return None
    return haversine_km(
        YUZHU_COORDINATES,
        (float(department.latitude), float(department.longitude)),
    )
