"""广州神州网点发现：短事务领取、匿名外呼、短事务落库。"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.departments.planner import DISCOVERY_PRESETS, build_grid
from app.domain import ScanQuery
from app.integrations.map_cache_repository import DEFAULT_PROVIDER, normalize_cache_text
from app.models import City, Department, DepartmentDiscoveryRun, MapSearchCache, Probe
from app.repositories.departments import (
    DepartmentDiscoveryRepository,
    DepartmentRepository,
    DiscoveryPointClaim,
    InvalidDiscoveryTransition,
)
from app.zuche.client import ZucheGatewayError
from app.zuche.parser import parse_choose_car


logger = logging.getLogger(__name__)


class DepartmentDiscoveryService:
    """每次只处理一个点，任何网络等待都不持有 SQLAlchemy Session。"""

    def __init__(
        self,
        session_factory,
        gateway_factory,
        *,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.gateway_factory = gateway_factory
        # 限速由单实例 interval job 保证；保留注入点兼容显式调用方。
        self.sleep = sleep

    def create_run(
        self,
        city: City | int,
        *,
        preset: str = "quick",
        max_requests: int | None = None,
        pickup_time: datetime | None = None,
        return_time: datetime | None = None,
    ) -> DepartmentDiscoveryRun:
        configuration = DISCOVERY_PRESETS.get(preset)
        if configuration is None:
            raise ValueError("未知的网点发现预设")
        request_limit = configuration.max_requests if max_requests is None else max_requests
        if isinstance(request_limit, bool) or not isinstance(request_limit, int) or request_limit <= 0:
            raise ValueError("请求上限必须是正整数")
        if request_limit > configuration.max_requests:
            raise ValueError("请求上限不能超过固定预设上限")
        if pickup_time is None and return_time is None:
            pickup_time, return_time = _default_period()
        elif pickup_time is None or return_time is None:
            raise ValueError("取还车时间必须同时提供")

        city_id = city.id if isinstance(city, City) else city
        with self.session_factory() as session:
            stored_city = session.get(City, city_id)
            if stored_city is None:
                raise ValueError("发现城市不存在")
            if stored_city.latitude is None or stored_city.longitude is None:
                raise ValueError("发现城市缺少中心坐标")
            repository = DepartmentDiscoveryRepository(session)
            run = repository.create_run(
                city_id=stored_city.id,
                preset=preset,
                radius_km=configuration.radius_km,
                spacing_km=configuration.spacing_km,
                max_requests=request_limit,
                pickup_time=pickup_time,
                return_time=return_time,
            )
            for latitude, longitude in build_grid(
                    float(stored_city.latitude), float(stored_city.longitude),
                    configuration.radius_km, configuration.spacing_km):
                repository.add_point(
                    run.id, latitude=latitude, longitude=longitude, source="GRID")
            for latitude, longitude, source in self._seed_points(session, stored_city):
                repository.add_point(
                    run.id, latitude=latitude, longitude=longitude, source=source)
            repository.set_planned_point_count(run)
            repository.mark_running(run)
            session.commit()
            session.refresh(run)
            session.expunge(run)
            return run

    def stop(self, run_id: UUID) -> DepartmentDiscoveryRun:
        return self._transition(run_id, "stop")

    def resume(self, run_id: UUID) -> DepartmentDiscoveryRun:
        return self._transition(run_id, "resume")

    async def process_active_run(self) -> str:
        """供唯一的全局调度作业调用，每个 tick 最多处理一个点。"""
        with self.session_factory() as session:
            run_id = session.scalar(select(DepartmentDiscoveryRun.id).where(
                DepartmentDiscoveryRun.status == "RUNNING",
            ).order_by(DepartmentDiscoveryRun.created_at, DepartmentDiscoveryRun.id).limit(1))
        if run_id is None:
            return "IDLE"
        return await self.process_next_point(run_id)

    async def process_next_point(self, run_id: UUID) -> str:
        try:
            claim_result = self._claim(run_id)
        except SQLAlchemyError:
            logger.exception("网点发现领取事务失败")
            return "DATABASE_ERROR"
        claim = claim_result.claim
        if claim is None:
            return claim_result.state

        query = ScanQuery(
            city_id=claim.zuche_city_id,
            location_name=f"{claim.city_name}网点发现",
            latitude=claim.latitude,
            longitude=claim.longitude,
            pickup_time=claim.pickup_time.astimezone(ZoneInfo("Asia/Shanghai")),
            return_time=claim.return_time.astimezone(ZoneInfo("Asia/Shanghai")),
        )
        request_error = None
        retry_state = None
        try:
            for attempt in range(3):
                try:
                    async with self.gateway_factory() as gateway:
                        payload = await gateway.choose_car(query)
                    parsed = parse_choose_car(payload, query)
                    request_error = None
                    break
                except Exception as error:
                    request_error = error
                    if not _is_retryable(error) or attempt == 2:
                        break
                    await self._retry_wait(2 ** attempt)
                    retry_state = self._reserve_retry(claim)
                    if retry_state != "RESERVED":
                        break
        except asyncio.CancelledError:
            try:
                self._safe_finalize_failure(
                    claim.point_id,
                    claim_token=claim.claim_token,
                    summary="网点发现请求已取消，可稍后重试",
                    requeue=True,
                )
            except Exception:  # pragma: no cover - persistent database outage cannot be repaired in-process
                logger.exception("取消网点发现请求后重排点位失败")
            raise
        if request_error is not None:
            summary = _safe_error_summary(request_error)
            try:
                self._safe_finalize_failure(
                    claim.point_id, claim_token=claim.claim_token, summary=summary)
            except InvalidDiscoveryTransition:
                return "STALE"
            except SQLAlchemyError:
                logger.exception("网点发现失败结果落库失败")
                return "DATABASE_ERROR"
            return retry_state if retry_state in {"INACTIVE", "LIMIT", "STALE"} else "FAILED"

        try:
            self._persist_success(claim, parsed.departments)
        except InvalidDiscoveryTransition:
            return "STALE"
        except SQLAlchemyError:
            logger.exception("网点发现成功结果落库失败")
            try:
                self._safe_finalize_failure(
                    claim.point_id,
                    claim_token=claim.claim_token,
                    summary="网点发现结果保存失败",
                )
            except InvalidDiscoveryTransition:
                return "STALE"
            except SQLAlchemyError:
                logger.exception("网点发现结果保存失败后的点位补偿失败")
                return "DATABASE_ERROR"
            return "FAILED"
        return "COMPLETED"

    def _claim(self, run_id: UUID):
        with self.session_factory() as session:
            result = DepartmentDiscoveryRepository(session).claim_next_point(run_id)
            session.commit()
            return result

    def _reserve_retry(self, claim: DiscoveryPointClaim) -> str:
        with self.session_factory() as session:
            state = DepartmentDiscoveryRepository(session).reserve_retry(
                claim.point_id, claim_token=claim.claim_token)
            session.commit()
            return state

    async def _retry_wait(self, delay: float) -> None:
        wait_result = self.sleep(delay) if self.sleep is not None else asyncio.sleep(delay)
        if wait_result is not None:
            await wait_result

    def _persist_success(self, claim: DiscoveryPointClaim, parsed_departments) -> None:
        unique_departments = {
            department.department_id: department for department in parsed_departments
        }
        department_ids = set(unique_departments)
        with self.session_factory() as session:
            departments = DepartmentRepository(session)
            existing_ids = departments.existing_zuche_ids(department_ids)
            for department in unique_departments.values():
                departments.upsert(claim.city_id, {
                    "deptId": department.department_id,
                    "deptName": department.name,
                    "deptAddress": department.address,
                    "lat": department.latitude,
                    "lon": department.longitude,
                    "business_hours": department.business_hours,
                    "is_open_24h": department.is_open_24h,
                    "self_service_pickup": department.self_service_pickup,
                    "self_service_return": department.self_service_return,
                })
            DepartmentDiscoveryRepository(session).finalize_point(
                claim.point_id,
                claim_token=claim.claim_token,
                succeeded=True,
                response_department_count=len(unique_departments),
                new_department_count=len(department_ids - existing_ids),
                updated_department_count=len(department_ids & existing_ids),
            )
            session.commit()

    def _safe_finalize_failure(
        self,
        point_id: int,
        *,
        claim_token: UUID,
        summary: str,
        requeue: bool = False,
    ) -> None:
        last_error = None
        for _ in range(2):
            try:
                with self.session_factory() as session:
                    DepartmentDiscoveryRepository(session).finalize_point(
                        point_id,
                        claim_token=claim_token,
                        succeeded=False,
                        error_summary=summary,
                        requeue=requeue,
                    )
                    session.commit()
                return
            except SQLAlchemyError as error:
                last_error = error
        assert last_error is not None
        raise last_error

    def _transition(self, run_id: UUID, operation: str) -> DepartmentDiscoveryRun:
        with self.session_factory() as session:
            repository = DepartmentDiscoveryRepository(session)
            run = repository.get_run_for_update(run_id)
            if run is None:
                raise ValueError("发现任务不存在")
            if operation == "stop":
                repository.mark_stopped(run)
            else:
                repository.mark_running(run)
            session.commit()
            session.refresh(run)
            session.expunge(run)
            return run

    @staticmethod
    def _seed_points(session, city: City):
        probes = session.execute(select(Probe.latitude, Probe.longitude).where(
            Probe.city_id == city.id,
            Probe.enabled.is_(True),
        )).all()
        for latitude, longitude in probes:
            if (point := _valid_point(latitude, longitude)) is not None:
                yield *point, "PROBE"

        departments = session.execute(select(Department.latitude, Department.longitude).where(
            Department.city_id == city.id,
            Department.latitude.is_not(None),
            Department.longitude.is_not(None),
        )).all()
        for latitude, longitude in departments:
            if (point := _valid_point(latitude, longitude)) is not None:
                yield *point, "DEPARTMENT"

        city_name = normalize_cache_text(city.name)
        city_alias = normalize_cache_text(
            city.name.removesuffix("市") if city.name.endswith("市") else f"{city.name}市")
        region_names = {city_name, city_alias}
        caches = session.scalars(select(MapSearchCache).where(
            MapSearchCache.provider == DEFAULT_PROVIDER,
            MapSearchCache.normalized_region.in_(region_names),
        )).all()
        for cache in caches:
            details = cache.results_json if isinstance(cache.results_json, list) else []
            for detail in details:
                if not isinstance(detail, Mapping):
                    continue
                point = _valid_point(detail.get("latitude"), detail.get("longitude"))
                if point is not None:
                    yield *point, "MAP_CACHE"


def _valid_point(latitude: object, longitude: object) -> tuple[float, float] | None:
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        return None
    if (not isfinite(latitude_value) or not isfinite(longitude_value)
            or not -90 <= latitude_value <= 90 or not -180 <= longitude_value <= 180):
        return None
    return latitude_value, longitude_value


def _default_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    local_now = (now or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
    pickup = local_now.replace(hour=9, minute=0, second=0, microsecond=0)
    if pickup <= local_now:
        pickup += timedelta(days=1)
    return pickup, pickup + timedelta(days=1)


def _safe_error_summary(error: Exception) -> str:
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError)):
        return "神州匿名接口网络请求失败"
    if isinstance(error, ZucheGatewayError):
        return "神州匿名接口请求失败"
    if isinstance(error, (ValueError, TypeError)):
        return "神州匿名接口响应格式错误"
    return "网点发现请求失败"


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return (isinstance(error, httpx.HTTPStatusError)
            and error.response.status_code >= 500)
