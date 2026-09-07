import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.enrichment.domain import (
    CompletionBatch,
    EnergySuggestion,
    EnrichmentResultStatus,
    EnrichmentScope,
    EnrichmentStage,
    TokenUsage,
)
from app.enrichment.service import VehicleEnrichmentService
from app.models import VehicleEnrichmentResult, VehicleEnrichmentRun, VehicleModel


def _config():
    return {
        "enabled": True,
        "api_key": "secret",
        "base_url": "https://api.example.com/v1",
        "model": "model-x",
        "batch_size": 1,
        "timeout_seconds": 30,
        "max_retries": 1,
    }


class QueueClient:
    def __init__(self, responses, calls, **configuration):
        self.responses = responses
        self.calls = calls
        self.configuration = configuration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def classify(self, prompts, focused=False):
        self.calls.append((prompts, focused, self.configuration))
        return self.responses.pop(0)


def _service(engine, responses, calls=None):
    calls = calls if calls is not None else []
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return VehicleEnrichmentService(
        factory,
        lambda **kwargs: QueueClient(responses, calls, **kwargs),
        _config,
    ), calls


def _seed(engine, *models):
    with Session(engine) as session:
        session.add_all(models)
        session.commit()
        return [item.id for item in models]


def test_run_endpoint_label_never_persists_url_credentials(engine):
    """若 Base URL 含用户信息，任务历史和 API 不能把它保存或展示出来。"""
    _seed(engine, VehicleModel(zuche_model_id=99, name="测试车型"))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    config = _config() | {
        "base_url": "https://alice:private-pass@api.example.com:8443/v1",
    }
    service = VehicleEnrichmentService(
        factory,
        lambda **_: pytest.fail("创建任务不应访问外部 AI"),
        lambda: config,
    )

    run = service.create_run(EnrichmentScope.PENDING_ONLY)

    assert run.endpoint_label == "api.example.com:8443/v1"
    assert "alice" not in run.endpoint_label
    assert "private-pass" not in run.endpoint_label


@pytest.mark.asyncio
async def test_single_model_initial_checks_apply_high_and_queue_medium_for_review(engine):
    ids = _seed(engine,
        VehicleModel(zuche_model_id=101, name="明确纯电", latest_description="纯电 60kWh"),
        VehicleModel(zuche_model_id=102, name="待复核车型"),
    )
    responses = [CompletionBatch(items=[
        EnergySuggestion(model_id=ids[0], energy_type="新能源", energy_subtype="纯电",
                         confidence="HIGH", rationale="描述明确"),
    ], usage=TokenUsage(prompt_tokens=40, completion_tokens=15, total_tokens=55)),
        CompletionBatch(items=[
        EnergySuggestion(model_id=ids[1], energy_type="未知", energy_subtype="未知",
                         confidence="MEDIUM", rationale="线索不足"),
    ], usage=TokenUsage(prompt_tokens=40, completion_tokens=15, total_tokens=55))]
    service, calls = _service(engine, responses)
    run = service.create_run(EnrichmentScope.PENDING_ONLY)

    result = await service.process_active_run()
    await service.process_active_run()

    with Session(engine) as session:
        high = session.get(VehicleModel, ids[0])
        medium = session.scalar(select(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.run_id == run.id,
            VehicleEnrichmentResult.vehicle_model_id == ids[1]))
        stored_run = session.get(VehicleEnrichmentRun, run.id)
        assert high.energy_type == "新能源"
        assert high.energy_subtype == "纯电"
        assert high.energy_source == "AI_BATCH"
        assert high.energy_confidence == "HIGH"
        assert medium.stage == EnrichmentStage.FOCUSED
        assert medium.status == EnrichmentResultStatus.PENDING
        assert stored_run.updated_count == 1
        assert stored_run.total_tokens == 110
    assert result.state == "PROCESSED"
    assert [len(prompts) for prompts, _, _ in calls] == [1, 1]
    assert calls[0][1] is False


@pytest.mark.asyncio
async def test_focused_high_applies_and_focused_low_keeps_unknown(engine):
    ids = _seed(engine,
        VehicleModel(zuche_model_id=201, name="复核成功"),
        VehicleModel(zuche_model_id=202, name="仍不确定"),
    )
    first_initial = CompletionBatch(items=[EnergySuggestion(
        model_id=ids[0], energy_type="未知", energy_subtype="未知",
        confidence="LOW", rationale="需要复核")])
    second_initial = CompletionBatch(items=[EnergySuggestion(
        model_id=ids[1], energy_type="未知", energy_subtype="未知",
        confidence="LOW", rationale="需要复核")])
    focused_high = CompletionBatch(items=[EnergySuggestion(
        model_id=ids[0], energy_type="燃油", energy_subtype="汽油",
        confidence="HIGH", rationale="复核后明确",
    )])
    focused_low = CompletionBatch(items=[EnergySuggestion(
        model_id=ids[1], energy_type="未知", energy_subtype="未知",
        confidence="LOW", rationale="仍无可靠线索",
    )])
    service, calls = _service(
        engine, [first_initial, second_initial, focused_high, focused_low])
    run = service.create_run(EnrichmentScope.PENDING_ONLY)

    await service.process_active_run()
    await service.process_active_run()
    await service.process_active_run()
    await service.process_active_run()

    with Session(engine) as session:
        applied = session.get(VehicleModel, ids[0])
        unknown_result = session.scalar(select(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.run_id == run.id,
            VehicleEnrichmentResult.vehicle_model_id == ids[1]))
        stored_run = session.get(VehicleEnrichmentRun, run.id)
        assert applied.energy_source == "AI_FOCUSED"
        assert unknown_result.status == EnrichmentResultStatus.KEPT_UNKNOWN
        assert stored_run.status.value == "COMPLETED"
        assert stored_run.processed_count == 2
        assert stored_run.unknown_count == 1
    assert [focused for _, focused, _ in calls] == [False, False, True, True]
    assert [len(prompts) for prompts, _, _ in calls] == [1, 1, 1, 1]


@pytest.mark.asyncio
async def test_all_scope_never_overwrites_manual_energy(engine):
    ids = _seed(engine, VehicleModel(
        zuche_model_id=301, name="人工车型", energy_type="燃油",
        energy_subtype="汽油", energy_source="MANUAL", energy_confidence="HIGH",
    ))
    service, calls = _service(engine, [])
    run = service.create_run(EnrichmentScope.ALL)

    result = await service.process_active_run()

    with Session(engine) as session:
        model = session.get(VehicleModel, ids[0])
        assert model.energy_source == "MANUAL"
        assert session.get(VehicleEnrichmentRun, run.id).total_count == 0
    assert result.state == "EMPTY"
    assert calls == []
