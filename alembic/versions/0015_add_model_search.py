"""增加广州按车型反向找车任务。

Revision ID: 0015_add_model_search
Revises: 0014_add_vehicle_energy_enrichment
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0015_add_model_search"
down_revision = "0014_add_vehicle_energy_enrichment"
branch_labels = None
depends_on = None


run_status = postgresql.ENUM(
    "PENDING", "RUNNING", "STOPPED", "INTERRUPTED", "COMPLETED", "PARTIAL",
    "BUDGET_EXCEEDED", name="modelsearchrunstatus", create_type=False,
)
phase = postgresql.ENUM("BASE", "FINE", name="modelsearchphase", create_type=False)
sample_status = postgresql.ENUM(
    "PENDING", "RUNNING", "COMPLETED", "FAILED",
    name="modelsearchsamplestatus", create_type=False,
)
sample_kind = postgresql.ENUM(
    "BASE", "FINE", name="modelsearchsamplekind", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (run_status, phase, sample_status, sample_kind):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "model_search_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id"), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="PENDING"),
        sa.Column("phase", phase, nullable=False, server_default="BASE"),
        sa.Column("target_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_version", sa.String(length=32), nullable=False,
                  server_default="choose-car-v3-1"),
        sa.Column("requested_names", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
        sa.Column("estimated_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planned_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("found_variant_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_concurrency", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_summary", sa.Text()),
        sa.CheckConstraint(
            "estimated_request_count >= 0 AND planned_sample_count >= 0 "
            "AND completed_sample_count >= 0 AND failed_sample_count >= 0 "
            "AND cache_hit_count >= 0 AND request_count >= 0 "
            "AND candidate_department_count >= 0 AND found_variant_count >= 0 "
            "AND available_department_count >= 0",
            name="ck_model_search_run_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "execution_concurrency IN (1, 2)", name="ck_model_search_concurrency"),
    )
    op.create_index("ix_model_search_runs_city_id", "model_search_runs", ["city_id"])
    op.create_index(
        "ix_model_search_runs_target_fingerprint", "model_search_runs", ["target_fingerprint"])
    op.create_index(
        "uq_model_search_active_city", "model_search_runs", ["city_id"], unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )

    op.create_table(
        "model_search_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("model_search_runs.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer(),
                  sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("requested_name", sa.String(length=255), nullable=False),
        sa.Column("zuche_model_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "run_id", "vehicle_model_id", name="uq_model_search_target_run_model"),
    )
    for column in ("run_id", "vehicle_model_id", "requested_name", "zuche_model_id"):
        op.create_index(
            f"ix_model_search_targets_{column}", "model_search_targets", [column])

    op.create_table(
        "model_search_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("model_search_runs.id"), nullable=False),
        sa.Column("anchor_department_id", sa.Integer(),
                  sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("kind", sample_kind, nullable=False),
        sa.Column("status", sample_status, nullable=False, server_default="PENDING"),
        sa.Column("pickup_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("return_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("cache_source_sample_id", sa.Integer(),
                  sa.ForeignKey("model_search_samples.id")),
        sa.Column("response_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_model_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_summary", sa.Text()),
        sa.UniqueConstraint(
            "run_id", "anchor_department_id", "pickup_time", "return_time",
            name="uq_model_search_sample_request",
        ),
        sa.CheckConstraint(
            "pickup_time < return_time", name="ck_model_search_sample_valid_period"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND response_department_count >= 0 "
            "AND response_model_count >= 0",
            name="ck_model_search_sample_counts_nonnegative",
        ),
    )
    for column in (
        "run_id", "anchor_department_id", "status", "pickup_time", "return_time",
        "cache_source_sample_id",
    ):
        op.create_index(
            f"ix_model_search_samples_{column}", "model_search_samples", [column])

    op.create_table(
        "model_search_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("model_search_runs.id"), nullable=False),
        sa.Column("sample_id", sa.Integer(),
                  sa.ForeignKey("model_search_samples.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer(),
                  sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("department_id", sa.Integer(),
                  sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("daily_price", sa.Numeric(10, 2)),
        sa.Column("package_price", sa.Numeric(10, 2)),
        sa.Column("book_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("inventory_type", sa.Integer()),
        sa.Column("model_description", sa.Text()),
        sa.Column("distance_from_yuzhu_km", sa.Numeric(10, 3)),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint(
            "sample_id", "vehicle_model_id", "department_id",
            name="uq_model_search_offer_sample_model_department",
        ),
        sa.CheckConstraint(
            "distance_from_yuzhu_km IS NULL OR distance_from_yuzhu_km >= 0",
            name="ck_model_search_offer_distance_nonnegative",
        ),
    )
    for column in ("run_id", "sample_id", "vehicle_model_id", "department_id", "verified_at"):
        op.create_index(
            f"ix_model_search_offers_{column}", "model_search_offers", [column])


def downgrade() -> None:
    for column in reversed((
        "run_id", "sample_id", "vehicle_model_id", "department_id", "verified_at",
    )):
        op.drop_index(f"ix_model_search_offers_{column}", table_name="model_search_offers")
    op.drop_table("model_search_offers")

    for column in reversed((
        "run_id", "anchor_department_id", "status", "pickup_time", "return_time",
        "cache_source_sample_id",
    )):
        op.drop_index(f"ix_model_search_samples_{column}", table_name="model_search_samples")
    op.drop_table("model_search_samples")

    for column in reversed(("run_id", "vehicle_model_id", "requested_name", "zuche_model_id")):
        op.drop_index(f"ix_model_search_targets_{column}", table_name="model_search_targets")
    op.drop_table("model_search_targets")

    op.drop_index("uq_model_search_active_city", table_name="model_search_runs")
    op.drop_index("ix_model_search_runs_target_fingerprint", table_name="model_search_runs")
    op.drop_index("ix_model_search_runs_city_id", table_name="model_search_runs")
    op.drop_table("model_search_runs")

    bind = op.get_bind()
    for enum_type in reversed((run_status, phase, sample_status, sample_kind)):
        enum_type.drop(bind, checkfirst=True)
