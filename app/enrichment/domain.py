"""车型能源补全的稳定枚举与结构化数据契约。"""

from enum import StrEnum
from typing import Literal
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class EnergySubtype(StrEnum):
    GASOLINE = "汽油"
    DIESEL = "柴油"
    PURE_ELECTRIC = "纯电"
    PLUG_IN_HYBRID = "插电混动"
    RANGE_EXTENDER = "增程"
    HYBRID = "油电混动"
    OTHER = "其他"
    UNKNOWN = "未知"


class EnergyConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EnrichmentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class EnrichmentScope(StrEnum):
    PENDING_ONLY = "PENDING_ONLY"
    ALL = "ALL"


class EnrichmentResultStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    APPLIED = "APPLIED"
    KEPT_UNKNOWN = "KEPT_UNKNOWN"
    FAILED = "FAILED"


class EnrichmentStage(StrEnum):
    BATCH = "BATCH"
    FOCUSED = "FOCUSED"


class ModelPrompt(BaseModel):
    model_id: int
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    current_energy_type: str | None = Field(default=None, max_length=64)
    current_energy_subtype: str | None = Field(default=None, max_length=64)
    current_energy_source: str | None = Field(default=None, max_length=32)


class EnergySuggestion(BaseModel):
    model_id: int
    energy_type: Literal["燃油", "新能源", "未知"]
    energy_subtype: EnergySubtype
    confidence: EnergyConfidence
    rationale: str = Field(min_length=1, max_length=1000)
    sources: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_pair(self):
        validate_energy_pair(self.energy_type, self.energy_subtype)
        return self


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class CompletionBatch(BaseModel):
    items: list[EnergySuggestion]
    usage: TokenUsage = Field(default_factory=TokenUsage)
    request_count: int = Field(default=1, ge=1)


class ConnectionTestResult(BaseModel):
    ok: bool
    elapsed_ms: int = Field(ge=0)
    model: str
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ClaimedWork:
    run_id: UUID
    stage: EnrichmentStage
    result_ids: tuple[int, ...]
    prompts: tuple[ModelPrompt, ...]
    claim_token: UUID


@dataclass(frozen=True, slots=True)
class EnrichmentProcessResult:
    state: str
    processed: int = 0


def validate_energy_pair(energy_type: str, subtype: EnergySubtype) -> None:
    allowed = {
        "燃油": {
            EnergySubtype.GASOLINE,
            EnergySubtype.DIESEL,
            EnergySubtype.HYBRID,
            EnergySubtype.OTHER,
            EnergySubtype.UNKNOWN,
        },
        "新能源": {
            EnergySubtype.PURE_ELECTRIC,
            EnergySubtype.PLUG_IN_HYBRID,
            EnergySubtype.RANGE_EXTENDER,
            EnergySubtype.OTHER,
            EnergySubtype.UNKNOWN,
        },
        "未知": {EnergySubtype.UNKNOWN},
    }
    if energy_type not in allowed or subtype not in allowed[energy_type]:
        raise ValueError("能源大类与细分类型不一致")
