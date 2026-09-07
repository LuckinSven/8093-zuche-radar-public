"""快照保存结构化公里距离。"""

import sqlalchemy as sa
from alembic import op

revision = "0005_add_numeric_distance"
down_revision = "0004_add_scan_city_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("availability_snapshots")}
    if "department_distance_km" not in columns:
        op.add_column("availability_snapshots",
                      sa.Column("department_distance_km", sa.Numeric(10, 3), nullable=True))
        op.execute(sa.text("""
            UPDATE availability_snapshots
               SET department_distance_km = regexp_replace(department_distance, '[^0-9.]', '', 'g')::numeric
             WHERE department_distance ~ '[0-9]'
        """))


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("availability_snapshots")}
    if "department_distance_km" in columns:
        op.drop_column("availability_snapshots", "department_distance_km")
