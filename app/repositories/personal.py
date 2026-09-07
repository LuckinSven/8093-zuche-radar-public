from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PersonalVehicleState, VehicleAnnotation


class PersonalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_state(self, model_pk: int) -> PersonalVehicleState | None:
        return self.session.get(PersonalVehicleState, model_pk)

    def add_state(self, item: PersonalVehicleState) -> None:
        self.session.add(item)

    def add_annotation(self, item: VehicleAnnotation) -> None:
        self.session.add(item)
        self.session.flush()

    def list_annotations(self, model_pk: int) -> list[VehicleAnnotation]:
        return list(self.session.scalars(select(VehicleAnnotation).where(
            VehicleAnnotation.vehicle_model_id == model_pk).order_by(VehicleAnnotation.id.desc())))
