from datetime import datetime

from sqlalchemy.orm import Session

from app.models import PersonalState, PersonalVehicleState, VehicleAnnotation


class PersonalVehicleService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def set_state(self, vehicle_model_id: int, state: PersonalState, note: str | None = None,
                  rented_on: datetime | None = None) -> PersonalVehicleState:
        record = self.session.get(PersonalVehicleState, vehicle_model_id)
        if record is None:
            record = PersonalVehicleState(vehicle_model_id=vehicle_model_id, state=state, note=note, rented_on=rented_on)
            self.session.add(record)
        else:
            record.state, record.note, record.rented_on = state, note, rented_on
        return record


class AnnotationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, vehicle_model_id: int, field_name: str, value: str, source: str, confidence: str,
            verified_at: datetime | None = None) -> VehicleAnnotation:
        if confidence not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError("可信度仅支持 LOW、MEDIUM、HIGH")
        if not all((field_name.strip(), value.strip(), source.strip())):
            raise ValueError("字段、内容和来源均不能为空")
        item = VehicleAnnotation(vehicle_model_id=vehicle_model_id, field_name=field_name, value=value,
                                 source=source, confidence=confidence, verified_at=verified_at)
        self.session.add(item)
        return item
