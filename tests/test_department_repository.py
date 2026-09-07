from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, current_thread
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    City,
    Department,
    DepartmentDiscoveryPoint,
    DepartmentDiscoveryPointStatus,
    DepartmentDiscoveryRun,
    DepartmentDiscoveryStatus,
)
from app.repositories.departments import (
    DepartmentDiscoveryRepository,
    DepartmentRepository,
    InvalidDiscoveryTransition,
)
from app.departments.service import DepartmentDiscoveryService


@pytest.fixture
def city(session):
    item = City(
        zuche_city_id="14",
        name="广州",
        latitude=23.1291,
        longitude=113.2644,
        enabled=True,
    )
    session.add(item)
    session.flush()
    return item


def test_department_upsert_keeps_identity_first_seen_and_latest_fields(session, city):
    """若改为每次插入新行，此测试应失败。"""
    repo = DepartmentRepository(session)
    first = repo.upsert(city, {
        "deptId": 88,
        "deptName": "旧名",
        "deptAddress": "旧地址",
        "lat": 23.1000001,
        "lon": 113.2000001,
    })
    first_seen_at = first.first_seen_at

    second = repo.upsert(city, {
        "deptId": 88,
        "deptName": "新名",
        "deptAddress": "新地址",
        "lat": 23.2000001,
        "lon": 113.3000001,
        "district": "天河区",
        "businessHours": "09:00-18:00",
        "is24Hour": True,
        "selfServicePickup": True,
    })
    session.commit()

    assert second.id == first.id
    assert second.name == "新名"
    assert second.address == "新地址"
    assert float(second.latitude) == 23.2
    assert float(second.longitude) == 113.3
    assert second.district == "天河区"
    assert second.business_hours == "09:00-18:00"
    assert second.is_open_24h is True
    assert second.self_service_pickup is True
    assert second.first_seen_at == first_seen_at
    assert second.first_seen_at.tzinfo is not None
    assert second.last_seen_at >= first_seen_at


def test_department_upsert_does_not_remove_a_department_missing_from_later_response(session, city):
    """若后续响应清空目录或停用旧行，此测试应失败。"""
    repo = DepartmentRepository(session)
    first = repo.upsert(city, {"deptId": 88, "deptName": "保留网点"})
    repo.upsert(city, {"deptId": 89, "deptName": "另一网点"})
    session.commit()

    retained = session.get(Department, first.id)

    assert retained is not None
    assert retained.active_state != "MANUALLY_DISABLED"


def test_running_discovery_is_marked_interrupted_on_startup(session, city):
    """若启动后继续运行旧任务，此测试应失败。"""
    repo = DepartmentDiscoveryRepository(session)
    run = repo.create_run(
        city_id=city.id,
        preset="quick",
        radius_km=35,
        spacing_km=10,
        max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repo.mark_running(run)

    assert repo.interrupt_stale_runs() == 1
    assert repo.get_run(run.id).status == DepartmentDiscoveryStatus.INTERRUPTED
    assert repo.mark_interrupted(run) is run


def test_interrupted_run_requeues_claimed_point_without_losing_attempt_or_error(session, city):
    """若启动恢复遗留 RUNNING 点，继续任务会永久卡住。"""
    repo = DepartmentDiscoveryRepository(session)
    run = repo.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    point = repo.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repo.mark_running(run)
    claimed = repo.next_pending_point(run.id)
    old_claim_token = claimed.claim_token
    claimed.error_summary = "上次请求已安全中断"
    session.flush()

    assert repo.interrupt_stale_runs() == 1
    assert point.status == DepartmentDiscoveryPointStatus.PENDING
    assert old_claim_token is not None
    assert point.claim_token is None
    assert point.attempt_count == 1
    assert point.error_summary == "上次请求已安全中断"

    repo.mark_running(run)
    resumed_claim = repo.next_pending_point(run.id)

    assert resumed_claim.id == claimed.id
    assert resumed_claim.attempt_count == 2
    assert resumed_claim.claim_token != old_claim_token


def test_active_city_run_is_mutually_exclusive_and_stopped_run_can_resume(session, city):
    """若同城同时有两个待运行任务或停止任务不能恢复，此测试应失败。"""
    repo = DepartmentDiscoveryRepository(session)
    run = repo.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(InvalidDiscoveryTransition, match="活动.*任务"):
        repo.create_run(
            city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
            pickup_time=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
            return_time=datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
        )
    repo.mark_running(run)
    repo.mark_stopped(run)
    assert repo.mark_stopped(run) is run
    assert repo.mark_running(run).status == DepartmentDiscoveryStatus.RUNNING


def test_discovery_points_are_unique_per_run_at_six_decimal_places(session, city):
    """若点位没有量化去重，任务会重复消耗请求配额。"""
    repo = DepartmentDiscoveryRepository(session)
    run = repo.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )

    first = repo.add_point(run.id, latitude=23.12910004, longitude=113.26440004, source="GRID")
    second = repo.add_point(run.id, latitude=23.12910049, longitude=113.26440049, source="GRID")
    session.commit()

    assert second.id == first.id
    assert float(first.latitude) == 23.1291
    assert float(first.longitude) == 113.2644


def test_illegal_completed_run_resume_is_rejected(session, city):
    """若完成任务仍能恢复，会破坏任务状态机。"""
    repo = DepartmentDiscoveryRepository(session)
    run = repo.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    run.status = DepartmentDiscoveryStatus.COMPLETED_LIMIT
    session.flush()

    with pytest.raises(InvalidDiscoveryTransition, match="不能恢复"):
        repo.mark_running(run)


def test_resuming_after_another_run_becomes_active_returns_a_domain_error(session, city):
    """若恢复撞上同城活动任务而泄漏数据库唯一键错误，此测试应失败。"""
    repo = DepartmentDiscoveryRepository(session)
    stopped = repo.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repo.mark_running(stopped)
    repo.mark_stopped(stopped)
    repo.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(InvalidDiscoveryTransition, match="活动.*任务"):
        repo.mark_running(stopped)


@pytest.mark.parametrize(("field", "value"), [
    ("radius_km", 0),
    ("spacing_km", 0),
    ("max_requests", 0),
])
def test_create_run_rejects_non_positive_limits(session, city, field, value):
    """若允许零或负覆盖参数，后续规划会产生无效或无限任务。"""
    kwargs = {"radius_km": 35, "spacing_km": 10, "max_requests": 80}
    kwargs[field] = value

    with pytest.raises(ValueError, match="必须大于 0"):
        DepartmentDiscoveryRepository(session).create_run(
            city_id=city.id, preset="quick", **kwargs,
            pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
            return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
        )


def test_create_run_rejects_equal_pickup_and_return_time(session, city):
    """若租期边界相等仍可写入，任务请求参数无效。"""
    moment = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="晚于"):
        DepartmentDiscoveryRepository(session).create_run(
            city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
            pickup_time=moment, return_time=moment,
        )


def test_database_rejects_negative_discovery_counters(session, city):
    """若迁移遗漏 CHECK，绕过仓储的写入会持久化负计数。"""
    run = DepartmentDiscoveryRun(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
        request_count=-1,
    )
    session.add(run)

    with pytest.raises(IntegrityError):
        session.flush()


def test_sqlite_without_returning_supports_department_run_and_point_writes():
    """若 SQLite 没有 RETURNING，仓储仍须按稳定唯一键读回已写入对象。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    engine.dialect.insert_returning = False
    for table in (City.__table__, Department.__table__, DepartmentDiscoveryRun.__table__, DepartmentDiscoveryPoint.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as sqlite_session:
        city = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644, enabled=True)
        sqlite_session.add(city)
        sqlite_session.flush()
        department = DepartmentRepository(sqlite_session).upsert(city, {"deptId": 88, "deptName": "SQLite 网点"})
        run = DepartmentDiscoveryRepository(sqlite_session).create_run(
            city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=80,
            pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
            return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
        )
        point = DepartmentDiscoveryRepository(sqlite_session).add_point(
            run.id, latitude=23.1291, longitude=113.2644, source="GRID")
        DepartmentDiscoveryRepository(sqlite_session).mark_running(run)
        claim = DepartmentDiscoveryRepository(sqlite_session).claim_next_point(run.id).claim
        sqlite_session.commit()

        assert department.zuche_dept_id == 88
        assert run.id is not None
        assert point.id is not None
        assert isinstance(claim.claim_token, UUID)
        assert sqlite_session.get(DepartmentDiscoveryPoint, point.id).claim_token == claim.claim_token
    engine.dispose()


def test_concurrent_department_upserts_keep_one_row(engine, session, city):
    """若回退为先查后插，并发写入应触发唯一键错误。"""
    barrier = Barrier(2)

    session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def write(name):
        with factory() as worker_session:
            worker_city = worker_session.scalar(select(City).where(City.id == city.id))
            barrier.wait(timeout=5)
            DepartmentRepository(worker_session).upsert(worker_city, {"deptId": 901, "deptName": name})
            worker_session.commit()

    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in (pool.submit(write, "并发网点 A"), pool.submit(write, "并发网点 B")):
            try:
                future.result(timeout=10)
            except Exception as error:  # pragma: no cover - assertion below captures the cause
                errors.append(error)

    assert errors == []
    with factory() as verify_session:
        assert verify_session.scalar(select(Department).where(Department.zuche_dept_id == 901)) is not None
        assert len(verify_session.scalars(select(Department).where(Department.zuche_dept_id == 901)).all()) == 1


def test_concurrent_discovery_claims_leave_only_one_running_point(engine, session, city):
    """若领取未锁住任务行，并发 worker 会同时外呼并越过请求上限。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=1,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repository.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.add_point(run.id, latitude=23.2, longitude=113.3, source="GRID")
    repository.mark_running(run)
    session.commit()

    barrier = Barrier(2)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def claim():
        with factory() as worker_session:
            barrier.wait(timeout=5)
            result = DepartmentDiscoveryRepository(worker_session).claim_next_point(run.id)
            worker_session.commit()
            return result.state

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = sorted(future.result(timeout=10) for future in (
            pool.submit(claim), pool.submit(claim)))

    with factory() as verify_session:
        running_count = len(verify_session.scalars(select(DepartmentDiscoveryPoint).where(
            DepartmentDiscoveryPoint.run_id == run.id,
            DepartmentDiscoveryPoint.status == DepartmentDiscoveryPointStatus.RUNNING,
        )).all())

    assert states == ["BUSY", "CLAIMED"]
    assert running_count == 1


def test_stop_waits_for_inflight_finalize_and_rejects_the_new_terminal_state(
        engine, session, city):
    """若停止先普通读取，worker 提交终态后会被陈旧 RUNNING 覆盖成 STOPPED。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=5,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repository.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.mark_running(run)
    session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    worker_session = factory()
    worker_repository = DepartmentDiscoveryRepository(worker_session)
    claim = worker_repository.claim_next_point(run.id).claim
    worker_session.commit()
    worker_repository.finalize_point(
        claim.point_id, claim_token=claim.claim_token, succeeded=True)

    transition_waiting = Event()

    def notice_blocking_transition(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.upper().split())
        if (current_thread().name.startswith("stale-stop")
                and ("FOR UPDATE" in normalized
                     or normalized.startswith("UPDATE DEPARTMENT_DISCOVERY_RUNS"))):
            transition_waiting.set()

    event.listen(engine, "before_cursor_execute", notice_blocking_transition)
    try:
        service = DepartmentDiscoveryService(factory, lambda: None)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-stop") as pool:
            stopped = pool.submit(service.stop, run.id)
            assert transition_waiting.wait(timeout=5)
            worker_session.commit()
            with pytest.raises(InvalidDiscoveryTransition, match="只有运行中"):
                stopped.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", notice_blocking_transition)
        worker_session.close()

    with factory() as verify_session:
        stored = DepartmentDiscoveryRepository(verify_session).get_run(run.id)

    assert stored.status == DepartmentDiscoveryStatus.COMPLETED_NO_NEW
    with pytest.raises(InvalidDiscoveryTransition, match="不能恢复"):
        DepartmentDiscoveryService(factory, lambda: None).resume(run.id)


def test_stop_that_locks_first_is_preserved_when_inflight_point_finishes(
        engine, session, city):
    """停止先持有任务锁时，在途 worker 仍须落点位终态但不得恢复任务状态。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=5,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    point = repository.add_point(
        run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.mark_running(run)
    claim = repository.claim_next_point(run.id).claim
    session.commit()

    stop_flushed = Event()
    release_stop = Event()
    worker_waiting = Event()

    class PausingStopSession(Session):
        def commit(self):
            if current_thread().name.startswith("stop-first"):
                stop_flushed.set()
                assert release_stop.wait(timeout=5)
            return super().commit()

    stop_factory = sessionmaker(
        bind=engine, class_=PausingStopSession, expire_on_commit=False)
    worker_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def notice_worker_run_lock(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.upper().split())
        if (current_thread().name.startswith("finish-after-stop")
                and "DEPARTMENT_DISCOVERY_RUNS" in normalized
                and "FOR UPDATE" in normalized):
            worker_waiting.set()

    def finalize():
        with worker_factory() as worker_session:
            DepartmentDiscoveryRepository(worker_session).finalize_point(
                claim.point_id, claim_token=claim.claim_token, succeeded=True)
            worker_session.commit()

    event.listen(engine, "before_cursor_execute", notice_worker_run_lock)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stop-first") as stop_pool, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="finish-after-stop") as worker_pool:
            stopped = stop_pool.submit(
                DepartmentDiscoveryService(stop_factory, lambda: None).stop, run.id)
            assert stop_flushed.wait(timeout=5)
            finished = worker_pool.submit(finalize)
            assert worker_waiting.wait(timeout=5)
            release_stop.set()
            assert stopped.result(timeout=10).status == DepartmentDiscoveryStatus.STOPPED
            finished.result(timeout=10)
    finally:
        release_stop.set()
        event.remove(engine, "before_cursor_execute", notice_worker_run_lock)

    with worker_factory() as verify_session:
        stored_run = DepartmentDiscoveryRepository(verify_session).get_run(run.id)
        stored_point = verify_session.get(DepartmentDiscoveryPoint, point.id)

    assert stored_run.status == DepartmentDiscoveryStatus.STOPPED
    assert stored_run.request_count == 1
    assert stored_run.completed_point_count == 1
    assert stored_point.status == DepartmentDiscoveryPointStatus.COMPLETED


def test_claim_records_attempt_start_time_and_expired_running_point_is_reclaimed(
        session, city):
    """若领取不记开始时间或不回收过期租约，数据库故障后点会永久 RUNNING。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=5,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repository.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.mark_running(run)
    before_claim = datetime.now(UTC)

    first = repository.claim_next_point(run.id).claim
    after_claim = datetime.now(UTC)

    point = session.get(DepartmentDiscoveryPoint, first.point_id)
    assert before_claim <= point.last_scanned_at <= after_claim
    point.last_scanned_at = datetime.now(UTC) - timedelta(seconds=121)
    session.flush()

    second = repository.claim_next_point(run.id)

    assert second.state == "CLAIMED"
    assert second.claim.point_id == first.point_id
    assert point.attempt_count == 2


def test_unexpired_running_point_keeps_claim_busy(session, city):
    """若未满安全租约也回收点位，同一个匿名请求会被重复发出。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=5,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repository.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.mark_running(run)
    first = repository.claim_next_point(run.id).claim
    point = session.get(DepartmentDiscoveryPoint, first.point_id)
    point.last_scanned_at = datetime.now(UTC) - timedelta(seconds=30)
    session.flush()

    second = repository.claim_next_point(run.id)

    assert second.state == "BUSY"
    assert point.attempt_count == 1


def test_expired_old_worker_token_cannot_finalize_the_new_claim(session, city):
    """若落库不校验领取 token，超时旧 worker 会终结新 worker 正在处理的尝试。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=5,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repository.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.mark_running(run)
    first_started_at = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)

    old_claim = repository.claim_next_point(run.id, now=first_started_at).claim
    new_claim = repository.claim_next_point(
        run.id, now=first_started_at + timedelta(seconds=121)).claim

    assert old_claim.claim_token != new_claim.claim_token
    assert repository.get_run(run.id).request_count == 2

    with pytest.raises(InvalidDiscoveryTransition, match="领取已失效"):
        repository.finalize_point(
            old_claim.point_id,
            claim_token=old_claim.claim_token,
            succeeded=True,
        )

    point = session.get(DepartmentDiscoveryPoint, old_claim.point_id)
    assert point.status == DepartmentDiscoveryPointStatus.RUNNING
    assert point.claim_token == new_claim.claim_token
    assert repository.get_run(run.id).completed_point_count == 0

    repository.finalize_point(
        new_claim.point_id,
        claim_token=new_claim.claim_token,
        succeeded=True,
    )

    assert point.status == DepartmentDiscoveryPointStatus.COMPLETED
    assert point.claim_token is None
    assert repository.get_run(run.id).completed_point_count == 1


def test_claim_reserves_request_quota_before_network_and_fresh_claim_stays_busy(
        session, city):
    """若额度到 finalize 才计数，gateway 进入失败或并发领取会绕过 max_requests。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=1,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repository.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.mark_running(run)
    started_at = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)

    first = repository.claim_next_point(run.id, now=started_at)
    second = repository.claim_next_point(
        run.id, now=started_at + timedelta(seconds=30))

    assert first.state == "CLAIMED"
    assert repository.get_run(run.id).request_count == 1
    assert second.state == "BUSY"
    assert repository.get_run(run.id).request_count == 1


def test_expired_claim_at_request_limit_becomes_failed_terminal_point(session, city):
    """若额度已满仍重排过期点，任务会留下无法再请求的 RUNNING/PENDING 假进度。"""
    repository = DepartmentDiscoveryRepository(session)
    run = repository.create_run(
        city_id=city.id, preset="quick", radius_km=35, spacing_km=10, max_requests=1,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    repository.add_point(run.id, latitude=23.1291, longitude=113.2644, source="GRID")
    repository.mark_running(run)
    started_at = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    claim = repository.claim_next_point(run.id, now=started_at).claim

    result = repository.claim_next_point(
        run.id, now=started_at + timedelta(seconds=121))

    point = session.get(DepartmentDiscoveryPoint, claim.point_id)
    assert result.state == "LIMIT"
    assert point.status == DepartmentDiscoveryPointStatus.FAILED
    assert point.claim_token is None
    assert point.error_summary == "发现点领取超时且请求额度已用完"
    assert run.request_count == 1
    assert run.completed_point_count == 1
    assert run.status == DepartmentDiscoveryStatus.COMPLETED_LIMIT
