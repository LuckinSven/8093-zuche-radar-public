"""增加广州全城扫描任务和永久车型库字段。

Revision ID: 0013_add_citywide_model_library
Revises: 0012_fix_department_distance_units
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0013_add_citywide_model_library"
down_revision = "0012_fix_department_distance_units"
branch_labels = None
depends_on = None


scan_status = postgresql.ENUM(
    "PENDING", "RUNNING", "STOPPED", "INTERRUPTED", "COMPLETED", "PARTIAL",
    name="citywidescanstatus", create_type=False,
)
point_status = postgresql.ENUM(
    "PENDING", "RUNNING", "COMPLETED", "FAILED",
    name="citywidepointstatus", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    scan_status.create(bind, checkfirst=True)
    point_status.create(bind, checkfirst=True)

    op.add_column("vehicle_models", sa.Column("first_seen_at", sa.DateTime(timezone=True)))
    op.add_column("vehicle_models", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.add_column("vehicle_models", sa.Column("latest_description", sa.Text()))
    op.add_column("vehicle_models", sa.Column("image_url", sa.Text()))
    op.add_column("vehicle_models", sa.Column("energy_type", sa.String(length=64)))
    op.add_column("vehicle_models", sa.Column("energy_source", sa.String(length=32)))
    op.execute(sa.text("""
        UPDATE vehicle_models AS model
           SET first_seen_at = history.first_seen_at,
               last_seen_at = history.last_seen_at
          FROM (
              SELECT snapshot.vehicle_model_id,
                     MIN(run.started_at) AS first_seen_at,
                     MAX(COALESCE(run.completed_at, run.started_at)) AS last_seen_at
                FROM availability_snapshots AS snapshot
                JOIN scan_runs AS run ON run.id = snapshot.scan_run_id
               GROUP BY snapshot.vehicle_model_id
          ) AS history
         WHERE model.id = history.vehicle_model_id
    """))

    op.create_table(
        "citywide_scan_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id"), nullable=False),
        sa.Column("pickup_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("return_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", scan_status, nullable=False, server_default="PENDING"),
        sa.Column("planned_point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_model_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_model_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_summary", sa.Text()),
        sa.CheckConstraint("pickup_time < return_time", name="ck_citywide_scan_run_valid_period"),
        sa.CheckConstraint(
            "planned_point_count >= 0 AND completed_point_count >= 0 "
            "AND failed_point_count >= 0 AND request_count >= 0 "
            "AND available_model_count >= 0 AND new_model_count >= 0",
            name="ck_citywide_scan_run_counts_nonnegative",
        ),
    )
    op.create_index("ix_citywide_scan_runs_city_id", "citywide_scan_runs", ["city_id"])
    op.create_index(
        "uq_citywide_scan_active_city", "citywide_scan_runs", ["city_id"], unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )

    op.create_table(
        "citywide_scan_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("citywide_scan_runs.id"), nullable=False),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("status", point_status, nullable=False, server_default="PENDING"),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_model_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_summary", sa.Text()),
        sa.UniqueConstraint("run_id", "department_id", name="uq_citywide_scan_point_department"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND response_department_count >= 0 AND response_model_count >= 0",
            name="ck_citywide_scan_point_counts_nonnegative",
        ),
    )
    op.create_index("ix_citywide_scan_points_run_id", "citywide_scan_points", ["run_id"])
    op.create_index("ix_citywide_scan_points_department_id", "citywide_scan_points", ["department_id"])

    op.create_table(
        "citywide_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("citywide_scan_runs.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer(), sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("source_point_id", sa.Integer(), sa.ForeignKey("citywide_scan_points.id"), nullable=False),
        sa.Column("source_is_self", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_distance_km", sa.Numeric(10, 3)),
        sa.Column("distance_from_yuzhu_km", sa.Numeric(10, 3)),
        sa.Column("daily_price", sa.Numeric(10, 2)),
        sa.Column("package_price", sa.Numeric(10, 2)),
        sa.Column("book_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("inventory_type", sa.Integer()),
        sa.Column("model_description", sa.Text()),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint(
            "run_id", "vehicle_model_id", "department_id",
            name="uq_citywide_offer_run_model_department",
        ),
        sa.CheckConstraint(
            "source_distance_km IS NULL OR source_distance_km >= 0",
            name="ck_citywide_offer_source_distance_nonnegative",
        ),
        sa.CheckConstraint(
            "distance_from_yuzhu_km IS NULL OR distance_from_yuzhu_km >= 0",
            name="ck_citywide_offer_yuzhu_distance_nonnegative",
        ),
    )
    for column in ("run_id", "vehicle_model_id", "department_id", "source_point_id"):
        op.create_index(f"ix_citywide_offers_{column}", "citywide_offers", [column])

    op.create_table(
        "citywide_model_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("citywide_scan_runs.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer(), sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("available_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_price", sa.Numeric(10, 2)),
        sa.Column("minimum_price", sa.Numeric(10, 2)),
        sa.Column("maximum_price", sa.Numeric(10, 2)),
        sa.Column("nearest_distance_km", sa.Numeric(10, 3)),
        sa.Column("nearest_department_id", sa.Integer(), sa.ForeignKey("departments.id")),
        sa.Column("first_observed_at", sa.DateTime(timezone=True)),
        sa.Column("last_observed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "vehicle_model_id", name="uq_citywide_summary_run_model"),
        sa.CheckConstraint(
            "available_department_count >= 0",
            name="ck_citywide_summary_department_count_nonnegative",
        ),
    )
    op.create_index("ix_citywide_model_summaries_run_id", "citywide_model_summaries", ["run_id"])
    op.create_index(
        "ix_citywide_model_summaries_vehicle_model_id",
        "citywide_model_summaries", ["vehicle_model_id"],
    )

    op.create_table(
        "citywide_raw_payloads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("citywide_scan_runs.id"), nullable=False),
        sa.Column("point_id", sa.Integer(), sa.ForeignKey("citywide_scan_points.id"), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_citywide_raw_payloads_run_id", "citywide_raw_payloads", ["run_id"])
    op.create_index("ix_citywide_raw_payloads_point_id", "citywide_raw_payloads", ["point_id"])
    op.create_index("ix_citywide_raw_payloads_captured_at", "citywide_raw_payloads", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_citywide_raw_payloads_captured_at", table_name="citywide_raw_payloads")
    op.drop_index("ix_citywide_raw_payloads_point_id", table_name="citywide_raw_payloads")
    op.drop_index("ix_citywide_raw_payloads_run_id", table_name="citywide_raw_payloads")
    op.drop_table("citywide_raw_payloads")

    op.drop_index("ix_citywide_model_summaries_vehicle_model_id",
                  table_name="citywide_model_summaries")
    op.drop_index("ix_citywide_model_summaries_run_id", table_name="citywide_model_summaries")
    op.drop_table("citywide_model_summaries")

    for column in reversed(("run_id", "vehicle_model_id", "department_id", "source_point_id")):
        op.drop_index(f"ix_citywide_offers_{column}", table_name="citywide_offers")
    op.drop_table("citywide_offers")

    op.drop_index("ix_citywide_scan_points_department_id", table_name="citywide_scan_points")
    op.drop_index("ix_citywide_scan_points_run_id", table_name="citywide_scan_points")
    op.drop_table("citywide_scan_points")

    op.drop_index("uq_citywide_scan_active_city", table_name="citywide_scan_runs")
    op.drop_index("ix_citywide_scan_runs_city_id", table_name="citywide_scan_runs")
    op.drop_table("citywide_scan_runs")

    for column in (
        "energy_source", "energy_type", "image_url", "latest_description",
        "last_seen_at", "first_seen_at",
    ):
        op.drop_column("vehicle_models", column)

    point_status.drop(op.get_bind(), checkfirst=True)
    scan_status.drop(op.get_bind(), checkfirst=True)
