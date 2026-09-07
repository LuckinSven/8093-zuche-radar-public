"""扩展按车型找车任务以支持多城市异地还车。

Revision ID: 0016_add_cross_city_search
Revises: 0015_add_model_search
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0016_add_cross_city_search"
down_revision = "0015_add_model_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_search_runs", sa.Column(
        "search_kind", sa.String(length=32), nullable=False, server_default="WEEKEND"))
    op.add_column("model_search_runs", sa.Column(
        "return_city_id", sa.Integer(), sa.ForeignKey("cities.id"), nullable=True))
    op.add_column("model_search_runs", sa.Column(
        "return_location_name", sa.String(length=255), nullable=True))
    op.add_column("model_search_runs", sa.Column(
        "pickup_city_ids", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("model_search_runs", sa.Column(
        "rental_windows", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("model_search_runs", sa.Column(
        "rail_costs", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.execute("UPDATE model_search_runs SET return_city_id = city_id")
    op.create_index(
        "ix_model_search_runs_return_city_id", "model_search_runs", ["return_city_id"])


def downgrade() -> None:
    op.drop_index("ix_model_search_runs_return_city_id", table_name="model_search_runs")
    for column in (
        "rail_costs", "rental_windows", "pickup_city_ids", "return_location_name",
        "return_city_id", "search_kind",
    ):
        op.drop_column("model_search_runs", column)
