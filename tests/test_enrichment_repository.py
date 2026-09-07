import pytest
from sqlalchemy import select

from app.enrichment.domain import (
    EnrichmentResultStatus,
    EnrichmentScope,
    EnrichmentStage,
)
from app.enrichment.repository import EnrichmentRepository, InvalidEnrichmentTransition
from app.models import VehicleEnrichmentResult, VehicleModel


def _model(session, zuche_id, name, **fields):
    item = VehicleModel(zuche_model_id=zuche_id, name=name, **fields)
    session.add(item)
    session.flush()
    return item


def test_pending_scope_selects_unknown_low_confidence_or_missing_subtype(session):
    """默认任务若漏选不完整车型或重扫高置信完整车型，都会浪费额度。"""
    unknown = _model(session, 1, "未知车型")
    low = _model(session, 2, "低置信车型", energy_type="燃油",
                 energy_subtype="汽油", energy_confidence="LOW")
    missing = _model(session, 3, "缺细分车型", energy_type="新能源",
                     energy_confidence="HIGH")
    _model(session, 4, "完整车型", energy_type="新能源", energy_subtype="纯电",
           energy_confidence="HIGH")
    _model(session, 5, "人工车型", energy_type="燃油", energy_source="MANUAL")

    run = EnrichmentRepository(session).create_run(
        EnrichmentScope.PENDING_ONLY, "model-x", "api.example")
    selected = session.scalars(select(VehicleEnrichmentResult).where(
        VehicleEnrichmentResult.run_id == run.id)).all()

    assert {item.vehicle_model_id for item in selected} == {
        unknown.id, low.id, missing.id,
    }
    assert run.total_count == 3


def test_create_run_returns_existing_active_run(session):
    """重复点击不应创建并发任务或重复消耗 Token。"""
    _model(session, 1, "未知车型")
    repository = EnrichmentRepository(session)
    first = repository.create_run(EnrichmentScope.PENDING_ONLY, "model-x", "api.example")
    second = repository.create_run(EnrichmentScope.ALL, "other-model", "other.example")

    assert second.id == first.id
    assert second.scope == EnrichmentScope.PENDING_ONLY


def test_claim_work_ignores_legacy_batch_size_and_claims_one_model(session):
    """旧配置即使仍是批量值，也不能再次整批领取和连带失败。"""
    models = [_model(session, number, f"车型{number}") for number in range(1, 3)]
    repository = EnrichmentRepository(session)
    run = repository.create_run(EnrichmentScope.PENDING_ONLY, "model-x", "api.example")

    first = repository.claim_work(run.id, batch_size=30)

    assert len(first.prompts) == 1
    assert first.stage == EnrichmentStage.BATCH
    assert first.prompts[0].model_id == models[0].id


def test_interrupt_stale_run_requeues_claim_without_auto_resume(session):
    """容器重启后不能静默续跑或永久卡住处理中车型。"""
    _model(session, 1, "未知车型")
    repository = EnrichmentRepository(session)
    run = repository.create_run(EnrichmentScope.PENDING_ONLY, "model-x", "api.example")
    claim = repository.claim_work(run.id, batch_size=15)

    changed = repository.interrupt_stale_runs()
    result = session.get(VehicleEnrichmentResult, claim.result_ids[0])

    assert changed == 1
    assert run.status.value == "INTERRUPTED"
    assert result.status == EnrichmentResultStatus.PENDING
    assert result.claim_token is None


def test_delete_stopped_run_keeps_vehicle_model_and_removes_task_details(session):
    """删除历史任务不得连带删除已积累的车型库。"""
    model = _model(session, 31, "保留车型", energy_type="新能源")
    repository = EnrichmentRepository(session)
    run = repository.create_run(EnrichmentScope.PENDING_ONLY, "model-x", "api.example")
    run.status = "STOPPED"
    session.flush()

    repository.delete_run(run.id)
    session.flush()

    assert repository.get_run(run.id) is None
    assert session.get(VehicleModel, model.id).name == "保留车型"
    assert session.scalars(select(VehicleEnrichmentResult).where(
        VehicleEnrichmentResult.run_id == run.id)).all() == []


def test_delete_run_rejects_active_or_in_flight_work(session):
    """删除仍在执行的任务会让后台回写落到不存在的任务上。"""
    _model(session, 32, "处理中车型")
    repository = EnrichmentRepository(session)
    run = repository.create_run(EnrichmentScope.PENDING_ONLY, "model-x", "api.example")

    with pytest.raises(InvalidEnrichmentTransition, match="运行中"):
        repository.delete_run(run.id)

    repository.claim_work(run.id, batch_size=1)
    run.status = "STOPPED"
    session.flush()
    with pytest.raises(InvalidEnrichmentTransition, match="正在收尾"):
        repository.delete_run(run.id)
