"""扫描历史保存神州城市 ID。"""

import sqlalchemy as sa
from alembic import op

revision = "0004_add_scan_city_id"
down_revision = "0003_restore_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("scan_runs")}
    if "zuche_city_id" not in columns:
        op.add_column("scan_runs", sa.Column("zuche_city_id", sa.String(length=32),
                                             nullable=False, server_default="14"))
        op.alter_column("scan_runs", "zuche_city_id", server_default=None)


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("scan_runs")}
    if "zuche_city_id" in columns:
        op.drop_column("scan_runs", "zuche_city_id")
