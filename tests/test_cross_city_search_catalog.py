from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model_search.catalog import ModelSearchCatalog
from app.model_search.domain import RentalWindow
from app.model_search.repository import ModelSearchRepository
from app.models import (
    City,
    Department,
    ModelSearchOffer,
    ModelSearchRunStatus,
    ModelSearchSample,
    ModelSearchSampleStatus,
    VehicleModel,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
PICKUP = datetime(2026, 9, 24, 9, tzinfo=SHANGHAI)


def _seed(session: Session):
    guangzhou = City(zuche_city_id="14", name="广州")
    wuhan = City(zuche_city_id="20", name="武汉")
    zhengzhou = City(zuche_city_id="31", name="郑州")
    model = VehicleModel(
        zuche_model_id=4677,
        name="小鹏P7+",
        latest_description="60kWh电动粤A牌 | 三厢5座",
    )
    session.add_all([guangzhou, wuhan, zhengzhou, model])
    session.flush()
    departments = [
        Department(city_id=wuhan.id, zuche_dept_id=86, name="武广服务点",
                   address="武汉站附近", latitude=30.61, longitude=114.42),
        Department(city_id=zhengzhou.id, zuche_dept_id=1424, name="郑州高铁东站服务点",
                   address="郑州东站附近", latitude=34.75, longitude=113.78),
    ]
    session.add_all(departments)
    session.commit()
    windows = (
        RentalWindow(PICKUP, PICKUP + timedelta(days=14)),
        RentalWindow(PICKUP + timedelta(days=1), PICKUP + timedelta(days=15)),
    )
    repository = ModelSearchRepository(session)
    run = repository.create_cross_city_run(
        vehicle_model_id=model.id,
        pickup_city_ids=[wuhan.id, zhengzhou.id],
        return_city_id=guangzhou.id,
        return_location_name="鱼珠地铁站服务点",
        windows=windows,
        rail_costs={str(wuhan.id): 600, str(zhengzhou.id): 1180},
    )
    session.flush()
    samples = list(session.scalars(select(ModelSearchSample).where(
        ModelSearchSample.run_id == run.id).order_by(
            ModelSearchSample.pickup_time, ModelSearchSample.anchor_department_id)))
    for sample in samples:
        sample.status = ModelSearchSampleStatus.COMPLETED
        sample.completed_at = PICKUP
    wuhan_first = next(sample for sample in samples
                       if sample.anchor_department_id == departments[0].id
                       and sample.pickup_time == PICKUP)
    session.add(ModelSearchOffer(
        run_id=run.id,
        sample_id=wuhan_first.id,
        vehicle_model_id=model.id,
        department_id=departments[0].id,
        daily_price=381,
        package_price=264,
        book_flag=True,
        model_description=model.latest_description,
        verified_at=PICKUP,
    ))
    run.status = ModelSearchRunStatus.COMPLETED
    run.completed_sample_count = run.planned_sample_count
    session.commit()
    return run, wuhan, zhengzhou, samples


def test_cross_city_results_group_exact_windows_and_estimate_total_cost(engine):
    """价格若不按城市租期去重或不加铁路费，会给出错误的出发城市排序。"""
    with Session(engine) as session:
        run, _, _, _ = _seed(session)

        result = ModelSearchCatalog(session).cross_city_results(run.id)

        assert [item["city_name"] for item in result["items"]] == ["武汉", "郑州"]
        wuhan = result["items"][0]
        assert wuhan["availability"] == "AVAILABLE"
        assert wuhan["rail_cost"] == 600.0
        assert wuhan["best_rate"] == 264.0
        assert wuhan["estimated_rental_total"] == 3696.0
        assert wuhan["estimated_trip_total"] == 4296.0
        assert [item["availability"] for item in wuhan["windows"]] == [
            "AVAILABLE", "NOT_FOUND"]
        assert wuhan["windows"][0]["departments"][0]["name"] == "武广服务点"
        assert result["items"][1]["availability"] == "NOT_FOUND"


def test_cross_city_results_never_call_an_unfinished_city_not_found(engine):
    """某城市仍有未执行样本时，结果必须显示扫描不完整。"""
    with Session(engine) as session:
        run, _, zhengzhou, samples = _seed(session)
        target_sample = next(sample for sample in samples
                             if session.get(Department, sample.anchor_department_id).city_id
                             == zhengzhou.id)
        target_sample.status = ModelSearchSampleStatus.PENDING
        run.status = ModelSearchRunStatus.RUNNING
        session.commit()

        result = ModelSearchCatalog(session).cross_city_results(run.id)

        by_city = {item["city_name"]: item for item in result["items"]}
        assert by_city["郑州"]["availability"] == "INCOMPLETE"
