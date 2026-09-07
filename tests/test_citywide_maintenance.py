from datetime import UTC, datetime, timedelta

from app.maintenance import CitywideDetailMaintenance, ModelSearchMaintenance
from app.models import (
    City,
    CitywideModelSummary,
    CitywideOffer,
    CitywideRawPayload,
    CitywideScanPoint,
    CitywideScanRun,
    Department,
    ModelSearchOffer,
    ModelSearchRun,
    ModelSearchRunStatus,
    ModelSearchSample,
    ModelSearchSampleKind,
    ModelSearchTarget,
    PersonalState,
    PersonalVehicleState,
    VehicleModel,
)


def test_citywide_maintenance_deletes_old_details_but_keeps_permanent_data(session):
    """若维护误删汇总、车型或人工状态，永久车型库会违背只增不删。"""
    cutoff = datetime(2026, 7, 4, 12, tzinfo=UTC)
    city = City(zuche_city_id="14", name="广州", latitude=23.1, longitude=113.4)
    session.add(city)
    session.flush()
    department = Department(
        city_id=city.id, zuche_dept_id=79340, name="鱼珠",
        latitude=23.101610, longitude=113.432649)
    model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
    session.add_all([department, model])
    session.flush()
    run = CitywideScanRun(
        city_id=city.id,
        pickup_time=cutoff,
        return_time=cutoff + timedelta(days=1))
    session.add(run)
    session.flush()
    point = CitywideScanPoint(run_id=run.id, department_id=department.id)
    session.add(point)
    session.flush()
    old = cutoff - timedelta(seconds=1)
    recent = cutoff
    old_offer = CitywideOffer(
        run_id=run.id, vehicle_model_id=model.id, department_id=department.id,
        source_point_id=point.id, first_observed_at=old, last_observed_at=old)
    summary = CitywideModelSummary(run_id=run.id, vehicle_model_id=model.id)
    state = PersonalVehicleState(
        vehicle_model_id=model.id, state=PersonalState.WANT_TO_RENT)
    old_raw = CitywideRawPayload(
        run_id=run.id, point_id=point.id, payload=b"old", captured_at=old)
    recent_raw = CitywideRawPayload(
        run_id=run.id, point_id=point.id, payload=b"recent", captured_at=recent)
    session.add_all([old_offer, summary, state, old_raw, recent_raw])
    session.commit()
    model_id, summary_id = model.id, summary.id

    result = CitywideDetailMaintenance(session).purge(cutoff, batch_size=1)

    assert result == {"offers": 1, "raw_payloads": 1}
    assert session.get(VehicleModel, model_id) is not None
    assert session.get(CitywideModelSummary, summary_id) is not None
    assert session.get(PersonalVehicleState, model_id) is not None
    assert session.get(CitywideRawPayload, recent_raw.id) is not None


def test_model_search_maintenance_removes_old_task_but_keeps_vehicle_library(session):
    """找车任务到期后应清掉明细，同时永久车型和新任务必须保留。"""
    cutoff = datetime(2026, 7, 4, 12, tzinfo=UTC)
    city = City(zuche_city_id="14", name="广州")
    model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
    department = Department(
        zuche_dept_id=79340, name="鱼珠", latitude=23.101610, longitude=113.432649)
    session.add_all([city, model, department])
    session.flush()
    department.city_id = city.id
    old_run = ModelSearchRun(
        city_id=city.id,
        status=ModelSearchRunStatus.COMPLETED,
        target_fingerprint="old",
        requested_names=[model.name],
        created_at=cutoff - timedelta(seconds=1),
        completed_at=cutoff - timedelta(seconds=1),
    )
    recent_run = ModelSearchRun(
        city_id=city.id,
        status=ModelSearchRunStatus.COMPLETED,
        target_fingerprint="recent",
        requested_names=[model.name],
        created_at=cutoff,
        completed_at=cutoff,
    )
    session.add_all([old_run, recent_run])
    session.flush()
    target = ModelSearchTarget(
        run_id=old_run.id, vehicle_model_id=model.id,
        requested_name=model.name, zuche_model_id=model.zuche_model_id)
    sample = ModelSearchSample(
        run_id=old_run.id,
        anchor_department_id=department.id,
        kind=ModelSearchSampleKind.BASE,
        pickup_time=cutoff + timedelta(days=1),
        return_time=cutoff + timedelta(days=2),
    )
    session.add_all([target, sample])
    session.flush()
    session.add(ModelSearchOffer(
        run_id=old_run.id,
        sample_id=sample.id,
        vehicle_model_id=model.id,
        department_id=department.id,
        book_flag=True,
    ))
    session.commit()
    model_id, recent_id = model.id, recent_run.id

    result = ModelSearchMaintenance(session).purge(cutoff, batch_size=1)

    assert result == {"offers": 1, "samples": 1, "targets": 1, "runs": 1}
    assert session.get(ModelSearchRun, old_run.id) is None
    assert session.get(ModelSearchRun, recent_id) is not None
    assert session.get(VehicleModel, model_id) is not None
