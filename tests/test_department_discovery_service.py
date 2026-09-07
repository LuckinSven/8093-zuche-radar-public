import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.departments.service import DepartmentDiscoveryService
from app.models import (
    City,
    Department,
    DepartmentDiscoveryPoint,
    DepartmentDiscoveryPointStatus,
    DepartmentDiscoveryStatus,
    MapSearchCache,
    Probe,
)
from app.repositories.departments import DepartmentDiscoveryRepository
from app.zuche.client import ZucheGatewayError


PICKUP_TIME = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
RETURN_TIME = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)


def _city(session: Session) -> City:
    city = City(
        zuche_city_id="14",
        name="广州",
        latitude=23.1291,
        longitude=113.2644,
        enabled=True,
    )
    session.add(city)
    session.commit()
    return city


def _payload(*departments: dict) -> dict:
    return {"code": 1, "content": {"deptHangModels": list(departments), "modelGroups": []}}


class Gateway:
    def __init__(self, payload_or_error, *, on_request=None):
        self.payload_or_error = payload_or_error
        self.on_request = on_request
        self.queries = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def choose_car(self, query):
        self.queries.append(query)
        if self.on_request is not None:
            await self.on_request()
        if isinstance(self.payload_or_error, Exception):
            raise self.payload_or_error
        return self.payload_or_error


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://m.zuche.com/api/gw.do")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("上游临时错误", request=request, response=response)


async def _done():
    return None


def test_create_run_combines_grid_city_seeds_and_only_valid_guangzhou_baidu_cache(engine):
    """若缓存范围/provider/坐标校验放宽，其他城市或非法点会污染广州任务。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = _city(session)
        session.add_all([
            Probe(city_id=city.id, name="启用点", latitude=23.501001, longitude=113.501001, enabled=True),
            Probe(city_id=city.id, name="关闭点", latitude=23.502002, longitude=113.502002, enabled=False),
            Department(city_id=city.id, zuche_dept_id=801, name="已有网点",
                       latitude=23.503003, longitude=113.503003),
            MapSearchCache(
                provider="baidu_maps", region="广州", keyword="有效",
                normalized_region="广州", normalized_keyword="有效",
                results_json=[
                    {"name": "有效地点", "latitude": 23.504004, "longitude": 113.504004},
                    {"name": "非法地点", "latitude": "NaN", "longitude": 113.6},
                ],
            ),
            MapSearchCache(
                provider="other", region="广州", keyword="其他 provider",
                normalized_region="广州", normalized_keyword="其他 provider",
                results_json=[{"latitude": 23.505005, "longitude": 113.505005}],
            ),
            MapSearchCache(
                provider="baidu_maps", region="深圳", keyword="其他城市",
                normalized_region="深圳", normalized_keyword="其他城市",
                results_json=[{"latitude": 23.506006, "longitude": 113.506006}],
            ),
            MapSearchCache(
                provider="baidu_maps", region="广州市", keyword="城市后缀别名",
                normalized_region="广州市", normalized_keyword="城市后缀别名",
                results_json=[{"latitude": 23.507007, "longitude": 113.507007}],
            ),
        ])
        session.commit()
        city_id = city.id

    service = DepartmentDiscoveryService(factory, lambda: Gateway(_payload()))
    run = service.create_run(
        city_id,
        preset="quick",
        max_requests=2,
        pickup_time=PICKUP_TIME,
        return_time=RETURN_TIME,
    )

    with factory() as session:
        points = session.scalars(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id)).all()
        coordinates = {(float(point.latitude), float(point.longitude)) for point in points}
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)

    assert (23.501001, 113.501001) in coordinates
    assert (23.503003, 113.503003) in coordinates
    assert (23.504004, 113.504004) in coordinates
    assert (23.502002, 113.502002) not in coordinates
    assert (23.505005, 113.505005) not in coordinates
    assert (23.506006, 113.506006) not in coordinates
    assert (23.507007, 113.507007) in coordinates
    assert stored.planned_point_count == len(coordinates)
    assert stored.pickup_time == PICKUP_TIME
    assert stored.return_time == RETURN_TIME
    assert stored.status == DepartmentDiscoveryStatus.RUNNING


def test_city_cache_seeds_accept_city_suffix_aliases_without_crossing_into_guangzhou(engine):
    """若种子无条件加入广州，深圳任务会混入广州缓存且漏掉“深圳市”别名。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = City(
            zuche_city_id="74", name="深圳", latitude=22.5431, longitude=114.0579,
            enabled=True)
        session.add(city)
        session.flush()
        session.add_all([
            MapSearchCache(
                provider="baidu_maps", region="广州", keyword="跨城",
                normalized_region="广州", normalized_keyword="跨城",
                results_json=[{"latitude": 25.101001, "longitude": 110.101001}],
            ),
            MapSearchCache(
                provider="baidu_maps", region="深圳", keyword="城市名",
                normalized_region="深圳", normalized_keyword="城市名",
                results_json=[{"latitude": 26.202002, "longitude": 111.202002}],
            ),
            MapSearchCache(
                provider="baidu_maps", region="深圳市", keyword="城市后缀",
                normalized_region="深圳市", normalized_keyword="城市后缀",
                results_json=[{"latitude": 27.303003, "longitude": 112.303003}],
            ),
        ])
        session.commit()
        city_id = city.id

    run = DepartmentDiscoveryService(factory, lambda: Gateway(_payload())).create_run(
        city_id, preset="quick", max_requests=1,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    with factory() as session:
        coordinates = {(float(point.latitude), float(point.longitude)) for point in session.scalars(
            select(DepartmentDiscoveryPoint).where(
                DepartmentDiscoveryPoint.run_id == run.id)).all()}

    assert (25.101001, 110.101001) not in coordinates
    assert (26.202002, 111.202002) in coordinates
    assert (27.303003, 112.303003) in coordinates


@pytest.mark.asyncio
async def test_runner_closes_database_sessions_during_anonymous_requests_and_stops_at_limit(engine):
    """若网络 await 持有会话或请求上限非原子，连接会被长占且配额可超发。"""
    active_sessions = 0

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs):
            nonlocal active_sessions
            super().__init__(*args, **kwargs)
            active_sessions += 1

        def close(self):
            nonlocal active_sessions
            try:
                super().close()
            finally:
                active_sessions -= 1

    factory = sessionmaker(bind=engine, class_=TrackingSession, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    gateways = []

    async def assert_no_session():
        assert active_sessions == 0

    def gateway_factory():
        assert active_sessions == 0
        gateway = Gateway(_payload(
            {"deptId": 88, "deptName": "广州网点", "deptAddress": "天河区",
             "lat": 23.2, "lon": 113.3, "models": []},
            {"deptId": 88, "deptName": "重复网点", "lat": 23.2, "lon": 113.3, "models": []},
        ), on_request=assert_no_session)
        gateways.append(gateway)
        return gateway

    service = DepartmentDiscoveryService(factory, gateway_factory)
    run = service.create_run(
        city_id, preset="quick", max_requests=2,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    await service.process_next_point(run.id)
    await service.process_next_point(run.id)
    await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        points = session.scalars(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.COMPLETED,
        )).all()
        department_count = session.scalar(select(func.count()).select_from(Department))

    assert len(gateways) == 2
    assert all(gateway.closed for gateway in gateways)
    assert stored.request_count == 2
    assert stored.completed_point_count == 2
    assert stored.new_department_count == 1
    assert stored.updated_department_count == 1
    assert stored.status == DepartmentDiscoveryStatus.COMPLETED_LIMIT
    assert len(points) == 2
    assert department_count == 1
    assert gateways[0].queries[0].pickup_time == PICKUP_TIME
    assert gateways[0].queries[0].return_time == RETURN_TIME
    assert active_sessions == 0


@pytest.mark.asyncio
async def test_saved_rental_period_is_sent_as_guangzhou_local_0900_after_database_round_trip(engine):
    """若 timestamptz 读回 UTC 后直接格式化，神州会收到错误的 01:00 租期。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    gateway = Gateway(_payload())
    pickup = datetime(2026, 9, 5, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    return_time = datetime(2026, 9, 6, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    service = DepartmentDiscoveryService(factory, lambda: gateway)
    run = service.create_run(
        city_id, preset="quick", max_requests=1,
        pickup_time=pickup, return_time=return_time)

    await service.process_next_point(run.id)

    assert gateway.queries[0].pickup_time.isoformat() == "2026-09-05T09:00:00+08:00"
    assert gateway.queries[0].return_time.isoformat() == "2026-09-06T09:00:00+08:00"


@pytest.mark.asyncio
async def test_stop_during_request_allows_result_to_commit_but_prevents_another_claim(engine):
    """若停止回滚在途结果或仍领取下一点，用户无法安全暂停任务。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    request_started = asyncio.Event()
    release_request = asyncio.Event()
    gateways = []

    async def wait_for_release():
        request_started.set()
        await release_request.wait()

    def gateway_factory():
        gateway = Gateway(_payload({"deptId": 99, "deptName": "在途网点", "models": []}),
                          on_request=wait_for_release)
        gateways.append(gateway)
        return gateway

    service = DepartmentDiscoveryService(factory, gateway_factory)
    run = service.create_run(
        city_id, preset="quick", max_requests=5,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    task = asyncio.create_task(service.process_next_point(run.id))
    await request_started.wait()
    service.stop(run.id)
    release_request.set()
    await task
    await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        completed = session.scalar(select(func.count()).select_from(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.COMPLETED))

    assert stored.status == DepartmentDiscoveryStatus.STOPPED
    assert stored.request_count == 1
    assert stored.completed_point_count == 1
    assert completed == 1
    assert len(gateways) == 1


@pytest.mark.asyncio
async def test_gateway_and_parse_failures_are_safe_terminal_points_and_do_not_leak_details(engine):
    """若外部或解析异常回滚领取状态，点会永久 RUNNING 或错误会泄漏原文。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    outcomes = iter([
        ZucheGatewayError("secret-cookie=should-not-leak"),
        {"code": 1, "content": "bad"},
    ])
    service = DepartmentDiscoveryService(factory, lambda: Gateway(next(outcomes)))
    run = service.create_run(
        city_id, preset="quick", max_requests=2,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    await service.process_next_point(run.id)
    await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        failed = session.scalars(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.FAILED,
        ).order_by(DepartmentDiscoveryPoint.id)).all()

    assert stored.request_count == 2
    assert stored.completed_point_count == 2
    assert stored.status == DepartmentDiscoveryStatus.COMPLETED_LIMIT
    assert len(failed) == 2
    assert all(point.error_summary for point in failed)
    assert "secret-cookie" not in " ".join(point.error_summary for point in failed)


@pytest.mark.asyncio
async def test_gateway_enter_failure_keeps_reserved_request_quota(engine):
    """若 gateway 创建失败退还额度，持续初始化故障可无限领取点位。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    class BrokenGateway:
        async def __aenter__(self):
            raise RuntimeError("gateway initialization failed")

        async def __aexit__(self, *_):
            return None

    service = DepartmentDiscoveryService(factory, BrokenGateway)
    run = service.create_run(
        city_id, preset="quick", max_requests=1,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    result = await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        point = session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.attempt_count == 1))

    assert result == "FAILED"
    assert stored.request_count == 1
    assert stored.completed_point_count == 1
    assert stored.status == DepartmentDiscoveryStatus.COMPLETED_LIMIT
    assert point.status == DepartmentDiscoveryPointStatus.FAILED


@pytest.mark.asyncio
async def test_transient_server_error_retries_then_persists_success(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id
    outcomes = iter([_http_error(503), _payload({
        "deptId": 703, "deptName": "重试成功网点", "workTime": "08:00-21:00", "models": []})])
    gateways = []
    sleeps = []

    def gateway_factory():
        gateway = Gateway(next(outcomes))
        gateways.append(gateway)
        return gateway

    async def record_sleep(delay):
        sleeps.append(delay)

    service = DepartmentDiscoveryService(factory, gateway_factory, sleep=record_sleep)
    run = service.create_run(city_id, preset="quick", max_requests=3,
                             pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    result = await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        point = session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.attempt_count == 2))
        department = session.scalar(select(Department).where(Department.zuche_dept_id == 703))
    assert result == "COMPLETED"
    assert len(gateways) == 2
    assert sleeps == [1]
    assert stored.request_count == 2
    assert point.status == DepartmentDiscoveryPointStatus.COMPLETED
    assert department.business_hours == "08:00-21:00"


@pytest.mark.asyncio
async def test_transient_error_retries_are_bounded_and_respect_request_limit(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id
    gateways = []
    sleeps = []

    def gateway_factory():
        gateway = Gateway(_http_error(503))
        gateways.append(gateway)
        return gateway

    async def record_sleep(delay):
        sleeps.append(delay)

    service = DepartmentDiscoveryService(factory, gateway_factory, sleep=record_sleep)
    run = service.create_run(city_id, preset="quick", max_requests=3,
                             pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    result = await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        point = session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.attempt_count == 3))
    assert result == "FAILED"
    assert len(gateways) == 3
    assert sleeps == [1, 2]
    assert stored.request_count == stored.max_requests == 3
    assert stored.status == DepartmentDiscoveryStatus.COMPLETED_LIMIT
    assert point.status == DepartmentDiscoveryPointStatus.FAILED


@pytest.mark.asyncio
async def test_stopping_during_retry_backoff_prevents_another_request(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id
    gateways = []
    service = None
    run_id = None

    def gateway_factory():
        gateway = Gateway(_http_error(503))
        gateways.append(gateway)
        return gateway

    async def stop_during_sleep(_delay):
        service.stop(run_id)

    service = DepartmentDiscoveryService(factory, gateway_factory, sleep=stop_during_sleep)
    run = service.create_run(city_id, preset="quick", max_requests=3,
                             pickup_time=PICKUP_TIME, return_time=RETURN_TIME)
    run_id = run.id

    result = await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
    assert result == "INACTIVE"
    assert len(gateways) == 1
    assert stored.request_count == 1
    assert stored.status == DepartmentDiscoveryStatus.STOPPED


@pytest.mark.asyncio
async def test_no_pending_point_finishes_run_and_terminal_points_count_as_completed(engine):
    """若失败点不计入完成数或空队列不结束，进度会永久落后。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = _city(session)
        repo = DepartmentDiscoveryRepository(session)
        run = repo.create_run(
            city_id=city.id, preset="quick", radius_km=35, spacing_km=10,
            max_requests=5, pickup_time=PICKUP_TIME, return_time=RETURN_TIME)
        repo.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
        run.planned_point_count = 1
        repo.mark_running(run)
        session.commit()
        run_id = run.id

    service = DepartmentDiscoveryService(
        factory, lambda: Gateway(ZucheGatewayError("temporary upstream failure")))
    await service.process_next_point(run_id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run_id)

    assert stored.request_count == 1
    assert stored.completed_point_count == 1
    assert stored.status == DepartmentDiscoveryStatus.COMPLETED_NO_NEW


@pytest.mark.asyncio
async def test_cancelled_request_is_re_raised_and_does_not_leave_point_running(engine):
    """若 CancelledError 被当普通失败吞掉，关停语义会失真；若不清理则点会卡死。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    started = asyncio.Event()

    async def never_returns():
        started.set()
        await asyncio.Event().wait()

    service = DepartmentDiscoveryService(
        factory, lambda: Gateway(_payload(), on_request=never_returns))
    run = service.create_run(
        city_id, preset="quick", max_requests=5,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    task = asyncio.create_task(service.process_next_point(run.id))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with factory() as session:
        point = session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.attempt_count == 1))
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)

    assert point.status == DepartmentDiscoveryPointStatus.PENDING
    assert stored.request_count == 1
    assert stored.completed_point_count == 0


@pytest.mark.asyncio
async def test_finalize_database_failure_rolls_back_departments_and_compensates_running_point(
        engine, monkeypatch):
    """若最终落库失败未使用新事务补偿，已领取点会永久停在 RUNNING。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    service = DepartmentDiscoveryService(
        factory,
        lambda: Gateway(_payload({"deptId": 701, "deptName": "应回滚网点", "models": []})),
    )
    run = service.create_run(
        city_id, preset="quick", max_requests=5,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    original = DepartmentDiscoveryRepository.finalize_point
    calls = 0

    def fail_first_finalize(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("UPDATE", {}, RuntimeError("database unavailable"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DepartmentDiscoveryRepository, "finalize_point", fail_first_finalize)

    result = await service.process_next_point(run.id)

    with factory() as session:
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        point = session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.attempt_count == 1))
        department_count = session.scalar(select(func.count()).select_from(Department))

    assert result == "FAILED"
    assert calls == 2
    assert point.status == DepartmentDiscoveryPointStatus.FAILED
    assert point.error_summary == "网点发现结果保存失败"
    assert stored.request_count == 1
    assert stored.completed_point_count == 1
    assert department_count == 0


@pytest.mark.asyncio
async def test_expired_point_is_failed_at_limit_after_both_database_compensations_fail(
        engine, monkeypatch):
    """max=1 且两次补偿失败后，租约到期只能终结旧点，绝不能再次外呼。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city_id = _city(session).id

    gateways = []

    def gateway_factory():
        gateway = Gateway(_payload({"deptId": 702, "deptName": "租约恢复网点", "models": []}))
        gateways.append(gateway)
        return gateway

    service = DepartmentDiscoveryService(factory, gateway_factory)
    run = service.create_run(
        city_id, preset="quick", max_requests=1,
        pickup_time=PICKUP_TIME, return_time=RETURN_TIME)

    original = DepartmentDiscoveryRepository.finalize_point
    failures = 0

    def fail_result_and_both_compensations(self, *args, **kwargs):
        nonlocal failures
        failures += 1
        if failures <= 3:
            raise OperationalError("UPDATE", {}, RuntimeError("database unavailable"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        DepartmentDiscoveryRepository, "finalize_point",
        fail_result_and_both_compensations)

    first_result = await service.process_next_point(run.id)

    with factory() as session:
        stuck = session.scalar(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.attempt_count == 1))
        assert stuck.status == DepartmentDiscoveryPointStatus.RUNNING
        stuck.last_scanned_at = datetime.now(UTC) - timedelta(seconds=121)
        session.commit()

    second_result = await service.process_next_point(run.id)

    with factory() as session:
        recovered = session.get(DepartmentDiscoveryPoint, stuck.id)
        stored = DepartmentDiscoveryRepository(session).get_run(run.id)
        department_count = session.scalar(select(func.count()).select_from(Department))

    assert first_result == "DATABASE_ERROR"
    assert second_result == "LIMIT"
    assert failures == 3
    assert len(gateways) == 1
    assert recovered.status == DepartmentDiscoveryPointStatus.FAILED
    assert recovered.attempt_count == 1
    assert stored.request_count == 1
    assert stored.completed_point_count == 1
    assert stored.status == DepartmentDiscoveryStatus.COMPLETED_LIMIT
    assert department_count == 0
