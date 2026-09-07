"""车型能源补全的两阶段后台编排。"""

from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session

from app.enrichment.client import EnrichmentClientError, OpenAICompatibleClient
from app.enrichment.domain import EnrichmentProcessResult, EnrichmentScope
from app.enrichment.repository import EnrichmentRepository
from app.models import VehicleEnrichmentRun


class VehicleEnrichmentService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        client_factory: Callable[..., OpenAICompatibleClient],
        config_loader: Callable[[], dict],
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.config_loader = config_loader

    def create_run(self, scope: EnrichmentScope) -> VehicleEnrichmentRun:
        config = self._configuration()
        endpoint = urlsplit(config["base_url"])
        host = endpoint.hostname or "未知主机"
        port = f":{endpoint.port}" if endpoint.port is not None else ""
        endpoint_label = f"{host}{port}{endpoint.path}".rstrip("/")
        with self.session_factory() as session:
            run = EnrichmentRepository(session).create_run(
                scope, config["model"], endpoint_label)
            session.commit()
            return run

    async def process_active_run(self) -> EnrichmentProcessResult:
        with self.session_factory() as session:
            repository = EnrichmentRepository(session)
            run_id = repository.active_run_id()
            if run_id is None:
                return EnrichmentProcessResult("IDLE")
        config = self._configuration()
        with self.session_factory() as session:
            repository = EnrichmentRepository(session)
            claimed = repository.claim_work(run_id, 1)
            session.commit()
        if claimed is None:
            return EnrichmentProcessResult("EMPTY")

        try:
            async with self.client_factory(
                api_key=config["api_key"],
                base_url=config["base_url"],
                model=config["model"],
                timeout_seconds=config["timeout_seconds"],
                max_retries=config["max_retries"],
            ) as client:
                completion = await client.classify(
                    list(claimed.prompts),
                    focused=claimed.stage.value == "FOCUSED",
                )
        except EnrichmentClientError as error:
            with self.session_factory() as session:
                EnrichmentRepository(session).fail_claim(claimed, error.public_message)
                session.commit()
            return EnrichmentProcessResult("FAILED", processed=len(claimed.result_ids))

        with self.session_factory() as session:
            EnrichmentRepository(session).save_suggestions(claimed, completion)
            session.commit()
        return EnrichmentProcessResult("PROCESSED", processed=len(claimed.result_ids))

    def stop(self, run_id: UUID) -> VehicleEnrichmentRun:
        return self._transition("stop", run_id)

    def resume(self, run_id: UUID) -> VehicleEnrichmentRun:
        return self._transition("resume", run_id)

    def retry_failed(self, run_id: UUID) -> VehicleEnrichmentRun:
        return self._transition("retry_failed", run_id)

    def delete_run(self, run_id: UUID) -> None:
        with self.session_factory() as session:
            EnrichmentRepository(session).delete_run(run_id)
            session.commit()

    def resolve_manually(
        self,
        run_id: UUID,
        model_id: int,
        *,
        energy_type: str,
        energy_subtype: str,
        note: str,
    ) -> VehicleEnrichmentRun:
        with self.session_factory() as session:
            run = EnrichmentRepository(session).resolve_manually(
                run_id,
                model_id,
                energy_type=energy_type,
                energy_subtype=energy_subtype,
                note=note,
            )
            session.commit()
            return run

    def interrupt_stale_runs(self) -> int:
        with self.session_factory() as session:
            count = EnrichmentRepository(session).interrupt_stale_runs()
            session.commit()
            return count

    def _transition(self, action: str, run_id: UUID) -> VehicleEnrichmentRun:
        with self.session_factory() as session:
            run = getattr(EnrichmentRepository(session), action)(run_id)
            session.commit()
            return run

    def _configuration(self) -> dict:
        config = self.config_loader()
        if not config.get("enabled") or not config.get("api_key"):
            raise ValueError("AI 补全接口尚未启用或未配置 API Key")
        required = ("base_url", "model", "batch_size", "timeout_seconds", "max_retries")
        if any(config.get(key) in (None, "") for key in required):
            raise ValueError("AI 补全接口配置不完整")
        return config
