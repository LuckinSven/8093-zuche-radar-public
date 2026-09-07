from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


def _seed_run(session: Session):
    city = City(zuche_city_id="14", name="广州")
    model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
    department = Department(
        zuche_dept_id=79340,
        name="鱼珠地铁站服务点",
        latitude=23.101610,
        longitude=113.432649,
    )
    session.add_all([city, model, department])
    session.flush()
    department.city_id = city.id
    run = ModelSearchRun(
        city_id=city.id,
        status=ModelSearchRunStatus.PENDING,
        phase=ModelSearchPhase.BASE,
        target_fingerprint="fingerprint",
        requested_names=["比亚迪海狮05"],
    )
    session.add(run)
    session.flush()
    return run, model, department


def test_model_search_target_rejects_duplicate_variant_for_same_run(engine):
    """同一变体重复成为目标会重复统计并浪费请求。"""
    with Session(engine) as session:
        run, model, _ = _seed_run(session)
        session.add_all([
            ModelSearchTarget(
                run_id=run.id,
                vehicle_model_id=model.id,
                requested_name="比亚迪海狮05",
                zuche_model_id=4952,
            ),
            ModelSearchTarget(
                run_id=run.id,
                vehicle_model_id=model.id,
                requested_name="比亚迪海狮05",
                zuche_model_id=4952,
            ),
        ])
        with pytest.raises(IntegrityError):
            session.commit()


def test_model_search_sample_and_offer_have_stable_uniqueness(engine):
    """相同时间请求或相同报价重复入库会让请求量和均价失真。"""
    pickup = datetime(2026, 9, 5, 1, 0, tzinfo=UTC)
    with Session(engine) as session:
        run, model, department = _seed_run(session)
        sample = ModelSearchSample(
            run_id=run.id,
            anchor_department_id=department.id,
            kind=ModelSearchSampleKind.BASE,
            status=ModelSearchSampleStatus.PENDING,
            pickup_time=pickup,
            return_time=pickup + timedelta(days=1),
        )
        session.add(sample)
        session.flush()
        session.add(ModelSearchOffer(
            run_id=run.id,
            sample_id=sample.id,
            vehicle_model_id=model.id,
            department_id=department.id,
            package_price=115,
            book_flag=True,
        ))
        session.commit()

        session.add(ModelSearchSample(
            run_id=run.id,
            anchor_department_id=department.id,
            kind=ModelSearchSampleKind.FINE,
            status=ModelSearchSampleStatus.PENDING,
            pickup_time=pickup,
            return_time=pickup + timedelta(days=1),
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def test_model_search_run_rejects_negative_counts(engine):
    """负数进度会使任务状态和前端进度条不可解释。"""
    with Session(engine) as session:
        city = City(zuche_city_id="14", name="广州")
        session.add(city)
        session.flush()
        session.add(ModelSearchRun(
            city_id=city.id,
            target_fingerprint="fingerprint",
            requested_names=["比亚迪海狮05"],
            planned_sample_count=-1,
        ))
        with pytest.raises(IntegrityError):
            session.commit()
