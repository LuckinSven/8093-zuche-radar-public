"""增加车型能源 AI 补全任务和审计字段。

Revision ID: 0014_add_vehicle_energy_enrichment
Revises: 0013_add_citywide_model_library
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0014_add_vehicle_energy_enrichment"
down_revision = "0013_add_citywide_model_library"
branch_labels = None
depends_on = None


run_scope = postgresql.ENUM(
    "PENDING_ONLY", "ALL", name="enrichmentscope", create_type=False)
run_status = postgresql.ENUM(
    "PENDING", "RUNNING", "STOPPED", "INTERRUPTED", "COMPLETED", "PARTIAL", "FAILED",
    name="enrichmentrunstatus", create_type=False,
)
result_status = postgresql.ENUM(
    "PENDING", "RUNNING", "APPLIED", "KEPT_UNKNOWN", "FAILED",
    name="enrichmentresultstatus", create_type=False,
)
stage = postgresql.ENUM("BATCH", "FOCUSED", name="enrichmentstage", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (run_scope, run_status, result_status, stage):
        enum_type.create(bind, checkfirst=True)

    op.add_column("vehicle_models", sa.Column("energy_subtype", sa.String(length=64)))
    op.add_column("vehicle_models", sa.Column("energy_confidence", sa.String(length=16)))
    op.add_column("vehicle_models", sa.Column("energy_updated_at", sa.DateTime(timezone=True)))

    op.create_table(
        "vehicle_enrichment_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("singleton_key", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scope", run_scope, nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="PENDING"),
        sa.Column("current_stage", stage, nullable=False, server_default="BATCH"),
        sa.Column("model_name", sa.String(length=128)),
        sa.Column("endpoint_label", sa.String(length=255)),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("batch_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("focused_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_error_summary", sa.Text()),
        sa.CheckConstraint("singleton_key = 1", name="ck_vehicle_enrichment_singleton"),
        sa.CheckConstraint(
            "total_count >= 0 AND processed_count >= 0 AND updated_count >= 0 "
            "AND unknown_count >= 0 AND failed_count >= 0 AND batch_request_count >= 0 "
            "AND focused_request_count >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND total_tokens >= 0",
            name="ck_vehicle_enrichment_run_counts_nonnegative",
        ),
    )
    op.create_index(
        "uq_vehicle_enrichment_active", "vehicle_enrichment_runs", ["singleton_key"],
        unique=True, postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )

    op.create_table(
        "vehicle_enrichment_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("vehicle_enrichment_runs.id"), nullable=False),
        sa.Column("vehicle_model_id", sa.Integer(),
                  sa.ForeignKey("vehicle_models.id"), nullable=False),
        sa.Column("stage", stage, nullable=False, server_default="BATCH"),
        sa.Column("status", result_status, nullable=False, server_default="PENDING"),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("suggested_energy_type", sa.String(length=64)),
        sa.Column("suggested_energy_subtype", sa.String(length=64)),
        sa.Column("suggested_confidence", sa.String(length=16)),
        sa.Column("rationale", sa.Text()),
        sa.Column("sources_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("before_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("after_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("not_applied_reason", sa.Text()),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint(
            "run_id", "vehicle_model_id", name="uq_vehicle_enrichment_result_run_model"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND request_count >= 0 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 AND total_tokens >= 0",
            name="ck_vehicle_enrichment_result_counts_nonnegative",
        ),
    )
    for column in ("run_id", "vehicle_model_id", "status"):
        op.create_index(
            f"ix_vehicle_enrichment_results_{column}",
            "vehicle_enrichment_results",
            [column],
        )


def downgrade() -> None:
    for column in reversed(("run_id", "vehicle_model_id", "status")):
        op.drop_index(
            f"ix_vehicle_enrichment_results_{column}",
            table_name="vehicle_enrichment_results",
        )
    op.drop_table("vehicle_enrichment_results")
    op.drop_index("uq_vehicle_enrichment_active", table_name="vehicle_enrichment_runs")
    op.drop_table("vehicle_enrichment_runs")

    for column in ("energy_updated_at", "energy_confidence", "energy_subtype"):
        op.drop_column("vehicle_models", column)

    bind = op.get_bind()
    for enum_type in reversed((run_scope, run_status, result_status, stage)):
        enum_type.drop(bind, checkfirst=True)
