from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.model_search.repository import (
    InvalidModelSearchTransition,
    ModelSearchRepository,
)
from app.models import (
    City,
    CitywideOffer,
    CitywideScanPoint,
    CitywideScanRun,
    Department,
    ModelSearchOffer,
    ModelSearchPhase,
    ModelSearchRun,
    ModelSearchRunStatus,
    ModelSearchSample,
    ModelSearchSampleKind,
    ModelSearchSampleStatus,
    VehicleModel,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=SHANGHAI)


def _seed(session: Session, department_count=3):
    city = City(zuche_city_id="14", name="广州")
    models = [
        VehicleModel(zuche_model_id=4952, name="比亚迪海狮05"),
        VehicleModel(zuche_model_id=4953, name="比亚迪海狮05"),
        VehicleModel(zuche_model_id=5187, name="丰田bZ5"),
    ]
    session.add_all([city, *models])
    session.flush()
    departments = [
        Department(
            city_id=city.id,
            zuche_dept_id=79_340 + index,
            name=f"广州网点{index}",
            latitude=23.101610 + index / 10_000,
            longitude=113.432649 + index / 10_000,
        )
        for index in range(department_count)
    ]
    session.add_all(departments)
    session.commit()
    return city, models, departments


def test_create_run_snapshots_all_same_name_variants_and_base_samples(engine):
    """漏掉同名变体或租期会让按车型找车结果天然不完整。"""
    with Session(engine, expire_on_commit=False) as session:
        city, models, _ = _seed(session)
        repository = ModelSearchRepository(session)

        run = repository.create_run(
            city_id=city.id,
            model_names=[" 比亚迪海狮05 ", "丰田bZ5", "比亚迪海狮05"],
            now=NOW,
        )
        session.commit()

        targets = session.scalars(select(repository.target_model).where(
            repository.target_model.run_id == run.id)).all()
        samples = session.scalars(select(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id)).all()
        assert run.requested_names == ["丰田bZ5", "比亚迪海狮05"]
        assert {item.zuche_model_id for item in targets} == {4952, 4953, 5187}
        assert run.planned_sample_count == 8 * 3
        assert len(samples) == 24
        assert {item.kind for item in samples} == {ModelSearchSampleKind.BASE}
        assert {item.pickup_time.astimezone(SHANGHAI).hour for item in samples} == {9}


def test_create_run_rejects_unknown_or_concurrent_targets(engine):
    """未知名称或同城重复活动任务不能悄悄创建空扫描。"""
    with Session(engine, expire_on_commit=False) as session:
        city, _, _ = _seed(session)
        repository = ModelSearchRepository(session)
        with pytest.raises(ValueError, match="车型库"):
            repository.create_run(city_id=city.id, model_names=["不存在车型"], now=NOW)
        repository.create_run(city_id=city.id, model_names=["丰田bZ5"], now=NOW)
        session.commit()
        with pytest.raises(InvalidModelSearchTransition, match="活动"):
            repository.create_run(city_id=city.id, model_names=["丰田bZ5"], now=NOW)


def test_base_completion_seeds_fine_samples_from_history_and_current_results(engine):
    """候选网点若不合并历史和本轮结果，会漏掉目标车型曾经出现的地点。"""
    with Session(engine, expire_on_commit=False) as session:
        city, models, departments = _seed(session, department_count=3)
        old_run = CitywideScanRun(
            city_id=city.id,
            pickup_time=NOW,
            return_time=NOW + timedelta(days=1),
        )
        session.add(old_run)
        session.flush()
        point = CitywideScanPoint(run_id=old_run.id, department_id=departments[0].id)
        session.add(point)
        session.flush()
        session.add(CitywideOffer(
            run_id=old_run.id,
            vehicle_model_id=models[0].id,
            department_id=departments[0].id,
            source_point_id=point.id,
            book_flag=True,
        ))
        repository = ModelSearchRepository(session)
        run = repository.create_run(
            city_id=city.id, model_names=["比亚迪海狮05"], now=NOW)
        current_sample = session.scalar(select(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id,
            ModelSearchSample.anchor_department_id == departments[1].id,
        ))
        session.add(ModelSearchOffer(
            run_id=run.id,
            sample_id=current_sample.id,
            vehicle_model_id=models[1].id,
            department_id=departments[1].id,
            book_flag=True,
        ))
        session.execute(
            ModelSearchSample.__table__.update().where(
                ModelSearchSample.run_id == run.id,
            ).values(status=ModelSearchSampleStatus.COMPLETED, completed_at=NOW))
        run.completed_sample_count = run.planned_sample_count

        repository.advance_if_exhausted(run.id, now=NOW)
        session.commit()

        session.refresh(run)
        fine_count = session.scalar(select(func.count()).select_from(
            ModelSearchSample).where(
                ModelSearchSample.run_id == run.id,
                ModelSearchSample.kind == ModelSearchSampleKind.FINE,
            ))
        assert run.phase == ModelSearchPhase.FINE
        assert run.candidate_department_count == 2
        assert fine_count == 2 * 96
        assert run.planned_sample_count == 24 + 192


def test_recent_successful_sample_is_reused_without_upstream_request(engine):
    """两小时内完全相同的成功样本应命中缓存并保留目标报价。"""
    with Session(engine, expire_on_commit=False) as session:
        city, models, departments = _seed(session, department_count=1)
        repository = ModelSearchRepository(session)
        source_run = repository.create_run(
            city_id=city.id, model_names=["丰田bZ5"], now=NOW)
        source_run.status = ModelSearchRunStatus.COMPLETED
        source_run.completed_at = NOW
        source_sample = session.scalar(select(ModelSearchSample).where(
            ModelSearchSample.run_id == source_run.id))
        source_sample.status = ModelSearchSampleStatus.COMPLETED
        source_sample.completed_at = NOW
        session.add(ModelSearchOffer(
            run_id=source_run.id,
            sample_id=source_sample.id,
            vehicle_model_id=models[2].id,
            department_id=departments[0].id,
            package_price=145,
            book_flag=True,
        ))
        session.commit()

        target_run = repository.create_run(
            city_id=city.id,
            model_names=["丰田bZ5"],
            now=NOW + timedelta(minutes=30),
        )
        batch = repository.claim_samples(
            target_run.id, limit=1, now=NOW + timedelta(minutes=30))
        session.commit()

        cached = session.scalar(select(ModelSearchSample).where(
            ModelSearchSample.run_id == target_run.id,
            ModelSearchSample.pickup_time == source_sample.pickup_time,
            ModelSearchSample.return_time == source_sample.return_time,
        ))
        assert batch.state == "CACHE_ONLY"
        assert cached.status == ModelSearchSampleStatus.COMPLETED
        assert cached.cache_source_sample_id == source_sample.id
        assert target_run.cache_hit_count == 1
        assert target_run.request_count == 0
        assert session.scalar(select(func.count()).select_from(ModelSearchOffer).where(
            ModelSearchOffer.run_id == target_run.id)) == 1


def test_more_than_five_thousand_samples_stops_before_fine_phase(engine):
    """最终预算超限时不能静默只扫一部分候选网点。"""
    with Session(engine, expire_on_commit=False) as session:
        city, models, departments = _seed(session, department_count=53)
        repository = ModelSearchRepository(session)
        run = repository.create_run(
            city_id=city.id, model_names=["丰田bZ5"], now=NOW)
        for department in departments:
            sample = session.scalar(select(ModelSearchSample).where(
                ModelSearchSample.run_id == run.id,
                ModelSearchSample.anchor_department_id == department.id,
            ))
            session.add(ModelSearchOffer(
                run_id=run.id,
                sample_id=sample.id,
                vehicle_model_id=models[2].id,
                department_id=department.id,
                book_flag=True,
            ))
        session.execute(ModelSearchSample.__table__.update().where(
            ModelSearchSample.run_id == run.id).values(
                status=ModelSearchSampleStatus.COMPLETED, completed_at=NOW))
        run.completed_sample_count = run.planned_sample_count

        repository.advance_if_exhausted(run.id, now=NOW)
        session.commit()

        assert run.status == ModelSearchRunStatus.BUDGET_EXCEEDED
        assert run.candidate_department_count == 53
        assert session.scalar(select(func.count()).select_from(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id,
            ModelSearchSample.kind == ModelSearchSampleKind.FINE,
        )) == 0
