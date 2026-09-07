"""在测试数据库隔离后再次恢复生产业务表。"""

from alembic import op

from app.migration_schema_v1 import create_schema_v1

revision = "0003_restore_schema"
down_revision = "0002_repair_missing_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_schema_v1(op.get_bind())


def downgrade() -> None:
    # 恢复迁移不删除任何业务表。
    pass
