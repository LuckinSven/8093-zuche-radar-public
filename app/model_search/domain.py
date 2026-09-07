"""按车型找车任务在数据库事务外使用的值对象。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RentalWindow:
    pickup: datetime
    return_time: datetime

    @property
    def duration_hours(self) -> int:
        return int((self.return_time - self.pickup).total_seconds() // 3600)


@dataclass(frozen=True, slots=True)
class ModelSearchSampleClaim:
    run_id: UUID
    sample_id: int
    claim_token: UUID
    city_id: int
    zuche_city_id: str
    return_zuche_city_id: str
    target_model_ids: tuple[int, ...]
    target_fingerprint: str
    anchor_department_id: int
    anchor_zuche_dept_id: int
    anchor_name: str
    latitude: float
    longitude: float
    pickup_time: datetime
    return_time: datetime


@dataclass(frozen=True, slots=True)
class ModelSearchClaimBatch:
    state: str
    claims: tuple[ModelSearchSampleClaim, ...] = ()
