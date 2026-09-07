"""车型补全任务的短事务、领取令牌和状态聚合。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.enrichment.domain import (
    ClaimedWork,
    CompletionBatch,
    EnergySubtype,
    EnrichmentResultStatus,
    EnrichmentRunStatus,
    EnrichmentScope,
    EnrichmentStage,
    ModelPrompt,
)
from app.models import VehicleEnrichmentResult, VehicleEnrichmentRun, VehicleModel
from app.personal import PersonalDataService


MANUAL_SOURCES = {"MANUAL", "USER", "人工"}
ACTIVE_STATUSES = (EnrichmentRunStatus.PENDING, EnrichmentRunStatus.RUNNING)
TERMINAL_RESULT_STATUSES = (
    EnrichmentResultStatus.APPLIED,
    EnrichmentResultStatus.KEPT_UNKNOWN,
    EnrichmentResultStatus.FAILED,
)


class InvalidEnrichmentTransition(ValueError):
    """补全任务状态变更不符合既定状态机。"""


class EnrichmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(
        self,
        scope: EnrichmentScope,
        model_name: str,
        endpoint_label: str,
    ) -> VehicleEnrichmentRun:
        existing = self.session.scalar(select(VehicleEnrichmentRun).where(
            VehicleEnrichmentRun.status.in_(ACTIVE_STATUSES),
        ).order_by(VehicleEnrichmentRun.created_at).limit(1))
        if existing is not None:
            return existing

        candidates = select(VehicleModel).where(
            or_(VehicleModel.energy_source.is_(None),
                VehicleModel.energy_source.not_in(MANUAL_SOURCES)))
        if scope == EnrichmentScope.PENDING_ONLY:
            candidates = candidates.where(or_(
                VehicleModel.energy_type.is_(None),
                VehicleModel.energy_type.not_in(("燃油", "新能源")),
                VehicleModel.energy_subtype.is_(None),
                VehicleModel.energy_subtype == "未知",
                VehicleModel.energy_confidence == "LOW",
            ))
        models = list(self.session.scalars(candidates.order_by(VehicleModel.id)))
        run = VehicleEnrichmentRun(
            scope=scope,
            model_name=model_name,
            endpoint_label=endpoint_label,
            total_count=len(models),
        )
        self.session.add(run)
        self.session.flush()
        self.session.add_all([
            VehicleEnrichmentResult(run_id=run.id, vehicle_model_id=model.id)
            for model in models
        ])
        self.session.flush()
        return run

    def get_run(self, run_id: UUID) -> VehicleEnrichmentRun | None:
        return self.session.get(VehicleEnrichmentRun, run_id)

    def active_run_id(self) -> UUID | None:
        return self.session.scalar(select(VehicleEnrichmentRun.id).where(
            VehicleEnrichmentRun.status.in_(ACTIVE_STATUSES),
        ).order_by(VehicleEnrichmentRun.created_at).limit(1))

    def claim_work(self, run_id: UUID, batch_size: int) -> ClaimedWork | None:
        run = self.session.scalar(select(VehicleEnrichmentRun).where(
            VehicleEnrichmentRun.id == run_id).with_for_update())
        if run is None:
            return None
        if run.status == EnrichmentRunStatus.PENDING:
            run.status = EnrichmentRunStatus.RUNNING
            run.started_at = datetime.now(UTC)
        elif run.status != EnrichmentRunStatus.RUNNING:
            return None

        stage = self._next_stage(run.id)
        if stage is None:
            self._refresh_run(run)
            return None
        # 部分 OpenAI 兼容网关无法稳定返回多车型结构化结果。即使数据库里
        # 还保存着旧批量值，也只领取一款，避免一次格式异常连带标记整批失败。
        limit = 1
        statement = select(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.run_id == run.id,
            VehicleEnrichmentResult.stage == stage,
            VehicleEnrichmentResult.status == EnrichmentResultStatus.PENDING,
        ).order_by(VehicleEnrichmentResult.id).limit(limit)
        if self.session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        results = list(self.session.scalars(statement))
        if not results:
            self._refresh_run(run)
            return None

        claim_token = uuid4()
        prompts = []
        for result in results:
            model = self.session.get(VehicleModel, result.vehicle_model_id)
            if model is None:
                result.status = EnrichmentResultStatus.FAILED
                result.error_summary = "关联车型不存在"
                continue
            result.status = EnrichmentResultStatus.RUNNING
            result.claim_token = claim_token
            result.attempt_count += 1
            prompts.append(ModelPrompt(
                model_id=model.id,
                name=model.name,
                description=model.latest_description,
                current_energy_type=model.energy_type,
                current_energy_subtype=model.energy_subtype,
                current_energy_source=model.energy_source,
            ))
        run.current_stage = stage
        self.session.flush()
        claimed_ids = tuple(result.id for result in results
                            if result.claim_token == claim_token)
        if not claimed_ids:
            self._refresh_run(run)
            return None
        return ClaimedWork(
            run_id=run.id,
            stage=stage,
            result_ids=claimed_ids,
            prompts=tuple(prompts),
            claim_token=claim_token,
        )

    def save_suggestions(
        self,
        claimed: ClaimedWork,
        completion: CompletionBatch,
    ) -> VehicleEnrichmentRun:
        run = self._locked_run(claimed.run_id)
        suggestions = {item.model_id: item for item in completion.items}
        results = self._locked_claim_results(claimed)
        now = datetime.now(UTC)
        for result in results:
            model = self.session.get(VehicleModel, result.vehicle_model_id)
            suggestion = suggestions.get(result.vehicle_model_id)
            if model is None or suggestion is None:
                raise InvalidEnrichmentTransition("补全领取结果与 AI 响应不一致")
            result.suggested_energy_type = suggestion.energy_type
            result.suggested_energy_subtype = suggestion.energy_subtype.value
            result.suggested_confidence = suggestion.confidence.value
            result.rationale = suggestion.rationale
            result.sources_json = suggestion.sources
            result.request_count += completion.request_count
            result.before_json = _energy_snapshot(model)
            result.error_summary = None
            result.claim_token = None

            if model.energy_source in MANUAL_SOURCES:
                result.status = EnrichmentResultStatus.KEPT_UNKNOWN
                result.not_applied_reason = "人工结论优先，未覆盖"
                result.after_json = _energy_snapshot(model)
                continue

            is_conclusive = (
                suggestion.confidence.value == "HIGH"
                and suggestion.energy_type != "未知"
                and suggestion.energy_subtype.value != "未知"
            )
            if is_conclusive:
                source = "AI_BATCH" if claimed.stage == EnrichmentStage.BATCH else "AI_FOCUSED"
                model.energy_type = suggestion.energy_type
                model.energy_subtype = suggestion.energy_subtype.value
                model.energy_source = source
                model.energy_confidence = suggestion.confidence.value
                model.energy_updated_at = now
                result.status = EnrichmentResultStatus.APPLIED
                result.applied = True
                result.not_applied_reason = None
                result.after_json = _energy_snapshot(model)
            elif claimed.stage == EnrichmentStage.BATCH:
                result.stage = EnrichmentStage.FOCUSED
                result.status = EnrichmentResultStatus.PENDING
                result.not_applied_reason = "逐车型初判未达高置信，进入 AI 二次识别"
                result.after_json = _energy_snapshot(model)
            else:
                result.status = EnrichmentResultStatus.KEPT_UNKNOWN
                result.not_applied_reason = "AI 二次识别仍无高置信结论"
                result.after_json = _energy_snapshot(model)

        run.batch_request_count += completion.request_count if claimed.stage == EnrichmentStage.BATCH else 0
        run.focused_request_count += completion.request_count if claimed.stage == EnrichmentStage.FOCUSED else 0
        run.prompt_tokens += completion.usage.prompt_tokens
        run.completion_tokens += completion.usage.completion_tokens
        run.total_tokens += completion.usage.total_tokens
        self._refresh_run(run)
        self.session.flush()
        return run

    def fail_claim(
        self,
        claimed: ClaimedWork,
        message: str,
    ) -> VehicleEnrichmentRun:
        run = self._locked_run(claimed.run_id)
        for result in self._locked_claim_results(claimed):
            result.status = EnrichmentResultStatus.FAILED
            result.claim_token = None
            result.error_summary = message
            result.not_applied_reason = "AI 请求失败"
        run.last_error_summary = message
        self._refresh_run(run)
        self.session.flush()
        return run

    def stop(self, run_id: UUID) -> VehicleEnrichmentRun:
        run = self._locked_run(run_id)
        if run.status != EnrichmentRunStatus.RUNNING:
            raise InvalidEnrichmentTransition("只有运行中的 AI 补全任务可以停止")
        run.status = EnrichmentRunStatus.STOPPED
        run.stopped_at = datetime.now(UTC)
        self.session.flush()
        return run

    def resume(self, run_id: UUID) -> VehicleEnrichmentRun:
        run = self._locked_run(run_id)
        if run.status not in (EnrichmentRunStatus.STOPPED, EnrichmentRunStatus.INTERRUPTED):
            raise InvalidEnrichmentTransition("只有已停止或已中断的 AI 补全任务可以继续")
        if self.active_run_id() not in (None, run.id):
            raise InvalidEnrichmentTransition("已有其他活动 AI 补全任务")
        run.status = EnrichmentRunStatus.RUNNING
        run.stopped_at = None
        run.completed_at = None
        self.session.flush()
        return run

    def retry_failed(self, run_id: UUID) -> VehicleEnrichmentRun:
        run = self._locked_run(run_id)
        if run.status != EnrichmentRunStatus.PARTIAL:
            raise InvalidEnrichmentTransition("只有部分完成的 AI 补全任务可以重试失败车型")
        self.session.execute(update(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.run_id == run.id,
            VehicleEnrichmentResult.status == EnrichmentResultStatus.FAILED,
        ).values(
            status=EnrichmentResultStatus.PENDING,
            claim_token=None,
            error_summary=None,
            not_applied_reason=None,
        ).execution_options(synchronize_session="fetch"))
        run.status = EnrichmentRunStatus.RUNNING
        run.completed_at = None
        run.last_error_summary = None
        self._refresh_run(run)
        self.session.flush()
        return run

    def delete_run(self, run_id: UUID) -> None:
        run = self._locked_run(run_id)
        if run.status in ACTIVE_STATUSES:
            raise InvalidEnrichmentTransition("等待或运行中的 AI 补全任务不能删除")
        in_flight = self.session.scalar(select(VehicleEnrichmentResult.id).where(
            VehicleEnrichmentResult.run_id == run.id,
            VehicleEnrichmentResult.status == EnrichmentResultStatus.RUNNING,
        ).limit(1))
        if in_flight is not None:
            raise InvalidEnrichmentTransition("AI 请求正在收尾，请稍后再删除")
        self.session.execute(delete(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.run_id == run.id))
        self.session.delete(run)
        self.session.flush()

    def resolve_manually(
        self,
        run_id: UUID,
        zuche_model_id: int,
        *,
        energy_type: str,
        energy_subtype: str,
        note: str,
    ) -> VehicleEnrichmentRun:
        run = self._locked_run(run_id)
        if run.status in ACTIVE_STATUSES:
            raise InvalidEnrichmentTransition("等待或运行中的任务不能人工处理")
        row = self.session.execute(select(
            VehicleEnrichmentResult,
            VehicleModel,
        ).join(
            VehicleModel,
            VehicleModel.id == VehicleEnrichmentResult.vehicle_model_id,
        ).where(
            VehicleEnrichmentResult.run_id == run.id,
            VehicleModel.zuche_model_id == zuche_model_id,
        ).with_for_update()).one_or_none()
        if row is None:
            raise InvalidEnrichmentTransition("该任务中不存在此车型")
        result, model = row
        if result.status not in (
            EnrichmentResultStatus.FAILED,
            EnrichmentResultStatus.KEPT_UNKNOWN,
        ):
            raise InvalidEnrichmentTransition("只能人工处理失败或仍未知的车型")

        PersonalDataService(self.session).confirm_energy(
            model,
            energy_type=energy_type,
            energy_subtype=energy_subtype,
            note=note,
        )
        result.suggested_energy_type = energy_type
        result.suggested_energy_subtype = EnergySubtype(energy_subtype).value
        result.suggested_confidence = "HIGH"
        result.rationale = note.strip()
        result.claim_token = None
        result.error_summary = None
        result.after_json = _energy_snapshot(model)
        if energy_type == "未知":
            result.status = EnrichmentResultStatus.KEPT_UNKNOWN
            result.applied = False
            result.not_applied_reason = "人工确认保留未知"
        else:
            result.status = EnrichmentResultStatus.APPLIED
            result.applied = True
            result.not_applied_reason = None
        self.session.flush()
        self._refresh_run(run)
        if run.processed_count >= run.total_count:
            run.status = (
                EnrichmentRunStatus.PARTIAL
                if run.failed_count else EnrichmentRunStatus.COMPLETED
            )
            run.completed_at = datetime.now(UTC)
        if run.failed_count == 0:
            run.last_error_summary = None
        self.session.flush()
        return run

    def interrupt_stale_runs(self) -> int:
        stale_ids = select(VehicleEnrichmentRun.id).where(
            VehicleEnrichmentRun.status.in_(ACTIVE_STATUSES))
        self.session.execute(update(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.run_id.in_(stale_ids),
            VehicleEnrichmentResult.status == EnrichmentResultStatus.RUNNING,
        ).values(
            status=EnrichmentResultStatus.PENDING,
            claim_token=None,
        ).execution_options(synchronize_session="fetch"))
        changed = self.session.execute(update(VehicleEnrichmentRun).where(
            VehicleEnrichmentRun.status.in_(ACTIVE_STATUSES),
        ).values(status=EnrichmentRunStatus.INTERRUPTED).execution_options(
            synchronize_session="fetch"))
        self.session.flush()
        return int(changed.rowcount or 0)

    def _next_stage(self, run_id: UUID) -> EnrichmentStage | None:
        for stage in (EnrichmentStage.BATCH, EnrichmentStage.FOCUSED):
            if self.session.scalar(select(VehicleEnrichmentResult.id).where(
                VehicleEnrichmentResult.run_id == run_id,
                VehicleEnrichmentResult.stage == stage,
                VehicleEnrichmentResult.status == EnrichmentResultStatus.PENDING,
            ).limit(1)) is not None:
                return stage
        return None

    def _locked_run(self, run_id: UUID) -> VehicleEnrichmentRun:
        run = self.session.scalar(select(VehicleEnrichmentRun).where(
            VehicleEnrichmentRun.id == run_id).with_for_update())
        if run is None:
            raise InvalidEnrichmentTransition("AI 补全任务不存在")
        return run

    def _locked_claim_results(self, claimed: ClaimedWork) -> list[VehicleEnrichmentResult]:
        results = list(self.session.scalars(select(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.id.in_(claimed.result_ids),
            VehicleEnrichmentResult.status == EnrichmentResultStatus.RUNNING,
            VehicleEnrichmentResult.claim_token == claimed.claim_token,
        ).with_for_update()))
        if len(results) != len(claimed.result_ids):
            raise InvalidEnrichmentTransition("AI 补全领取已失效")
        return results

    def _refresh_run(self, run: VehicleEnrichmentRun) -> None:
        counts = dict(self.session.execute(select(
            VehicleEnrichmentResult.status,
            func.count(VehicleEnrichmentResult.id),
        ).where(VehicleEnrichmentResult.run_id == run.id).group_by(
            VehicleEnrichmentResult.status)).all())
        run.processed_count = sum(int(counts.get(status, 0)) for status in TERMINAL_RESULT_STATUSES)
        run.updated_count = int(counts.get(EnrichmentResultStatus.APPLIED, 0))
        run.unknown_count = int(counts.get(EnrichmentResultStatus.KEPT_UNKNOWN, 0))
        run.failed_count = int(counts.get(EnrichmentResultStatus.FAILED, 0))
        run.updated_at = datetime.now(UTC)
        unfinished = run.total_count - run.processed_count
        if unfinished <= 0 and run.status in ACTIVE_STATUSES:
            run.status = (
                EnrichmentRunStatus.PARTIAL
                if run.failed_count else EnrichmentRunStatus.COMPLETED
            )
            run.completed_at = datetime.now(UTC)
        elif self._next_stage(run.id) == EnrichmentStage.FOCUSED:
            run.current_stage = EnrichmentStage.FOCUSED


def _energy_snapshot(model: VehicleModel) -> dict:
    return {
        "energy_type": model.energy_type,
        "energy_subtype": model.energy_subtype,
        "energy_source": model.energy_source,
        "energy_confidence": model.energy_confidence,
    }
