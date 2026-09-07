from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.enrichment.domain import (
    EnrichmentResultStatus,
    EnrichmentRunStatus,
    EnrichmentScope,
    EnrichmentStage,
)


class Base(DeclarativeBase):
    pass


class ScanStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PersonalState(StrEnum):
    UNTRIED = "UNTRIED"
    WANT_TO_RENT = "WANT_TO_RENT"
    RENTED = "RENTED"
    LIKED = "LIKED"
    NOT_CONSIDERING = "NOT_CONSIDERING"


class DepartmentActivityState(StrEnum):
    """网点可解释的发现状态，不因一次响应缺失而改变。"""

    DISCOVERED = "DISCOVERED"
    RECENTLY_SEEN = "RECENTLY_SEEN"
    LONG_UNSEEN = "LONG_UNSEEN"
    MANUALLY_DISABLED = "MANUALLY_DISABLED"


class DepartmentDiscoveryStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED_LIMIT = "COMPLETED_LIMIT"
    COMPLETED_NO_NEW = "COMPLETED_NO_NEW"
    FAILED = "FAILED"


class DepartmentDiscoveryPointStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CitywideScanStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"


class CitywidePointStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ModelSearchRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class ModelSearchPhase(StrEnum):
    BASE = "BASE"
    FINE = "FINE"


class ModelSearchSampleStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ModelSearchSampleKind(StrEnum):
    BASE = "BASE"
    FINE = "FINE"


class City(Base):
    __tablename__ = "cities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zuche_city_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    en_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    catalog_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    catalog_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MapSearchCache(Base):
    __tablename__ = "map_search_caches"
    __table_args__ = (UniqueConstraint(
        "provider", "normalized_region", "normalized_keyword",
        name="uq_map_search_cache_provider_region_keyword"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), default="baidu_maps")
    region: Mapped[str] = mapped_column(String(256))
    keyword: Mapped[str] = mapped_column(String(256))
    normalized_region: Mapped[str] = mapped_column(String(256))
    normalized_keyword: Mapped[str] = mapped_column(String(256))
    results_json: Mapped[list] = mapped_column(JSONB, default=list)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoordinateCache(Base):
    __tablename__ = "coordinate_caches"
    __table_args__ = (UniqueConstraint(
        "provider", "source_crs", "target_crs", "source_latitude", "source_longitude",
        name="uq_coordinate_cache_provider_crs_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), default="baidu_maps")
    source_crs: Mapped[str] = mapped_column(String(32))
    target_crs: Mapped[str] = mapped_column(String(32))
    source_latitude: Mapped[Decimal] = mapped_column(Numeric(11, 8))
    source_longitude: Mapped[Decimal] = mapped_column(Numeric(11, 8))
    target_latitude: Mapped[Decimal] = mapped_column(Numeric(11, 8))
    target_longitude: Mapped[Decimal] = mapped_column(Numeric(11, 8))
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Probe(Base):
    __tablename__ = "probes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    latitude: Mapped[float] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float] = mapped_column(Numeric(9, 6))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule: Mapped[str | None] = mapped_column(String(128), nullable=True)


class IntegrationSetting(Base):
    __tablename__ = "integration_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[ScanStatus] = mapped_column(Enum(ScanStatus), default=ScanStatus.PENDING)
    zuche_city_id: Mapped[str] = mapped_column(String(32))
    location_name: Mapped[str] = mapped_column(String(128))
    latitude: Mapped[float] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float] = mapped_column(Numeric(9, 6))
    pickup_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    return_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class RawPayload(Base):
    __tablename__ = "raw_payloads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True, index=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zuche_dept_id: Mapped[int] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    # deptId 在神州侧全局稳定，因此现有全局唯一约束继续作为更强的去重保证；
    # city_id 记录该网点最近关联的城市，允许无法从历史扫描反推城市的旧记录为空。
    city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id"), nullable=True, index=True)
    district: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_hours: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_open_24h: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    self_service_pickup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    self_service_return: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    discovery_source: Mapped[str] = mapped_column(String(32), default="CHOOSE_CAR")
    active_state: Mapped[DepartmentActivityState] = mapped_column(
        Enum(DepartmentActivityState), default=DepartmentActivityState.DISCOVERED)


class DepartmentDiscoveryRun(Base):
    __tablename__ = "department_discovery_runs"
    __table_args__ = (
        Index(
            "uq_department_discovery_active_city",
            "city_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        CheckConstraint("radius_km > 0", name="ck_department_discovery_run_radius_positive"),
        CheckConstraint("spacing_km > 0", name="ck_department_discovery_run_spacing_positive"),
        CheckConstraint("max_requests > 0", name="ck_department_discovery_run_max_requests_positive"),
        CheckConstraint("pickup_time < return_time", name="ck_department_discovery_run_valid_period"),
        CheckConstraint(
            "planned_point_count >= 0 AND completed_point_count >= 0 AND request_count >= 0 "
            "AND new_department_count >= 0 AND updated_department_count >= 0",
            name="ck_department_discovery_run_counts_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    preset: Mapped[str] = mapped_column(String(32))
    radius_km: Mapped[float] = mapped_column(Numeric(6, 2))
    spacing_km: Mapped[float] = mapped_column(Numeric(6, 2))
    max_requests: Mapped[int] = mapped_column(Integer)
    pickup_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    return_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[DepartmentDiscoveryStatus] = mapped_column(
        Enum(DepartmentDiscoveryStatus), default=DepartmentDiscoveryStatus.PENDING)
    planned_point_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_point_count: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    new_department_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_department_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class DepartmentDiscoveryPoint(Base):
    __tablename__ = "department_discovery_points"
    __table_args__ = (
        UniqueConstraint("run_id", "latitude", "longitude", name="uq_department_discovery_point_coordinate"),
        CheckConstraint("round_number >= 1", name="ck_department_discovery_point_round_positive"),
        CheckConstraint(
            "attempt_count >= 0 AND response_department_count >= 0 AND new_department_count >= 0",
            name="ck_department_discovery_point_counts_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("department_discovery_runs.id"), index=True)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    source: Mapped[str] = mapped_column(String(32))
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[DepartmentDiscoveryPointStatus] = mapped_column(
        Enum(DepartmentDiscoveryPointStatus), default=DepartmentDiscoveryPointStatus.PENDING)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_department_count: Mapped[int] = mapped_column(Integer, default=0)
    new_department_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class VehicleModel(Base):
    __tablename__ = "vehicle_models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zuche_model_id: Mapped[int] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    energy_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    energy_subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    energy_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    energy_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    energy_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VehicleEnrichmentRun(Base):
    __tablename__ = "vehicle_enrichment_runs"
    __table_args__ = (
        Index(
            "uq_vehicle_enrichment_active",
            "singleton_key",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        CheckConstraint("singleton_key = 1", name="ck_vehicle_enrichment_singleton"),
        CheckConstraint(
            "total_count >= 0 AND processed_count >= 0 AND updated_count >= 0 "
            "AND unknown_count >= 0 AND failed_count >= 0 AND batch_request_count >= 0 "
            "AND focused_request_count >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND total_tokens >= 0",
            name="ck_vehicle_enrichment_run_counts_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    singleton_key: Mapped[int] = mapped_column(Integer, default=1)
    scope: Mapped[EnrichmentScope] = mapped_column(Enum(EnrichmentScope))
    status: Mapped[EnrichmentRunStatus] = mapped_column(
        Enum(EnrichmentRunStatus), default=EnrichmentRunStatus.PENDING)
    current_stage: Mapped[EnrichmentStage] = mapped_column(
        Enum(EnrichmentStage), default=EnrichmentStage.BATCH)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    endpoint_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    batch_request_count: Mapped[int] = mapped_column(Integer, default=0)
    focused_request_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class VehicleEnrichmentResult(Base):
    __tablename__ = "vehicle_enrichment_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "vehicle_model_id", name="uq_vehicle_enrichment_result_run_model"),
        CheckConstraint(
            "attempt_count >= 0 AND request_count >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND total_tokens >= 0",
            name="ck_vehicle_enrichment_result_counts_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("vehicle_enrichment_runs.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    stage: Mapped[EnrichmentStage] = mapped_column(
        Enum(EnrichmentStage), default=EnrichmentStage.BATCH)
    status: Mapped[EnrichmentResultStatus] = mapped_column(
        Enum(EnrichmentResultStatus), default=EnrichmentResultStatus.PENDING, index=True)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    suggested_energy_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_energy_subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[list] = mapped_column(JSONB, default=list)
    before_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    after_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    not_applied_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class CitywideScanRun(Base):
    __tablename__ = "citywide_scan_runs"
    __table_args__ = (
        Index(
            "uq_citywide_scan_active_city",
            "city_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        CheckConstraint("pickup_time < return_time", name="ck_citywide_scan_run_valid_period"),
        CheckConstraint(
            "planned_point_count >= 0 AND completed_point_count >= 0 "
            "AND failed_point_count >= 0 AND request_count >= 0 "
            "AND available_model_count >= 0 AND new_model_count >= 0",
            name="ck_citywide_scan_run_counts_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    pickup_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    return_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[CitywideScanStatus] = mapped_column(
        Enum(CitywideScanStatus), default=CitywideScanStatus.PENDING)
    planned_point_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_point_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_point_count: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    available_model_count: Mapped[int] = mapped_column(Integer, default=0)
    new_model_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class CitywideScanPoint(Base):
    __tablename__ = "citywide_scan_points"
    __table_args__ = (
        UniqueConstraint("run_id", "department_id", name="uq_citywide_scan_point_department"),
        CheckConstraint(
            "attempt_count >= 0 AND response_department_count >= 0 AND response_model_count >= 0",
            name="ck_citywide_scan_point_counts_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("citywide_scan_runs.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    status: Mapped[CitywidePointStatus] = mapped_column(
        Enum(CitywidePointStatus), default=CitywidePointStatus.PENDING)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    response_department_count: Mapped[int] = mapped_column(Integer, default=0)
    response_model_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class CitywideOffer(Base):
    __tablename__ = "citywide_offers"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "vehicle_model_id", "department_id",
            name="uq_citywide_offer_run_model_department",
        ),
        CheckConstraint(
            "source_distance_km IS NULL OR source_distance_km >= 0",
            name="ck_citywide_offer_source_distance_nonnegative",
        ),
        CheckConstraint(
            "distance_from_yuzhu_km IS NULL OR distance_from_yuzhu_km >= 0",
            name="ck_citywide_offer_yuzhu_distance_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("citywide_scan_runs.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    source_point_id: Mapped[int] = mapped_column(ForeignKey("citywide_scan_points.id"), index=True)
    source_is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    source_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    distance_from_yuzhu_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    daily_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    package_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    book_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    inventory_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))


class CitywideModelSummary(Base):
    __tablename__ = "citywide_model_summaries"
    __table_args__ = (
        UniqueConstraint("run_id", "vehicle_model_id", name="uq_citywide_summary_run_model"),
        CheckConstraint(
            "available_department_count >= 0",
            name="ck_citywide_summary_department_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("citywide_scan_runs.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    available_department_count: Mapped[int] = mapped_column(Integer, default=0)
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    minimum_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    maximum_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    nearest_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    nearest_department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True)
    first_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CitywideRawPayload(Base):
    __tablename__ = "citywide_raw_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("citywide_scan_runs.id"), index=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("citywide_scan_points.id"), index=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class ModelSearchRun(Base):
    __tablename__ = "model_search_runs"
    __table_args__ = (
        Index(
            "uq_model_search_active_city",
            "city_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        CheckConstraint(
            "estimated_request_count >= 0 AND planned_sample_count >= 0 "
            "AND completed_sample_count >= 0 AND failed_sample_count >= 0 "
            "AND cache_hit_count >= 0 AND request_count >= 0 "
            "AND candidate_department_count >= 0 AND found_variant_count >= 0 "
            "AND available_department_count >= 0",
            name="ck_model_search_run_counts_nonnegative",
        ),
        CheckConstraint("execution_concurrency IN (1, 2)", name="ck_model_search_concurrency"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    search_kind: Mapped[str] = mapped_column(String(32), default="WEEKEND")
    return_city_id: Mapped[int | None] = mapped_column(
        ForeignKey("cities.id"), nullable=True, index=True)
    return_location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pickup_city_ids: Mapped[list] = mapped_column(JSONB, default=list)
    rental_windows: Mapped[list] = mapped_column(JSONB, default=list)
    rail_costs: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[ModelSearchRunStatus] = mapped_column(
        Enum(ModelSearchRunStatus), default=ModelSearchRunStatus.PENDING)
    phase: Mapped[ModelSearchPhase] = mapped_column(
        Enum(ModelSearchPhase), default=ModelSearchPhase.BASE)
    target_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    request_version: Mapped[str] = mapped_column(String(32), default="choose-car-v3-1")
    requested_names: Mapped[list] = mapped_column(JSONB, default=list)
    estimated_request_count: Mapped[int] = mapped_column(Integer, default=0)
    planned_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_department_count: Mapped[int] = mapped_column(Integer, default=0)
    found_variant_count: Mapped[int] = mapped_column(Integer, default=0)
    available_department_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_concurrency: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ModelSearchTarget(Base):
    __tablename__ = "model_search_targets"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "vehicle_model_id", name="uq_model_search_target_run_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("model_search_runs.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    requested_name: Mapped[str] = mapped_column(String(255), index=True)
    zuche_model_id: Mapped[int] = mapped_column(Integer, index=True)


class ModelSearchSample(Base):
    __tablename__ = "model_search_samples"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "anchor_department_id", "pickup_time", "return_time",
            name="uq_model_search_sample_request",
        ),
        CheckConstraint("pickup_time < return_time", name="ck_model_search_sample_valid_period"),
        CheckConstraint(
            "attempt_count >= 0 AND response_department_count >= 0 "
            "AND response_model_count >= 0",
            name="ck_model_search_sample_counts_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("model_search_runs.id"), index=True)
    anchor_department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    kind: Mapped[ModelSearchSampleKind] = mapped_column(Enum(ModelSearchSampleKind))
    status: Mapped[ModelSearchSampleStatus] = mapped_column(
        Enum(ModelSearchSampleStatus), default=ModelSearchSampleStatus.PENDING, index=True)
    pickup_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    return_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cache_source_sample_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_search_samples.id"), nullable=True, index=True)
    response_department_count: Mapped[int] = mapped_column(Integer, default=0)
    response_model_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ModelSearchOffer(Base):
    __tablename__ = "model_search_offers"
    __table_args__ = (
        UniqueConstraint(
            "sample_id", "vehicle_model_id", "department_id",
            name="uq_model_search_offer_sample_model_department",
        ),
        CheckConstraint(
            "distance_from_yuzhu_km IS NULL OR distance_from_yuzhu_km >= 0",
            name="ck_model_search_offer_distance_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("model_search_runs.id"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("model_search_samples.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    daily_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    package_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    book_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    inventory_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    distance_from_yuzhu_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class AvailabilitySnapshot(Base):
    __tablename__ = "availability_snapshots"
    __table_args__ = (UniqueConstraint("scan_run_id", "department_id", "vehicle_model_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[UUID] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    daily_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    package_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    model_desc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    book_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    inventory_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    department_distance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    department_distance_km: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)


class ModelGroupMembership(Base):
    __tablename__ = "model_group_memberships"
    __table_args__ = (UniqueConstraint("scan_run_id", "vehicle_model_id", "group_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[UUID] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    group_id: Mapped[int] = mapped_column(Integer)
    group_name: Mapped[str] = mapped_column(String(128))


class PersonalVehicleState(Base):
    __tablename__ = "personal_vehicle_states"
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), primary_key=True)
    state: Mapped[PersonalState] = mapped_column(Enum(PersonalState), default=PersonalState.UNTRIED)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rented_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class VehicleAnnotation(Base):
    __tablename__ = "vehicle_annotations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[str] = mapped_column(String(16))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChangeEvent(Base):
    __tablename__ = "change_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[UUID] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    vehicle_model_id: Mapped[int] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
