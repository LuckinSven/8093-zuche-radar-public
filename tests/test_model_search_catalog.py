from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.model_search.catalog import ModelSearchCatalog
from app.models import (
    City,
    Department,
    ModelSearchOffer,
    ModelSearchPhase,
    ModelSearchRun,
    ModelSearchRunStatus,
    ModelSearchSample,
    ModelSearchSampleKind,
    ModelSearchSampleStatus,
    ModelSearchTarget,
    VehicleModel,
)


PICKUP = datetime(2026, 9, 5, 1, 0, tzinfo=UTC)


def _seed(session: Session, *, status=ModelSearchRunStatus.COMPLETED):
    city = City(zuche_city_id="14", name="广州")
    target = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05",
                          latest_description="纯电58kWh | SUV5座")
    missing = VehicleModel(zuche_model_id=5187, name="丰田bZ5")
    department = Department(
        zuche_dept_id=79340,
        name="鱼珠地铁站服务点",
        latitude=23.101610,
        longitude=113.432649,
    )
    session.add_all([city, target, missing, department])
    session.flush()
    department.city_id = city.id
    run = ModelSearchRun(
        city_id=city.id,
        status=status,
        phase=ModelSearchPhase.FINE,
        target_fingerprint="fingerprint",
        requested_names=["比亚迪海狮05", "丰田bZ5"],
        planned_sample_count=2,
        completed_sample_count=2 if status == ModelSearchRunStatus.COMPLETED else 1,
    )
    session.add(run)
    session.flush()
    session.add_all([
        ModelSearchTarget(run_id=run.id, vehicle_model_id=target.id,
                          requested_name=target.name, zuche_model_id=target.zuche_model_id),
        ModelSearchTarget(run_id=run.id, vehicle_model_id=missing.id,
                          requested_name=missing.name, zuche_model_id=missing.zuche_model_id),
    ])
    sample = ModelSearchSample(
        run_id=run.id,
        anchor_department_id=department.id,
        kind=ModelSearchSampleKind.BASE,
        status=ModelSearchSampleStatus.COMPLETED,
        pickup_time=PICKUP,
        return_time=PICKUP + timedelta(days=1),
        completed_at=PICKUP - timedelta(hours=1),
    )
    second = ModelSearchSample(
        run_id=run.id,
        anchor_department_id=department.id,
        kind=ModelSearchSampleKind.FINE,
        status=(ModelSearchSampleStatus.COMPLETED
                if status == ModelSearchRunStatus.COMPLETED
                else ModelSearchSampleStatus.PENDING),
        pickup_time=PICKUP + timedelta(hours=1),
        return_time=PICKUP + timedelta(days=1, hours=1),
    )
    session.add_all([sample, second])
    session.flush()
    session.add(ModelSearchOffer(
        run_id=run.id,
        sample_id=sample.id,
        vehicle_model_id=target.id,
        department_id=department.id,
        daily_price=155,
        package_price=145,
        book_flag=True,
        model_description=target.latest_description,
        distance_from_yuzhu_km=0,
        verified_at=PICKUP - timedelta(hours=1),
    ))
    session.commit()
    return run


def test_catalog_keeps_available_result_but_marks_missing_target_complete(engine):
    """完整任务应区分真实可租与确实未找到的目标车型。"""
    with Session(engine) as session:
        run = _seed(session)
        result = ModelSearchCatalog(session).results(run.id, availability="ALL")

        by_name = {item["model_name"]: item for item in result["items"]}
        assert by_name["比亚迪海狮05"]["availability"] == "AVAILABLE"
        assert by_name["比亚迪海狮05"]["minimum_price"] == 145.0
        assert by_name["比亚迪海狮05"]["available_department_count"] == 1
        assert by_name["丰田bZ5"]["availability"] == "NOT_FOUND"


def test_catalog_never_calls_unfinished_target_not_found(engine):
    """任何未完成样本都必须阻止无车结论。"""
    with Session(engine) as session:
        run = _seed(session, status=ModelSearchRunStatus.RUNNING)
        result = ModelSearchCatalog(session).results(run.id, availability="ALL")

        by_name = {item["model_name"]: item for item in result["items"]}
        assert by_name["比亚迪海狮05"]["availability"] == "AVAILABLE"
        assert by_name["丰田bZ5"]["availability"] == "INCOMPLETE"


def test_periods_return_exact_samples_variants_and_pickup_departments(engine):
    """结果展开必须保留准确租期、车型ID和实际返回网点。"""
    with Session(engine) as session:
        run = _seed(session)
        result = ModelSearchCatalog(session).periods(
            run.id, model_name="比亚迪海狮05")

        first = result["items"][0]
        assert first["pickup_time"] == PICKUP.isoformat()
        assert first["return_time"] == (PICKUP + timedelta(days=1)).isoformat()
        assert first["duration_hours"] == 24
        assert first["availability"] == "AVAILABLE"
        assert first["variants"][0]["zuche_model_id"] == 4952
        assert first["variants"][0]["departments"][0]["name"] == "鱼珠地铁站服务点"


def test_periods_filter_exact_rental_windows_by_availability(engine):
    """“只看可租租期”必须排除同一车型下未找到车辆的其他时间。"""
    with Session(engine) as session:
        run = _seed(session)

        available = ModelSearchCatalog(session).periods(
            run.id, model_name="比亚迪海狮05", availability="AVAILABLE")
        missing = ModelSearchCatalog(session).periods(
            run.id, model_name="比亚迪海狮05", availability="NOT_FOUND")

        assert available["pagination"]["total"] == 1
        assert [item["availability"] for item in available["items"]] == ["AVAILABLE"]
        assert missing["pagination"]["total"] == 1
        assert [item["availability"] for item in missing["items"]] == ["NOT_FOUND"]
