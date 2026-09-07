from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class ScanQuery(BaseModel):
    city_id: str
    return_city_id: str | None = None
    location_name: str = Field(min_length=1, max_length=128)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    pickup_time: datetime
    return_time: datetime

    @model_validator(mode="after")
    def validate_period(self) -> "ScanQuery":
        if self.return_time <= self.pickup_time:
            raise ValueError("还车时间必须晚于取车时间")
        return self

    @property
    def effective_return_city_id(self) -> str:
        return self.return_city_id or self.city_id


class CityResolution(BaseModel):
    city_id: str
    city_name: str


class ParsedDepartment(BaseModel):
    department_id: int
    name: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float | None = None
    business_hours: str | None = None
    is_open_24h: bool | None = None
    self_service_pickup: bool | None = None
    self_service_return: bool | None = None


class ParsedOffer(BaseModel):
    department_id: int
    model_id: int
    model_name: str
    daily_price: Decimal | None = None
    package_price: Decimal | None = None
    distance_km: float | None = None
    distance_text: str | None = None
    model_desc: str | None = None
    image_url: str | None = None
    body_style: str | None = None
    seat_count: int | None = None
    energy_type: str | None = None
    bookable: bool = False
    inventory_type: int | None = None


class ParsedGroup(BaseModel):
    model_id: int
    group_id: int
    group_name: str
    group_low_price_desc: str | None = None
    sort_num: int | None = None
    model_name: str | None = None
    model_low_price_desc: str | None = None
    model_image_url: str | None = None


class ParsedScan(BaseModel):
    query: ScanQuery
    departments: list[ParsedDepartment]
    offers: list[ParsedOffer]
    groups: list[ParsedGroup]
