"""保留地图查询原文和最后命中时间

Revision ID: 0010_preserve_map_queries
Revises: 0009_add_department_discovery
"""

import sqlalchemy as sa
from alembic import op


revision = "0010_preserve_map_queries"
down_revision = "0009_add_department_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("map_search_caches", sa.Column("region", sa.String(length=256), nullable=True))
    op.add_column("map_search_caches", sa.Column("keyword", sa.String(length=256), nullable=True))
    op.add_column("map_search_caches", sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text(
        "UPDATE map_search_caches SET region = normalized_region, keyword = normalized_keyword "
        "WHERE region IS NULL OR keyword IS NULL"))
    op.alter_column("map_search_caches", "region", existing_type=sa.String(length=256), nullable=False)
    op.alter_column("map_search_caches", "keyword", existing_type=sa.String(length=256), nullable=False)


def downgrade() -> None:
    op.drop_column("map_search_caches", "last_hit_at")
    op.drop_column("map_search_caches", "keyword")
    op.drop_column("map_search_caches", "region")
