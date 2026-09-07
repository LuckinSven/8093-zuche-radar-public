from app.models import PersonalState
from app.repositories import ScanRepository
from app.services import PersonalVehicleService


def test_setting_want_to_rent_keeps_current_personal_state(session):
    model = ScanRepository(session).upsert_vehicle_model(4666, "大众途铠")
    session.commit()

    state = PersonalVehicleService(session).set_state(model.id, PersonalState.WANT_TO_RENT, "下次试试")
    session.commit()

    assert state.state == PersonalState.WANT_TO_RENT
    assert state.note == "下次试试"
