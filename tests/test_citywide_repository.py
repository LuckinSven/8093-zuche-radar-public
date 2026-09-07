from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.citywide.repository import CitywideRepository, InvalidCitywideTransition
from app.models import (
    City,
    CitywideModelSummary,
    CitywideOffer,
    CitywidePointStatus,
    CitywideScanPoint,
    CitywideScanStatus,
    Department,
    DepartmentActivityState,
    VehicleModel,
)


PICKUP = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
RETURN = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def guangzhou(session):
    item = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644)
    session.add(item)
    session.flush()
    return item


def _department(city_id: int, number: int, *, latitude: float | None = None,
                longitude: float | None = None, state=DepartmentActivityState.RECENTLY_SEEN):
    return Department(
        zuche_dept_id=70_000 + number,
        city_id=city_id,
        name=f"广州测试网点{number}",
        latitude=latitude if latitude is not None else 23.10 + number / 100_000,
        longitude=longitude if longitude is not None else 113.43 + number / 100_000,
        active_state=state,
    )


def _create_run_with_departments(session, city, count=5):
    session.add_all([_department(city.id, number) for number in range(count)])
    session.flush()
    return CitywideRepository(session).create_run(
        city_id=city.id,
        pickup_time=PICKUP,
        return_time=RETURN,
    )


def test_create_run_snapshots_only_enabled_city_departments_with_coordinates(session, guangzhou):
    """若快照混入外市、停用或无坐标网点，全城进度和完整性都会失真。"""
    session.add_all([_department(guangzhou.id, number) for number in range(91)])
    session.add(_department(
        guangzhou.id, 201, state=DepartmentActivityState.MANUALLY_DISABLED))
    missing_coordinates = _department(guangzhou.id, 202)
    missing_coordinates.latitude = None
    missing_coordinates.longitude = None
    session.add(missing_coordinates)
    foshan = City(zuche_city_id="15", name="佛山", latitude=23.0215, longitude=113.1214)
    session.add(foshan)
    session.flush()
    session.add(_department(foshan.id, 203))
    session.flush()

    run = CitywideRepository(session).create_run(
        city_id=guangzhou.id,
        pickup_time=PICKUP,
        return_time=RETURN,
    )

    point_count = session.scalar(
        select(func.count()).select_from(CitywideScanPoint)
        .where(CitywideScanPoint.run_id == run.id))
    assert run.planned_point_count == 91
    assert point_count == 91


def test_claim_points_never_exceeds_three_and_never_returns_same_point_twice(session, guangzhou):
    """若领取上限或行锁失效，同一轮会超出并发 3 或重复请求网点。"""
    run = _create_run_with_departments(session, guangzhou, count=7)
    repo = CitywideRepository(session)

    first = repo.claim_points(run.id, limit=20)
    second = repo.claim_points(run.id, limit=3)

    assert first.state == "CLAIMED"
    assert len(first.claims) == 3
    assert len(second.claims) == 3
    assert len({claim.claim_token for claim in (*first.claims, *second.claims)}) == 6
    assert {claim.point_id for claim in first.claims}.isdisjoint(
        {claim.point_id for claim in second.claims})
    assert run.status == CitywideScanStatus.RUNNING
    assert run.request_count == 6


def test_interrupt_stale_runs_requeues_claimed_points_without_auto_resume(session, guangzhou):
    """若进程重启后继续旧领取，点位会永久卡在运行中或静默自动续跑。"""
    run = _create_run_with_departments(session, guangzhou, count=2)
    claim = CitywideRepository(session).claim_points(run.id, limit=1).claims[0]

    changed = CitywideRepository(session).interrupt_stale_runs()
    point = session.get(CitywideScanPoint, claim.point_id)

    assert changed == 1
    assert run.status == CitywideScanStatus.INTERRUPTED
    assert point.status == CitywidePointStatus.PENDING
    assert point.claim_token is None
    assert point.attempt_count == 1


def test_state_machine_rejects_illegal_stop_and_completed_resume(session, guangzhou):
    """若可从任意状态跳转，完成任务会被再次写入并破坏历史。"""
    run = _create_run_with_departments(session, guangzhou, count=1)
    repo = CitywideRepository(session)

    with pytest.raises(InvalidCitywideTransition, match="运行中"):
        repo.stop(run.id)

    run.status = CitywideScanStatus.COMPLETED
    session.flush()
    with pytest.raises(InvalidCitywideTransition, match="继续"):
        repo.resume(run.id)


def test_complete_point_rejects_stale_claim_token(session, guangzhou):
    """若落库不核对领取令牌，过期 worker 会覆盖恢复后的新结果。"""
    run = _create_run_with_departments(session, guangzhou, count=1)
    claim = CitywideRepository(session).claim_points(run.id, limit=1).claims[0]

    with pytest.raises(InvalidCitywideTransition, match="领取已失效"):
        CitywideRepository(session).complete_point(
            claim.point_id,
            claim_token=run.id,
            response_department_count=1,
            response_model_count=1,
        )


def test_self_anchor_offer_wins_and_summary_uses_one_price_per_department(session, guangzhou):
    """若邻近观察覆盖自身观察或报价重复，实际网点价格和均价都会错误。"""
    run = _create_run_with_departments(session, guangzhou, count=2)
    points = list(session.scalars(
        select(CitywideScanPoint).where(CitywideScanPoint.run_id == run.id)
        .order_by(CitywideScanPoint.id)))
    model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
    session.add(model)
    session.flush()
    repo = CitywideRepository(session)

    repo.upsert_offer(
        run_id=run.id,
        vehicle_model_id=model.id,
        department_id=points[0].department_id,
        source_point_id=points[1].id,
        source_is_self=False,
        source_distance_km=2.4,
        package_price=118,
    )
    repo.upsert_offer(
        run_id=run.id,
        vehicle_model_id=model.id,
        department_id=points[0].department_id,
        source_point_id=points[0].id,
        source_is_self=True,
        source_distance_km=0,
        package_price=128,
    )
    repo.upsert_offer(
        run_id=run.id,
        vehicle_model_id=model.id,
        department_id=points[1].department_id,
        source_point_id=points[1].id,
        source_is_self=True,
        source_distance_km=0,
        daily_price=100,
    )

    offers = list(session.scalars(select(CitywideOffer).order_by(CitywideOffer.department_id)))
    summary = session.scalar(select(CitywideModelSummary))
    assert len(offers) == 2
    assert offers[0].package_price == Decimal("128.00")
    assert offers[0].source_is_self is True
    assert summary.available_department_count == 2
    assert summary.average_price == Decimal("114.00")
    assert summary.minimum_price == Decimal("100.00")
    assert summary.maximum_price == Decimal("128.00")
