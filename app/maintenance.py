from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    CitywideOffer,
    CitywideRawPayload,
    ModelSearchOffer,
    ModelSearchRun,
    ModelSearchSample,
    ModelSearchTarget,
)
from app.repositories.scans import ScanRepository


class RawPayloadMaintenance:
    def __init__(self, session: Session, retention_days: int = 60) -> None:
        self.repository = ScanRepository(session)
        self.retention_days = retention_days

    def purge(self, now: datetime) -> int:
        return self.repository.purge_expired_raw_payloads(now - timedelta(days=self.retention_days))


class CitywideDetailMaintenance:
    """分批清理可再生成的详细数据，永不触碰永久车型和精简汇总。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def purge(self, cutoff: datetime, batch_size: int = 1000) -> dict[str, int]:
        if batch_size <= 0:
            raise ValueError("清理批次必须大于 0")
        return {
            "offers": self._purge_model(
                CitywideOffer, CitywideOffer.last_observed_at, cutoff, batch_size),
            "raw_payloads": self._purge_model(
                CitywideRawPayload, CitywideRawPayload.captured_at, cutoff, batch_size),
        }

    def _purge_model(self, model, timestamp, cutoff: datetime, batch_size: int) -> int:
        deleted = 0
        while True:
            ids = list(self.session.scalars(select(model.id).where(
                timestamp < cutoff,
            ).order_by(model.id).limit(batch_size)))
            if not ids:
                return deleted
            result = self.session.execute(delete(model).where(model.id.in_(ids)))
            deleted += int(result.rowcount or 0)
            self.session.commit()


class ModelSearchMaintenance:
    """按外键顺序分批清理超过保留期的找车任务。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def purge(self, cutoff: datetime, batch_size: int = 1000) -> dict[str, int]:
        if batch_size <= 0:
            raise ValueError("清理批次必须大于 0")
        result = {"offers": 0, "samples": 0, "targets": 0, "runs": 0}
        while True:
            run_ids = list(self.session.scalars(select(ModelSearchRun.id).where(
                ModelSearchRun.completed_at.is_not(None),
                ModelSearchRun.completed_at < cutoff,
            ).order_by(ModelSearchRun.id).limit(batch_size)))
            if not run_ids:
                return result
            for key, model in (
                ("offers", ModelSearchOffer),
                ("samples", ModelSearchSample),
                ("targets", ModelSearchTarget),
                ("runs", ModelSearchRun),
            ):
                deleted = self.session.execute(delete(model).where(
                    model.run_id.in_(run_ids) if model is not ModelSearchRun
                    else model.id.in_(run_ids)
                ))
                result[key] += int(deleted.rowcount or 0)
            self.session.commit()
