"""允许开放城市目录暂缺中心坐标

Revision ID: 0011_allow_city_without_coordinates
Revises: 0010_preserve_map_queries
"""

import sqlalchemy as sa
from alembic import op


revision = "0011_allow_city_without_coordinates"
down_revision = "0010_preserve_map_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("cities", "latitude", existing_type=sa.Numeric(9, 6), nullable=True)
    op.alter_column("cities", "longitude", existing_type=sa.Numeric(9, 6), nullable=True)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM cities WHERE latitude IS NULL OR longitude IS NULL"))
    op.alter_column("cities", "longitude", existing_type=sa.Numeric(9, 6), nullable=False)
    op.alter_column("cities", "latitude", existing_type=sa.Numeric(9, 6), nullable=False)
