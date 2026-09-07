import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.enrichment.domain import (
    EnergyConfidence,
    EnergySubtype,
    EnergySuggestion,
    EnrichmentScope,
    validate_energy_pair,
)
from app.models import VehicleEnrichmentResult, VehicleEnrichmentRun, VehicleModel


def test_energy_pair_rejects_cross_category_subtype():
    """若新能源可搭配汽油细分，AI 错误结论会污染车型库。"""
    with pytest.raises(ValueError, match="能源大类与细分类型不一致"):
        validate_energy_pair("新能源", EnergySubtype.GASOLINE)


@pytest.mark.parametrize(
    ("energy_type", "subtype"),
    [
        ("燃油", EnergySubtype.GASOLINE),
        ("燃油", EnergySubtype.HYBRID),
        ("新能源", EnergySubtype.PURE_ELECTRIC),
        ("新能源", EnergySubtype.PLUG_IN_HYBRID),
        ("新能源", EnergySubtype.RANGE_EXTENDER),
        ("未知", EnergySubtype.UNKNOWN),
    ],
)
def test_energy_pair_accepts_supported_combinations(energy_type, subtype):
    validate_energy_pair(energy_type, subtype)


def test_energy_suggestion_validates_pair_during_parsing():
    """若结构化建议跳过配对校验，非法响应会进入后续写库流程。"""
    with pytest.raises(ValidationError, match="能源大类与细分类型不一致"):
        EnergySuggestion(
            model_id=4952,
            energy_type="燃油",
            energy_subtype="纯电",
            confidence=EnergyConfidence.HIGH,
            rationale="错误示例",
        )


def test_enrichment_models_keep_one_result_per_run_and_vehicle(session):
    """同一任务重复创建车型结果会导致统计和应用记录翻倍。"""
    model = VehicleModel(zuche_model_id=900001, name="测试车型")
    run = VehicleEnrichmentRun(scope=EnrichmentScope.PENDING_ONLY)
    session.add_all([model, run])
    session.flush()
    session.add_all([
        VehicleEnrichmentResult(run_id=run.id, vehicle_model_id=model.id),
        VehicleEnrichmentResult(run_id=run.id, vehicle_model_id=model.id),
    ])

    with pytest.raises(IntegrityError):
        session.flush()


def test_enrichment_run_rejects_negative_statistics(session):
    """负数统计会让任务进度失真。"""
    session.add(VehicleEnrichmentRun(
        scope=EnrichmentScope.PENDING_ONLY,
        total_count=-1,
    ))

    with pytest.raises(IntegrityError):
        session.flush()
