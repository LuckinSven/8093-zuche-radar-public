"""永久车型库的可信更新规则。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VehicleModel


PROTECTED_ENERGY_SOURCES = {"MANUAL", "USER", "人工", "AI_BATCH", "AI_FOCUSED"}


def upsert_permanent_model(
    session: Session,
    *,
    model_id: int,
    model_name: str | None,
    observed_at: datetime,
    description: str | None = None,
    image_url: str | None = None,
    energy_type: str | None = None,
) -> tuple[VehicleModel, bool]:
    model = session.scalar(select(VehicleModel).where(
        VehicleModel.zuche_model_id == model_id))
    created = model is None
    if model is None:
        model = VehicleModel(
            zuche_model_id=model_id,
            name=model_name or "未命名车型",
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )
        session.add(model)
        session.flush()
    else:
        if model.first_seen_at is None:
            model.first_seen_at = observed_at
        model.last_seen_at = observed_at
        if model_name:
            model.name = model_name
    if description:
        model.latest_description = description
    if image_url:
        model.image_url = image_url
    if energy_type and model.energy_source not in PROTECTED_ENERGY_SOURCES:
        model.energy_type = energy_type
        model.energy_source = "UPSTREAM"
    session.flush()
    return model, created
