"""修正神州网点距离的米/公里单位。"""

import sqlalchemy as sa
from alembic import op


revision = "0012_fix_department_distance_units"
down_revision = "0011_allow_city_without_coordinates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE availability_snapshots
           SET department_distance_km = CASE
               WHEN lower(coalesce(department_distance, '')) LIKE '%km%'
                   THEN (regexp_match(department_distance, '[0-9]+(?:\\.[0-9]+)?'))[1]::numeric
               WHEN lower(coalesce(department_distance, '')) LIKE '%m%'
                   THEN (regexp_match(department_distance, '[0-9]+(?:\\.[0-9]+)?'))[1]::numeric / 1000
               ELSE department_distance_km / 1000
           END
         WHERE department_distance_km IS NOT NULL OR department_distance ~ '[0-9]'
    """))


def downgrade() -> None:
    pass
