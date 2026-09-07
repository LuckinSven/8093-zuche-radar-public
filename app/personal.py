from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.enrichment.domain import EnergySubtype, validate_energy_pair
from app.models import PersonalState, PersonalVehicleState, VehicleAnnotation, VehicleModel
from app.repositories.personal import PersonalRepository


class PersonalDataService:
    """个人状态与人工补充；人工结论与神州原始快照分开保存。"""

    def __init__(self, session: Session) -> None:
        self.repository = PersonalRepository(session)

    def set_state(self, model_pk: int, state: PersonalState, note: str | None = None,
                  rented_on: datetime | None = None) -> PersonalVehicleState:
        item = self.repository.get_state(model_pk)
        if item is None:
            item = PersonalVehicleState(vehicle_model_id=model_pk, state=state, note=note, rented_on=rented_on)
            self.repository.add_state(item)
        else:
            item.state, item.note, item.rented_on = state, note, rented_on
        return item

    def add_annotation(self, model_pk: int, field_name: str, value: str, source: str, confidence: str,
                       verified_at: datetime | None = None) -> VehicleAnnotation:
        if confidence not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError("可信度仅支持 LOW、MEDIUM、HIGH")
        values = (field_name.strip(), value.strip(), source.strip())
        if not all(values):
            raise ValueError("字段、内容和来源均不能为空")
        item = VehicleAnnotation(vehicle_model_id=model_pk, field_name=values[0], value=values[1], source=values[2],
                                 confidence=confidence, verified_at=verified_at)
        self.repository.add_annotation(item)
        return item

    def list_annotations(self, model_pk: int) -> list[VehicleAnnotation]:
        return self.repository.list_annotations(model_pk)

    def confirm_energy(
        self,
        model: VehicleModel,
        *,
        energy_type: str,
        energy_subtype: str,
        note: str,
        verified_at: datetime | None = None,
    ) -> VehicleAnnotation:
        subtype = EnergySubtype(energy_subtype)
        validate_energy_pair(energy_type, subtype)
        source_note = note.strip()
        if not source_note:
            raise ValueError("人工确认依据不能为空")
        confirmed_at = verified_at or datetime.now(UTC)
        model.energy_type = energy_type
        model.energy_subtype = subtype.value
        model.energy_source = "MANUAL"
        model.energy_confidence = "HIGH"
        model.energy_updated_at = confirmed_at
        return self.add_annotation(
            model.id,
            "energy",
            f"{energy_type} · {subtype.value}",
            source_note,
            "HIGH",
            confirmed_at,
        )
