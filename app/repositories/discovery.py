import json
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models import AvailabilitySnapshot, ChangeEvent, Department, ModelGroupMembership, PersonalVehicleState, ScanRun, ScanStatus, VehicleModel


class DiscoveryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def snapshot_rows(self, scan_id: UUID):
        return self.session.execute(
            select(AvailabilitySnapshot, VehicleModel, Department)
            .join(VehicleModel, VehicleModel.id == AvailabilitySnapshot.vehicle_model_id)
            .join(Department, Department.id == AvailabilitySnapshot.department_id)
            .where(AvailabilitySnapshot.scan_run_id == scan_id)
        ).all()

    def group_rows(self, scan_id: UUID):
        return self.session.execute(
            select(ModelGroupMembership.vehicle_model_id, ModelGroupMembership.group_id, ModelGroupMembership.group_name)
            .where(ModelGroupMembership.scan_run_id == scan_id)
        ).all()

    def event_rows(self, scan_id: UUID):
        return self.session.execute(
            select(ChangeEvent.vehicle_model_id, ChangeEvent.event_type).where(ChangeEvent.scan_run_id == scan_id)
        ).all()

    def personal_states(self):
        return {item.vehicle_model_id: str(item.state) for item in self.session.scalars(select(PersonalVehicleState))}

    def personal_state(self, vehicle_model_id: int):
        return self.session.get(PersonalVehicleState, vehicle_model_id)

    def model_by_external_id(self, model_id: int):
        return self.session.scalar(select(VehicleModel).where(VehicleModel.zuche_model_id == model_id))

    def latest_scan_id_for_model(self, vehicle_model_id: int):
        return self.session.scalar(
            select(ScanRun.id)
            .join(AvailabilitySnapshot, AvailabilitySnapshot.scan_run_id == ScanRun.id)
            .where(AvailabilitySnapshot.vehicle_model_id == vehicle_model_id, ScanRun.status == ScanStatus.SUCCESS)
            .order_by(ScanRun.completed_at.desc())
            .limit(1)
        )

    def model_history_rows(self, vehicle_model_id: int):
        return self.session.execute(
            select(AvailabilitySnapshot, Department, ScanRun)
            .join(Department, Department.id == AvailabilitySnapshot.department_id)
            .join(ScanRun, ScanRun.id == AvailabilitySnapshot.scan_run_id)
            .where(AvailabilitySnapshot.vehicle_model_id == vehicle_model_id, ScanRun.status == ScanStatus.SUCCESS)
            .order_by(ScanRun.started_at.desc(), AvailabilitySnapshot.package_price.asc().nullslast())
        ).all()

    def disappeared_rows(self, scan_id: UUID):
        pairs = list(self.disappeared_predecessors(scan_id).items())
        if not pairs:
            return []
        snapshot_pairs = [(previous_scan_id, model_id) for model_id, previous_scan_id in pairs]
        return self.session.execute(
            select(AvailabilitySnapshot, VehicleModel, Department)
            .join(VehicleModel, VehicleModel.id == AvailabilitySnapshot.vehicle_model_id)
            .join(Department, Department.id == AvailabilitySnapshot.department_id)
            .where(tuple_(AvailabilitySnapshot.scan_run_id,
                          AvailabilitySnapshot.vehicle_model_id).in_(snapshot_pairs))
        ).all()

    def disappeared_group_rows(self, scan_id: UUID):
        pairs = list(self.disappeared_predecessors(scan_id).items())
        if not pairs:
            return []
        membership_pairs = [(previous_scan_id, model_id) for model_id, previous_scan_id in pairs]
        return self.session.execute(select(
            ModelGroupMembership.vehicle_model_id,
            ModelGroupMembership.group_id,
            ModelGroupMembership.group_name,
        ).where(tuple_(ModelGroupMembership.scan_run_id,
                       ModelGroupMembership.vehicle_model_id).in_(membership_pairs))).all()

    def disappeared_predecessors(self, scan_id: UUID) -> dict[int, UUID]:
        result = {}
        for event in self.session.scalars(select(ChangeEvent).where(
                ChangeEvent.scan_run_id == scan_id,
                ChangeEvent.event_type == "DISAPPEARED")):
            previous_scan_id = self._previous_scan_id(event)
            if previous_scan_id is not None:
                result[event.vehicle_model_id] = previous_scan_id
        return result

    @staticmethod
    def _previous_scan_id(event: ChangeEvent) -> UUID | None:
        try:
            value = json.loads(event.detail or "{}").get("previous_scan_id")
            return UUID(value) if value else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
