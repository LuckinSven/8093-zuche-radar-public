"""广州全城扫描的短事务编排和匿名上游并发调用。"""

import asyncio
import gzip
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.citywide.domain import CitywidePointClaim
from app.citywide.repository import CitywideRepository, InvalidCitywideTransition
from app.domain import ParsedGroup, ParsedOffer, ParsedScan, ScanQuery
from app.model_catalog import upsert_permanent_model
from app.models import City, CitywideRawPayload, CitywideScanRun, VehicleModel
from app.repositories.departments import DepartmentRepository
from app.zuche.client import ZucheGatewayError
from app.zuche.parser import parse_choose_car


NETWORK_ERROR = "神州匿名接口网络请求失败"
FORMAT_ERROR = "神州匿名接口响应格式错误"
SAVE_ERROR = "扫描结果保存失败"


@dataclass(frozen=True, slots=True)
class CitywideProcessResult:
    state: str
    processed: int = 0
    succeeded: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    claim: CitywidePointClaim
    raw: dict | None = None
    parsed: ParsedScan | None = None
    error_summary: str | None = None
    retryable: bool = False


class CitywideScanService:
    """每轮最多处理三个点，任何网络等待期间都不持有数据库会话。"""

    def __init__(self, session_factory: Callable[[], Session], gateway_factory: Callable[[], object]):
        self.session_factory = session_factory
        self.gateway_factory = gateway_factory

    def create_run(
        self,
        city_id: int,
        pickup_time: datetime,
        return_time: datetime,
    ) -> CitywideScanRun:
        with self.session_factory() as session:
            run = CitywideRepository(session).create_run(
                city_id=city_id,
                pickup_time=pickup_time,
                return_time=return_time,
            )
            session.commit()
            return run

    async def process_active_run(self) -> CitywideProcessResult:
        with self.session_factory() as session:
            repository = CitywideRepository(session)
            run_id = repository.active_run_id()
            if run_id is None:
                return CitywideProcessResult("IDLE")
            batch = repository.claim_points(run_id, limit=3)
            session.commit()
        if not batch.claims:
            return CitywideProcessResult(batch.state)

        outcomes = await asyncio.gather(*(
            self._fetch(claim) for claim in batch.claims
        ))
        succeeded = 0
        failed = 0
        for outcome in outcomes:
            if self._persist_outcome(outcome):
                succeeded += 1
            else:
                failed += 1
        return CitywideProcessResult(
            "PROCESSED",
            processed=len(outcomes),
            succeeded=succeeded,
            failed=failed,
        )

    def stop(self, run_id: UUID) -> CitywideScanRun:
        return self._transition("stop", run_id)

    def resume(self, run_id: UUID) -> CitywideScanRun:
        return self._transition("resume", run_id)

    def retry_failed(self, run_id: UUID) -> CitywideScanRun:
        return self._transition("retry_failed", run_id)

    def interrupt_stale_runs(self) -> int:
        with self.session_factory() as session:
            count = CitywideRepository(session).interrupt_stale_runs()
            session.commit()
            return count

    def _transition(self, action: str, run_id: UUID) -> CitywideScanRun:
        with self.session_factory() as session:
            repository = CitywideRepository(session)
            run = getattr(repository, action)(run_id)
            session.commit()
            return run

    async def _fetch(self, claim: CitywidePointClaim) -> _FetchOutcome:
        query = ScanQuery(
            city_id=claim.zuche_city_id,
            location_name=claim.anchor_name,
            latitude=claim.latitude,
            longitude=claim.longitude,
            pickup_time=claim.pickup_time,
            return_time=claim.return_time,
        )
        raw: object | None = None
        try:
            async with self.gateway_factory() as gateway:
                raw = await gateway.choose_car(query)
            parsed = parse_choose_car(raw, query)
            return _FetchOutcome(claim=claim, raw=raw, parsed=parsed)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            return _FetchOutcome(
                claim=claim,
                error_summary=NETWORK_ERROR,
                retryable=_is_retryable_network_error(error),
            )
        except httpx.HTTPStatusError as error:
            retryable = error.response.status_code >= 500
            return _FetchOutcome(
                claim=claim,
                error_summary=NETWORK_ERROR if retryable else FORMAT_ERROR,
                retryable=retryable,
            )
        except (ZucheGatewayError, ValueError, TypeError):
            return _FetchOutcome(
                claim=claim,
                raw=raw if isinstance(raw, dict) else None,
                error_summary=FORMAT_ERROR,
            )

    def _persist_outcome(self, outcome: _FetchOutcome) -> bool:
        if outcome.parsed is None:
            self._record_failure(outcome)
            return False
        try:
            with self.session_factory() as session:
                self._persist_success(session, outcome)
                session.commit()
            return True
        except InvalidCitywideTransition:
            return False
        except Exception:
            self._record_save_failure(outcome.claim)
            return False

    def _persist_success(self, session: Session, outcome: _FetchOutcome) -> None:
        claim = outcome.claim
        parsed = outcome.parsed
        raw = outcome.raw
        if parsed is None or raw is None:
            raise ValueError("成功结果缺少解析数据")
        repository = CitywideRepository(session)
        city = session.get(City, claim.city_id)
        if city is None:
            raise ValueError("扫描城市不存在")
        now = datetime.now(UTC)
        session.add(CitywideRawPayload(
            run_id=claim.run_id,
            point_id=claim.point_id,
            payload=_compressed_payload(raw),
            captured_at=now,
        ))

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
                source="CITYWIDE_SCAN",
            )
            for item in parsed.departments
        }

        models: dict[int, VehicleModel] = {}
        new_model_count = 0
        for group in parsed.groups:
            model, created = upsert_permanent_model(
                session,
                model_id=group.model_id,
                model_name=group.model_name,
                image_url=group.model_image_url,
                observed_at=now,
            )
            models[group.model_id] = model
            new_model_count += int(created)
        for offer in parsed.offers:
            model, created = upsert_permanent_model(
                session,
                model_id=offer.model_id,
                model_name=offer.model_name,
                description=offer.model_desc,
                image_url=offer.image_url,
                energy_type=offer.energy_type,
                observed_at=now,
            )
            models[offer.model_id] = model
            new_model_count += int(created)
            department = departments.get(offer.department_id)
            if department is None:
                continue
            repository.upsert_offer(
                run_id=claim.run_id,
                vehicle_model_id=model.id,
                department_id=department.id,
                source_point_id=claim.point_id,
                source_is_self=offer.department_id == claim.anchor_zuche_dept_id,
                source_distance_km=offer.distance_km,
                daily_price=offer.daily_price,
                package_price=offer.package_price,
                book_flag=offer.bookable,
                inventory_type=offer.inventory_type,
                model_description=offer.model_desc,
            )

        run = session.get(CitywideScanRun, claim.run_id)
        if run is None:
            raise InvalidCitywideTransition("全城扫描任务不存在")
        run.new_model_count += new_model_count
        repository.complete_point(
            claim.point_id,
            claim_token=claim.claim_token,
            response_department_count=len(parsed.departments),
            response_model_count=len(models),
        )

    def _record_failure(self, outcome: _FetchOutcome) -> None:
        with self.session_factory() as session:
            if outcome.raw is not None:
                session.add(CitywideRawPayload(
                    run_id=outcome.claim.run_id,
                    point_id=outcome.claim.point_id,
                    payload=_compressed_payload(outcome.raw),
                    captured_at=datetime.now(UTC),
                ))
            CitywideRepository(session).fail_point(
                outcome.claim.point_id,
                claim_token=outcome.claim.claim_token,
                error_summary=outcome.error_summary or FORMAT_ERROR,
                retryable=outcome.retryable,
            )
            session.commit()

    def _record_save_failure(self, claim: CitywidePointClaim) -> None:
        with self.session_factory() as session:
            try:
                CitywideRepository(session).fail_point(
                    claim.point_id,
                    claim_token=claim.claim_token,
                    error_summary=SAVE_ERROR,
                    retryable=False,
                )
                session.commit()
            except InvalidCitywideTransition:
                session.rollback()


def _compressed_payload(raw: dict) -> bytes:
    encoded = json.dumps(
        raw,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return gzip.compress(encoded)


def _is_retryable_network_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    return isinstance(error, (httpx.TimeoutException, httpx.TransportError))
