"""数据库事务关闭后仍可安全使用的全城扫描值对象。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CitywidePointClaim:
    run_id: UUID
    point_id: int
    claim_token: UUID
    city_id: int
    zuche_city_id: str
    anchor_department_id: int
    anchor_zuche_dept_id: int
    anchor_name: str
    latitude: float
    longitude: float
    pickup_time: datetime
    return_time: datetime


@dataclass(frozen=True, slots=True)
class CitywideClaimBatch:
    state: str
    claims: tuple[CitywidePointClaim, ...] = ()
