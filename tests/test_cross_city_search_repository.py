from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.model_search.domain import RentalWindow
from app.model_search.repository import ModelSearchRepository
from app.models import City, Department, ModelSearchSample, ModelSearchTarget, VehicleModel


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=SHANGHAI)


def _seed(session: Session, *, pickup_department_count: int = 2):
    guangzhou = City(zuche_city_id="14", name="广州")
    wuhan = City(zuche_city_id="20", name="武汉")
    model = VehicleModel(
        zuche_model_id=4677,
        name="小鹏P7+",
        latest_description="60kWh电动粤A牌 | 三厢5座",
    )
    session.add_all([guangzhou, wuhan, model])
    session.flush()
    session.add(Department(
        city_id=guangzhou.id,
        zuche_dept_id=79340,
        name="鱼珠地铁站服务点",
        latitude=23.101610,
        longitude=113.432649,
    ))
    session.add_all([
        Department(
            city_id=wuhan.id,
            zuche_dept_id=8600 + index,
            name=f"武汉网点{index}",
            latitude=30.61 + index / 1000,
            longitude=114.42 + index / 1000,
        )
        for index in range(pickup_department_count)
    ])
    session.commit()
    return guangzhou, wuhan, model


def _windows():
    first = datetime(2026, 9, 24, 9, tzinfo=SHANGHAI)
    second = datetime(2026, 9, 25, 9, tzinfo=SHANGHAI)
    return (
        RentalWindow(first, first + timedelta(days=14)),
        RentalWindow(second, second + timedelta(days=14)),
    )


def test_cross_city_run_uses_exact_model_and_all_city_window_pairs(engine):
    """按名称扩展变体或漏掉一个城市租期组合都会污染专项搜索。"""
    with Session(engine, expire_on_commit=False) as session:
        guangzhou, wuhan, model = _seed(session)
        same_name = VehicleModel(
            zuche_model_id=5139,
            name="小鹏P7+",
            latest_description="49kWh增程 | 三厢5座",
        )
        session.add(same_name)
        session.commit()

        run = ModelSearchRepository(session).create_cross_city_run(
            vehicle_model_id=model.id,
            pickup_city_ids=[wuhan.id],
            return_city_id=guangzhou.id,
            return_location_name="鱼珠地铁站服务点",
            windows=_windows(),
            rail_costs={str(wuhan.id): 600},
            now=NOW,
        )
        session.commit()

        targets = session.scalars(select(ModelSearchTarget).where(
            ModelSearchTarget.run_id == run.id)).all()
        samples = session.scalars(select(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id)).all()
        assert [item.zuche_model_id for item in targets] == [4677]
        assert len(samples) == 4
        assert run.search_kind == "CROSS_CITY"
        assert run.return_city_id == guangzhou.id
        assert run.return_location_name == "鱼珠地铁站服务点"
        assert run.pickup_city_ids == [wuhan.id]
        assert run.rail_costs == {str(wuhan.id): 600}
        assert run.planned_sample_count == 4
        assert run.estimated_request_count == 4


def test_cross_city_claim_carries_pickup_and_return_city_ids(engine):
    """样本若沿用任务城市，会把武汉取车错误请求成广州取车。"""
    with Session(engine, expire_on_commit=False) as session:
        guangzhou, wuhan, model = _seed(session, pickup_department_count=1)
        repository = ModelSearchRepository(session)
        run = repository.create_cross_city_run(
            vehicle_model_id=model.id,
            pickup_city_ids=[wuhan.id],
            return_city_id=guangzhou.id,
            return_location_name="鱼珠地铁站服务点",
            windows=(_windows()[0],),
            rail_costs={},
            now=NOW,
        )

        batch = repository.claim_samples(run.id, limit=1, now=NOW)

        assert batch.claims[0].zuche_city_id == "20"
        assert batch.claims[0].return_zuche_city_id == "14"


def test_cross_city_run_rejects_request_count_over_its_safety_budget(engine, monkeypatch):
    """计划网点乘租期超过安全额度时不能创建一个注定被截断的任务。"""
    monkeypatch.setattr("app.model_search.repository.CROSS_CITY_REQUEST_LIMIT", 3)
    with Session(engine) as session:
        guangzhou, wuhan, model = _seed(session)

        with pytest.raises(ValueError, match="3 次"):
            ModelSearchRepository(session).create_cross_city_run(
                vehicle_model_id=model.id,
                pickup_city_ids=[wuhan.id],
                return_city_id=guangzhou.id,
                return_location_name="鱼珠地铁站服务点",
                windows=_windows(),
                rail_costs={},
                now=NOW,
            )
