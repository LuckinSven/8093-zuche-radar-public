from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.dependencies import request_session
from app.models import PersonalState, VehicleModel
from app.personal import PersonalDataService

router = APIRouter()


class StateInput(BaseModel):
    state: PersonalState
    note: str | None = None
    rented_on: datetime | None = None


class AnnotationInput(BaseModel):
    field_name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confidence: str
    verified_at: datetime | None = None


class ManualEnergyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    energy_type: Literal["燃油", "新能源", "未知"]
    energy_subtype: Literal["汽油", "柴油", "油电混动", "纯电", "插电混动", "增程", "其他", "未知"]
    note: str = Field(min_length=1, max_length=500)


def _model(session, model_id: int):
    item = session.scalar(select(VehicleModel).where(VehicleModel.zuche_model_id == model_id))
    if item is None: raise HTTPException(404, "车型不存在")
    return item


@router.put("/models/{model_id}/personal-state")
def set_state(model_id: int, payload: StateInput, request: Request):
    with request_session(request) as session:
        model = _model(session, model_id)
        item = PersonalDataService(session).set_state(model.id, payload.state, payload.note, payload.rented_on)
        session.flush()
        return {"model_id": model_id, "state": str(item.state), "note": item.note, "rented_on": item.rented_on}


@router.post("/models/{model_id}/annotations", status_code=201)
def add_annotation(model_id: int, payload: AnnotationInput, request: Request):
    with request_session(request) as session:
        model = _model(session, model_id)
        try:
            item = PersonalDataService(session).add_annotation(model.id, **payload.model_dump())
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return _annotation(item)


@router.get("/models/{model_id}/annotations")
def annotations(model_id: int, request: Request):
    with request_session(request) as session:
        model = _model(session, model_id)
        return {"items": [_annotation(x) for x in PersonalDataService(session).list_annotations(model.id)]}


@router.put("/models/{model_id}/energy")
def confirm_model_energy(model_id: int, payload: ManualEnergyInput, request: Request):
    with request_session(request) as session:
        model = _model(session, model_id)
        try:
            PersonalDataService(session).confirm_energy(model, **payload.model_dump())
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        session.flush()
        return {
            "model_id": model.zuche_model_id,
            "energy_type": model.energy_type,
            "energy_subtype": model.energy_subtype,
            "energy_source": model.energy_source,
            "energy_confidence": model.energy_confidence,
        }


def _annotation(x):
    return {"id": x.id, "field_name": x.field_name, "value": x.value, "source": x.source,
            "confidence": x.confidence, "verified_at": x.verified_at}
