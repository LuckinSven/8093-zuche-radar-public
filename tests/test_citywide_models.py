from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    City,
    CitywideModelSummary,
    CitywideOffer,
    CitywidePointStatus,
    CitywideRawPayload,
    CitywideScanPoint,
    CitywideScanRun,
    CitywideScanStatus,
    Department,
    VehicleModel,
)


PICKUP = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
RETURN = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
SEEN = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def city(session):
    item = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644)
    session.add(item)
    session.flush()
    return item


@pytest.fixture
def department(session, city):
    item = Department(zuche_dept_id=79340, city_id=city.id, name="鱼珠地铁站服务点")
    session.add(item)
    session.flush()
    return item


@pytest.fixture
def vehicle_model(session):
    item = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
    session.add(item)
    session.flush()
    return item


def _run(city_id: int, status: CitywideScanStatus = CitywideScanStatus.PENDING):
    return CitywideScanRun(
        city_id=city_id,
        pickup_time=PICKUP,
        return_time=RETURN,
        status=status,
    )


def test_citywide_schema_rejects_two_active_runs_for_one_city(session, city):
    """若移除活动任务部分唯一索引，同城会并行产生两个全城任务。"""
    session.add(_run(city.id, CitywideScanStatus.RUNNING))
    session.flush()

    session.add(_run(city.id, CitywideScanStatus.PENDING))

    with pytest.raises(IntegrityError):
        session.flush()


def test_citywide_schema_rejects_invalid_rental_period(session, city):
    """若移除租期检查，取车晚于还车的任务会进入调度器。"""
    session.add(CitywideScanRun(
        city_id=city.id,
        pickup_time=RETURN,
        return_time=PICKUP,
        status=CitywideScanStatus.STOPPED,
    ))

    with pytest.raises(IntegrityError):
        session.flush()


def test_citywide_schema_rejects_duplicate_point(session, city, department):
    """若点位唯一键缺失，同一任务会重复扫描一个网点。"""
    run = _run(city.id)
    session.add(run)
    session.flush()
    session.add(CitywideScanPoint(
        run_id=run.id,
        department_id=department.id,
        status=CitywidePointStatus.PENDING,
    ))
    session.flush()
    session.add(CitywideScanPoint(run_id=run.id, department_id=department.id))

    with pytest.raises(IntegrityError):
        session.flush()


def test_citywide_schema_rejects_duplicate_offer(session, city, department, vehicle_model):
    """若报价唯一键缺失，同一车型网点会重复进入均价。"""
    run = _run(city.id)
    session.add(run)
    session.flush()
    point = CitywideScanPoint(run_id=run.id, department_id=department.id)
    session.add(point)
    session.flush()
    session.add_all([
        CitywideOffer(
            run_id=run.id,
            vehicle_model_id=vehicle_model.id,
            department_id=department.id,
            source_point_id=point.id,
        ),
        CitywideOffer(
            run_id=run.id,
            vehicle_model_id=vehicle_model.id,
            department_id=department.id,
            source_point_id=point.id,
        ),
    ])

    with pytest.raises(IntegrityError):
        session.flush()


def test_citywide_schema_rejects_duplicate_summary(session, city, vehicle_model):
    """若汇总唯一键缺失，同一租期会为车型产生多行结果。"""
    run = _run(city.id)
    session.add(run)
    session.flush()
    session.add_all([
        CitywideModelSummary(run_id=run.id, vehicle_model_id=vehicle_model.id),
        CitywideModelSummary(run_id=run.id, vehicle_model_id=vehicle_model.id),
    ])

    with pytest.raises(IntegrityError):
        session.flush()


def test_vehicle_model_keeps_permanent_catalog_fields(session):
    """若永久字段未映射，车型首次发现信息无法跨租期保存。"""
    model = VehicleModel(
        zuche_model_id=4952,
        name="比亚迪海狮05",
        first_seen_at=SEEN,
        last_seen_at=SEEN,
        latest_description="纯电 58kWh | SUV 5座",
        image_url="https://image.example/4952.png",
        energy_type="新能源",
        energy_source="UPSTREAM",
    )
    session.add(model)
    session.flush()

    assert model.first_seen_at == SEEN
    assert model.last_seen_at == SEEN
    assert model.energy_type == "新能源"
    assert model.energy_source == "UPSTREAM"


def test_citywide_raw_payload_is_bound_to_run_and_point(session, city, department):
    """若原始响应缺少任务和点位关联，60 天维护无法安全定向清理。"""
    run = _run(city.id)
    session.add(run)
    session.flush()
    point = CitywideScanPoint(run_id=run.id, department_id=department.id)
    session.add(point)
    session.flush()
    payload = CitywideRawPayload(run_id=run.id, point_id=point.id, payload=b"gzip")
    session.add(payload)
    session.flush()

    assert payload.run_id == run.id
    assert payload.point_id == point.id
