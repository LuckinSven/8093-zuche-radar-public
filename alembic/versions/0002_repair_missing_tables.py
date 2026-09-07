"""补齐早期部署中可能缺失的数据表。"""

from alembic import op

from app.migration_schema_v1 import create_schema_v1

revision = "0002_repair_missing_tables"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_schema_v1(op.get_bind())


def downgrade() -> None:
    # 修复迁移不删除任何既有业务表，回退仅调整 Alembic 版本。
    pass
