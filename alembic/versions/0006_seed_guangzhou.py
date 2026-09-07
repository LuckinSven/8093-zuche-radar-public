"""初始化广州城市

Revision ID: 0006_seed_guangzhou
Revises: 0005_add_numeric_distance
"""

from alembic import op


revision = "0006_seed_guangzhou"
down_revision = "0005_add_numeric_distance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO cities (zuche_city_id, name, latitude, longitude, enabled)
        VALUES ('14', '广州', 23.129100, 113.264400, true)
        ON CONFLICT (zuche_city_id) DO NOTHING
    """)


def downgrade() -> None:
    # 城市可能已经被用户使用或修改，回退结构版本时不删除个人数据。
    pass
