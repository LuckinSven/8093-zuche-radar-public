"""新增地图缓存和城市目录字段

Revision ID: 0008_add_map_cache_and_city_catalog
Revises: 0007_add_integration_settings
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0008_add_map_cache_and_city_catalog"
down_revision = "0007_add_integration_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 初始迁移将 Alembic 版本列限制为 32 个字符；本版本标识更长，必须先扩容，
    # 否则 Alembic 在本迁移完成后写入版本号时会失败并整体回滚。
    op.alter_column("alembic_version", "version_num", type_=sa.String(length=64), existing_type=sa.String(length=32))
    op.add_column("cities", sa.Column("code", sa.String(length=64), nullable=True))
    op.add_column("cities", sa.Column("en_name", sa.String(length=128), nullable=True))
    op.add_column("cities", sa.Column(
        "catalog_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("cities", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cities", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cities", sa.Column("catalog_synced_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "map_search_caches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="baidu_maps"),
        sa.Column("normalized_region", sa.String(length=256), nullable=False),
        sa.Column("normalized_keyword", sa.String(length=256), nullable=False),
        sa.Column("results_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "normalized_region", "normalized_keyword",
                            name="uq_map_search_cache_provider_region_keyword"),
    )
    op.create_table(
        "coordinate_caches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="baidu_maps"),
        sa.Column("source_crs", sa.String(length=32), nullable=False),
        sa.Column("target_crs", sa.String(length=32), nullable=False),
        sa.Column("source_latitude", sa.Numeric(precision=11, scale=8), nullable=False),
        sa.Column("source_longitude", sa.Numeric(precision=11, scale=8), nullable=False),
        sa.Column("target_latitude", sa.Numeric(precision=11, scale=8), nullable=False),
        sa.Column("target_longitude", sa.Numeric(precision=11, scale=8), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "source_crs", "target_crs", "source_latitude", "source_longitude",
                            name="uq_coordinate_cache_provider_crs_source"),
    )


def downgrade() -> None:
    op.drop_table("coordinate_caches")
    op.drop_table("map_search_caches")
    op.drop_column("cities", "catalog_synced_at")
    op.drop_column("cities", "last_seen_at")
    op.drop_column("cities", "first_seen_at")
    op.drop_column("cities", "catalog_active")
    op.drop_column("cities", "en_name")
    op.drop_column("cities", "code")
