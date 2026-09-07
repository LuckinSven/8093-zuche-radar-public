"""冻结的 v1 数据库结构；历史迁移不得依赖当前 ORM 模型。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def metadata_v1() -> sa.MetaData:
    metadata = sa.MetaData()
    scan_status = sa.Enum("PENDING", "SUCCESS", "FAILED", name="scanstatus")
    personal_state = sa.Enum("UNTRIED", "WANT_TO_RENT", "RENTED", "LIKED", "NOT_CONSIDERING",
                             name="personalstate")
    cities = sa.Table("cities", metadata,
        sa.Column("id", sa.Integer, primary_key=True), sa.Column("zuche_city_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False), sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False), sa.Column("enabled", sa.Boolean, nullable=False))
    probes = sa.Table("probes", metadata,
        sa.Column("id", sa.Integer, primary_key=True), sa.Column("city_id", sa.Integer, sa.ForeignKey("cities.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False), sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False), sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("schedule", sa.String(128)))
    runs = sa.Table("scan_runs", metadata,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", scan_status, nullable=False), sa.Column("location_name", sa.String(128), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False), sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("pickup_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("return_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text))
    payloads = sa.Table("raw_payloads", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scan_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scan_runs.id")),
        sa.Column("payload", sa.LargeBinary, nullable=False), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False))
    departments = sa.Table("departments", metadata,
        sa.Column("id", sa.Integer, primary_key=True), sa.Column("zuche_dept_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("address", sa.Text),
        sa.Column("latitude", sa.Numeric(9, 6)), sa.Column("longitude", sa.Numeric(9, 6)))
    models = sa.Table("vehicle_models", metadata,
        sa.Column("id", sa.Integer, primary_key=True), sa.Column("zuche_model_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False))
    snapshots = sa.Table("availability_snapshots", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scan_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scan_runs.id"), nullable=False),
        sa.Column("department_id", sa.Integer, sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer, sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("daily_price", sa.Numeric(10, 2)), sa.Column("package_price", sa.Numeric(10, 2)),
        sa.Column("model_desc", sa.String(255)), sa.Column("book_flag", sa.Boolean, nullable=False),
        sa.Column("inventory_type", sa.Integer), sa.Column("department_distance", sa.String(32)),
        sa.UniqueConstraint("scan_run_id", "department_id", "vehicle_model_id"))
    memberships = sa.Table("model_group_memberships", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scan_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scan_runs.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer, sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("group_id", sa.Integer, nullable=False), sa.Column("group_name", sa.String(128), nullable=False),
        sa.UniqueConstraint("scan_run_id", "vehicle_model_id", "group_id"))
    personal = sa.Table("personal_vehicle_states", metadata,
        sa.Column("vehicle_model_id", sa.Integer, sa.ForeignKey("vehicle_models.id"), primary_key=True),
        sa.Column("state", personal_state, nullable=False), sa.Column("note", sa.Text),
        sa.Column("rented_on", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    annotations = sa.Table("vehicle_annotations", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("vehicle_model_id", sa.Integer, sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False), sa.Column("value", sa.Text, nullable=False),
        sa.Column("source", sa.String(255), nullable=False), sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)))
    events = sa.Table("change_events", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scan_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scan_runs.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer, sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("department_id", sa.Integer, sa.ForeignKey("departments.id")),
        sa.Column("event_type", sa.String(32), nullable=False), sa.Column("detail", sa.Text))
    sa.Index("ix_cities_zuche_city_id", cities.c.zuche_city_id, unique=True)
    sa.Index("ix_probes_city_id", probes.c.city_id)
    sa.Index("ix_raw_payloads_scan_run_id", payloads.c.scan_run_id)
    sa.Index("ix_raw_payloads_captured_at", payloads.c.captured_at)
    sa.Index("ix_departments_zuche_dept_id", departments.c.zuche_dept_id, unique=True)
    sa.Index("ix_vehicle_models_zuche_model_id", models.c.zuche_model_id, unique=True)
    for table, column in ((snapshots, snapshots.c.scan_run_id), (snapshots, snapshots.c.department_id),
                          (snapshots, snapshots.c.vehicle_model_id), (memberships, memberships.c.scan_run_id),
                          (memberships, memberships.c.vehicle_model_id), (annotations, annotations.c.vehicle_model_id),
                          (events, events.c.scan_run_id), (events, events.c.vehicle_model_id)):
        sa.Index(f"ix_{table.name}_{column.name}", column)
    return metadata


def create_schema_v1(bind) -> None:
    metadata_v1().create_all(bind)


def drop_schema_v1(bind) -> None:
    metadata_v1().drop_all(bind)
