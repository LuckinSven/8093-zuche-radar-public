from datetime import datetime

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    """一次神州车型查询所需的固定输入。"""

    city_id: str
    location_name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    pickup_time: datetime
    return_time: datetime


class CityResolution(BaseModel):
    """神州城市定位接口返回的城市标识。"""

    city_id: str
    city_name: str


class ParsedDepartment(BaseModel):
    zuche_dept_id: int
    name: str
    address: str | None
    latitude: float | None
    longitude: float | None
    distance_km: float | None
    business_hours: str | None = None
    is_open_24h: bool | None = None
    self_service_pickup: bool | None = None
    self_service_return: bool | None = None


class ParsedAvailability(BaseModel):
    zuche_dept_id: int
    zuche_model_id: int
    model_name: str
    daily_price: float | None
    package_price: float | None
    model_desc: str | None
    body_style: str | None
    seat_count: int | None
    energy_type: str | None
    book_flag: bool
    inventory_type: int | None
    department_distance: str | None


class ParsedGroupMembership(BaseModel):
    zuche_model_id: int
    group_id: int
    group_name: str


class ParsedScan(BaseModel):
    request: ScanRequest
    departments: list[ParsedDepartment]
    availabilities: list[ParsedAvailability]
    groups: list[ParsedGroupMembership]
