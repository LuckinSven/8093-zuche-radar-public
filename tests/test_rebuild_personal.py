from datetime import UTC, datetime

import pytest

from app.models import PersonalState
from app.personal import PersonalDataService
from app.repositories.scans import ScanRepository


def test_annotation_is_separate_revision_with_source_and_confidence(session):
    model = ScanRepository(session).upsert_vehicle_model(4666, "大众途铠")
    service = PersonalDataService(session)

    annotation = service.add_annotation(model.id, "energy_type", "汽油", "本人取车核对", "HIGH")
    session.commit()

    assert (annotation.field_name, annotation.value, annotation.source, annotation.confidence) == (
        "energy_type", "汽油", "本人取车核对", "HIGH")


def test_invalid_annotation_confidence_is_rejected(session):
    model = ScanRepository(session).upsert_vehicle_model(4666, "大众途铠")
    with pytest.raises(ValueError, match="可信度"):
        PersonalDataService(session).add_annotation(model.id, "energy_type", "汽油", "本人核对", "CERTAIN")


def test_personal_state_has_one_current_value(session):
    model = ScanRepository(session).upsert_vehicle_model(4666, "大众途铠")
    service = PersonalDataService(session)
    service.set_state(model.id, PersonalState.WANT_TO_RENT, "先试试")
    current = service.set_state(model.id, PersonalState.LIKED, "喜欢")
    session.commit()
    assert (current.state, current.note) == (PersonalState.LIKED, "喜欢")


def test_manual_energy_confirmation_updates_model_and_keeps_audit_record(session):
    """如果人工确认只写备注而没有更新车型库，该测试应失败。"""
    model = ScanRepository(session).upsert_vehicle_model(4952, "比亚迪海狮05")
    verified_at = datetime(2026, 9, 2, 5, 0, tzinfo=UTC)

    annotation = PersonalDataService(session).confirm_energy(
        model,
        energy_type="新能源",
        energy_subtype="插电混动",
        note="品牌官网已核对",
        verified_at=verified_at,
    )
    session.commit()

    assert (model.energy_type, model.energy_subtype) == ("新能源", "插电混动")
    assert (model.energy_source, model.energy_confidence) == ("MANUAL", "HIGH")
    assert model.energy_updated_at == verified_at
    assert (annotation.field_name, annotation.value) == ("energy", "新能源 · 插电混动")
    assert (annotation.source, annotation.confidence) == ("品牌官网已核对", "HIGH")


def test_manual_energy_confirmation_rejects_incompatible_subtype(session):
    """如果燃油大类可以搭配纯电细分，该测试应失败。"""
    model = ScanRepository(session).upsert_vehicle_model(4952, "比亚迪海狮05")

    with pytest.raises(ValueError, match="不一致"):
        PersonalDataService(session).confirm_energy(
            model, energy_type="燃油", energy_subtype="纯电", note="人工确认")
