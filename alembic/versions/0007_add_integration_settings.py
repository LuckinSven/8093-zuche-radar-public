"""新增第三方 API 配置表

Revision ID: 0007_add_integration_settings
Revises: 0006_seed_guangzhou
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0007_add_integration_settings"
down_revision = "0006_seed_guangzhou"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("secret_value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("provider", name="uq_integration_settings_provider"),
    )
    op.create_index("ix_integration_settings_provider", "integration_settings", ["provider"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_integration_settings_provider", table_name="integration_settings")
    op.drop_table("integration_settings")
