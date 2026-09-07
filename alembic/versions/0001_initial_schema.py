"""建立车型雷达的初始数据表。"""

from alembic import op

from app.migration_schema_v1 import create_schema_v1, drop_schema_v1

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_schema_v1(op.get_bind())


def downgrade() -> None:
    drop_schema_v1(op.get_bind())
