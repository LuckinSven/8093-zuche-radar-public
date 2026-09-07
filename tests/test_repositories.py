from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker
from app.domain import ParsedDepartment, ParsedScan, ScanQuery
from app.models import City, Department
from app.repositories import ScanRepository


def test_same_model_name_with_two_model_ids_creates_two_models(session):
    repository = ScanRepository(session)

    repository.upsert_vehicle_model(model_id=100, model_name="比亚迪秦PLUS")
    repository.upsert_vehicle_model(model_id=101, model_name="比亚迪秦PLUS")
    session.commit()

    assert repository.count_vehicle_models() == 2


def test_raw_payload_purge_only_deletes_rows_older_than_60_days(session):
    repository = ScanRepository(session)
    repository.create_raw_payload(captured_at=datetime(2026, 6, 1, tzinfo=UTC), payload=b"old")
    repository.create_raw_payload(captured_at=datetime(2026, 7, 1, tzinfo=UTC), payload=b"new")
    session.commit()

    deleted = repository.purge_expired_raw_payloads(datetime(2026, 6, 30, tzinfo=UTC))
    session.commit()

    assert deleted == 1
    assert repository.count_raw_payloads() == 1


def test_concurrent_model_upserts_do_not_fail_unique_constraint(engine):
    barrier = Barrier(2)

    def synchronize_legacy_select(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "FROM vehicle_models" in statement and "vehicle_models.zuche_model_id =" in statement:
            barrier.wait(timeout=5)

    event.listen(engine, "before_cursor_execute", synchronize_legacy_select)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def write(name):
        with factory() as session:
            ScanRepository(session).upsert_vehicle_model(9001, name)
            session.commit()

    errors = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in (pool.submit(write, "并发车型A"), pool.submit(write, "并发车型B")):
                try:
                    future.result(timeout=10)
                except Exception as error:
                    errors.append(error)
    finally:
        event.remove(engine, "before_cursor_execute", synchronize_legacy_select)

    assert errors == []
    with factory() as session:
        assert ScanRepository(session).count_vehicle_models() == 1


def test_manual_scan_department_write_keeps_first_seen_and_updates_city_semantics(session):
    """若旧扫描仓储绕过网点语义，城市和发现状态会一直缺失或被错误覆盖。"""
    city = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644, enabled=True)
    session.add(city)
    session.flush()
    query = ScanQuery(
        city_id="14", location_name="广州中心", latitude=23.1291, longitude=113.2644,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
    )
    parsed = ParsedScan(
        query=query,
        departments=[ParsedDepartment(department_id=88, name="旧扫描网点", address="地址", latitude=23.1, longitude=113.2)],
        offers=[], groups=[],
    )
    repository = ScanRepository(session)
    repository.persist_success(repository.create_run(query, "MANUAL"), raw={}, parsed=parsed)
    first = session.scalar(select(Department).where(Department.zuche_dept_id == 88))
    first_seen_at = first.first_seen_at

    parsed.departments[0].name = "更新后的扫描网点"
    repository.persist_success(repository.create_run(query, "MANUAL"), raw={}, parsed=parsed)
    session.flush()
    stored = session.scalar(select(Department).where(Department.zuche_dept_id == 88))

    assert stored.city_id == city.id
    assert stored.name == "更新后的扫描网点"
    assert stored.first_seen_at == first_seen_at
    assert stored.discovery_source == "SCAN"
    assert str(stored.active_state) == "DISCOVERED"
