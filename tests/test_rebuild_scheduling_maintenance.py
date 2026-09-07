import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.main import create_app
from app.maintenance import RawPayloadMaintenance
from app.enrichment.domain import EnrichmentScope
from app.models import (City, CitywideScanStatus, Department, DepartmentDiscoveryStatus,
                        EnrichmentRunStatus, IntegrationSetting, ModelSearchRunStatus,
                        Probe, VehicleModel)
from app.repositories.departments import DepartmentDiscoveryRepository
from app.repositories.scans import ScanRepository
from app.scheduling import ProbeScheduler, RadarRuntime, build_probe_query


CHOOSE_CAR_FIXTURE = Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json"


def test_scheduler_only_registers_enabled_probes(session):
    city = City(zuche_city_id="test-scheduler-14", name="广州", latitude=23.1291, longitude=113.2644)
    session.add(city); session.flush()
    disabled = Probe(city_id=city.id, name="关闭", latitude=23.1, longitude=113.2, enabled=False, schedule="interval:15")
    enabled = Probe(city_id=city.id, name="开启", latitude=23.2, longitude=113.3, enabled=True, schedule="interval:30")
    session.add_all([disabled, enabled]); session.commit()

    scheduler = ProbeScheduler(session, lambda _: None)
    scheduler.reconcile()

    assert scheduler.job_ids() == [f"probe-{enabled.id}"]


def test_raw_payload_is_kept_on_day_60_and_deleted_after_it(session):
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    repository = ScanRepository(session)
    repository.create_raw_payload(now - timedelta(days=60), b"keep")
    repository.create_raw_payload(now - timedelta(days=60, seconds=1), b"delete")
    session.commit()

    deleted = RawPayloadMaintenance(session, retention_days=60).purge(now)
    session.commit()

    assert deleted == 1
    assert repository.count_raw_payloads() == 1


@pytest.mark.asyncio
async def test_application_lifespan_starts_and_stops_runtime(engine):
    events = []

    class RuntimeSpy:
        probe_scheduler = object()

        def start(self):
            events.append("start")

        def shutdown(self):
            events.append("shutdown")

    app = create_app(session_factory=sessionmaker(bind=engine), runtime_factory=lambda _: RuntimeSpy())

    async with app.router.lifespan_context(app):
        assert app.state.probe_scheduler is not None

    assert events == ["start", "shutdown"]


@pytest.mark.asyncio
async def test_scheduled_scan_uses_injectable_anonymous_client_factory_without_arguments(engine):
    class AnonymousChooseCar:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def choose_car(self, _):
            return json.loads(CHOOSE_CAR_FIXTURE.read_text(encoding="utf-8"))

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    factory_calls = []
    runtime = RadarRuntime(
        factory,
        gateway_factory=lambda: factory_calls.append("anonymous") or AnonymousChooseCar(),
    )
    query = build_probe_query(
        City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644),
        Probe(city_id=1, name="广州中心", latitude=23.1291, longitude=113.2644),
        datetime(2026, 8, 30, 8, tzinfo=UTC),
    )

    await runtime._scan(query)

    assert factory_calls == ["anonymous"]


def test_scheduled_probe_uses_next_0900_to_following_0900():
    city = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644)
    city.id = 1
    probe = Probe(city_id=1, name="广州中心", latitude=23.1291, longitude=113.2644)
    now = datetime(2026, 8, 30, 12, 30, tzinfo=UTC)

    query = build_probe_query(city, probe, now)

    assert query.pickup_time.isoformat() == "2026-08-31T09:00:00+08:00"
    assert query.return_time.isoformat() == "2026-09-01T09:00:00+08:00"


def test_runtime_interrupts_stale_discovery_and_registers_one_throttled_worker(engine):
    """若启动自动恢复旧任务或注册并发/高频 job，匿名接口会被意外连续请求。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = City(
            zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644,
            enabled=True)
        session.add(city)
        session.flush()
        repository = DepartmentDiscoveryRepository(session)
        run = repository.create_run(
            city_id=city.id, preset="quick", radius_km=35, spacing_km=10,
            max_requests=80,
            pickup_time=datetime(2026, 9, 5, 9, tzinfo=UTC),
            return_time=datetime(2026, 9, 6, 9, tzinfo=UTC),
        )
        repository.mark_running(run)
        session.commit()
        run_id = run.id

    runtime = RadarRuntime(factory, gateway_factory=lambda: pytest.fail("启动不得发起发现请求"))
    try:
        runtime.start()
        jobs = [job for job in runtime.scheduler.get_jobs()
                if job.id == "department-discovery"]

        with factory() as session:
            stored = DepartmentDiscoveryRepository(session).get_run(run_id)

        assert stored.status == DepartmentDiscoveryStatus.INTERRUPTED
        assert len(jobs) == 1
        assert jobs[0].trigger.interval.total_seconds() >= 2
        assert jobs[0].trigger.jitter is not None
        assert jobs[0].max_instances == 1
        assert jobs[0].coalesce is True
    finally:
        runtime.shutdown()


def test_department_discovery_scheduler_job_is_a_sync_wrapper_for_async_service(engine):
    """若后台线程直接返回 coroutine，发现任务不会真正执行。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = RadarRuntime(factory)
    events = []

    class ServiceSpy:
        async def process_active_run(self):
            events.append("processed")

    runtime.department_discovery_service = ServiceSpy()

    result = runtime._run_department_discovery()

    assert result is None
    assert events == ["processed"]


def test_runtime_interrupts_citywide_run_and_registers_one_worker(engine):
    """若启动自动续跑旧任务或注册多个 worker，会意外消耗匿名接口请求。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644)
        session.add(city)
        session.flush()
        session.add(Department(
            city_id=city.id,
            zuche_dept_id=79340,
            name="鱼珠地铁站服务点",
            latitude=23.101610,
            longitude=113.432649,
        ))
        session.commit()
        city_id = city.id

    runtime = RadarRuntime(factory, gateway_factory=lambda: pytest.fail("启动不得请求神州"))
    run = runtime.citywide_scan_service.create_run(
        city_id,
        datetime(2026, 9, 6, 9, tzinfo=UTC),
        datetime(2026, 9, 7, 9, tzinfo=UTC),
    )
    try:
        runtime.start()
        jobs = [job for job in runtime.scheduler.get_jobs() if job.id == "citywide-scan"]
        with factory() as session:
            status = session.get(type(run), run.id).status

        assert status == CitywideScanStatus.INTERRUPTED
        assert len(jobs) == 1
        assert jobs[0].trigger.interval.total_seconds() >= 2
        assert jobs[0].max_instances == 1
        assert jobs[0].coalesce is True
    finally:
        runtime.shutdown()


def test_citywide_scheduler_job_runs_async_service_to_completion(engine):
    """若后台线程只返回 coroutine，任务永远不会处理点位。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = RadarRuntime(factory)
    events = []

    class ServiceSpy:
        async def process_active_run(self):
            events.append("processed")

    runtime.citywide_scan_service = ServiceSpy()

    result = runtime._run_citywide_scan()

    assert result is None
    assert events == ["processed"]


def test_runtime_interrupts_enrichment_run_and_registers_one_worker(engine):
    """若重启后自动续跑 AI 任务，会在用户不知情时继续消耗额度。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([
            VehicleModel(zuche_model_id=4952, name="比亚迪海狮05"),
            IntegrationSetting(
                provider="openai_compatible_enrichment",
                enabled=True,
                secret_value="secret",
                config_json={
                    "base_url": "https://api.example.com/v1",
                    "model": "model-x",
                    "batch_size": 15,
                    "timeout_seconds": 30,
                    "max_retries": 1,
                },
            ),
        ])
        session.commit()

    runtime = RadarRuntime(factory)
    run = runtime.vehicle_enrichment_service.create_run(EnrichmentScope.PENDING_ONLY)
    try:
        runtime.start()
        jobs = [job for job in runtime.scheduler.get_jobs()
                if job.id == "vehicle-enrichment"]
        with factory() as session:
            status = session.get(type(run), run.id).status

        assert status == EnrichmentRunStatus.INTERRUPTED
        assert len(jobs) == 1
        assert jobs[0].trigger.interval.total_seconds() >= 2
        assert jobs[0].max_instances == 1
        assert jobs[0].coalesce is True
    finally:
        runtime.shutdown()


def test_enrichment_scheduler_job_runs_async_service_to_completion(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = RadarRuntime(factory)
    events = []

    class ServiceSpy:
        async def process_active_run(self):
            events.append("processed")

    runtime.vehicle_enrichment_service = ServiceSpy()

    result = runtime._run_vehicle_enrichment()

    assert result is None
    assert events == ["processed"]


def test_runtime_interrupts_model_search_run_and_registers_one_worker(engine):
    """容器重启后找车任务必须等待人工继续，不能自行消耗数千次请求。"""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        city = City(zuche_city_id="14", name="广州")
        model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
        session.add_all([city, model])
        session.flush()
        session.add(Department(
            city_id=city.id,
            zuche_dept_id=79340,
            name="鱼珠地铁站服务点",
            latitude=23.101610,
            longitude=113.432649,
        ))
        session.commit()
        city_id = city.id

    runtime = RadarRuntime(factory, gateway_factory=lambda: pytest.fail("启动不得请求神州"))
    run = runtime.model_search_service.create_run(
        city_id, ["比亚迪海狮05"],
        now=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )
    try:
        runtime.start()
        jobs = [job for job in runtime.scheduler.get_jobs() if job.id == "model-search"]
        with factory() as session:
            status = session.get(type(run), run.id).status

        assert status == ModelSearchRunStatus.INTERRUPTED
        assert len(jobs) == 1
        assert jobs[0].trigger.interval.total_seconds() >= 2
        assert jobs[0].max_instances == 1
        assert jobs[0].coalesce is True
    finally:
        runtime.shutdown()


def test_model_search_scheduler_job_runs_async_service(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = RadarRuntime(factory)
    events = []

    class ServiceSpy:
        async def process_active_run(self):
            events.append("processed")

    runtime.model_search_service = ServiceSpy()

    result = runtime._run_model_search()

    assert result is None
    assert events == ["processed"]
