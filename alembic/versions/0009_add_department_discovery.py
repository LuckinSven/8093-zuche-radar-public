"""新增网点目录字段和可恢复的发现任务。

Revision ID: 0009_add_department_discovery
Revises: 0008_add_map_cache_and_city_catalog
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0009_add_department_discovery"
down_revision = "0008_add_map_cache_and_city_catalog"
branch_labels = None
depends_on = None


department_activity_state = postgresql.ENUM(
    "DISCOVERED", "RECENTLY_SEEN", "LONG_UNSEEN", "MANUALLY_DISABLED",
    name="departmentactivitystate", create_type=False,
)
discovery_status = postgresql.ENUM(
    "PENDING", "RUNNING", "STOPPED", "INTERRUPTED", "COMPLETED_LIMIT", "COMPLETED_NO_NEW", "FAILED",
    name="departmentdiscoverystatus", create_type=False,
)
point_status = postgresql.ENUM(
    "PENDING", "RUNNING", "COMPLETED", "FAILED", name="departmentdiscoverypointstatus", create_type=False)


def upgrade() -> None:
    # add_column 不会为 PostgreSQL 原生枚举自动建类型，须在引用前显式创建。
    bind = op.get_bind()
    department_activity_state.create(bind, checkfirst=True)
    discovery_status.create(bind, checkfirst=True)
    point_status.create(bind, checkfirst=True)

    # 旧 departments 可能来自历史扫描。先添加可空列、按扫描历史回填，再收紧可确定的字段，
    # 避免一次迁移在已有库上因新增非空列失败。zuche_dept_id 的既有全局唯一索引继续作为
    # 更强的稳定身份保证；city_id 仅记录最近可推断出的关联城市，无法推断时保留为空。
    op.add_column("departments", sa.Column("city_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_departments_city_id", "departments", "cities", ["city_id"], ["id"])
    op.create_index("ix_departments_city_id", "departments", ["city_id"])
    op.add_column("departments", sa.Column("district", sa.String(length=128), nullable=True))
    op.add_column("departments", sa.Column("business_hours", sa.String(length=255), nullable=True))
    op.add_column("departments", sa.Column("is_open_24h", sa.Boolean(), nullable=True))
    op.add_column("departments", sa.Column("self_service_pickup", sa.Boolean(), nullable=True))
    op.add_column("departments", sa.Column("self_service_return", sa.Boolean(), nullable=True))
    op.add_column("departments", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("departments", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("departments", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("departments", sa.Column("discovery_source", sa.String(length=32), nullable=True))
    op.add_column("departments", sa.Column("active_state", department_activity_state, nullable=True))

    op.execute("""
        UPDATE departments AS department
        SET city_id = history.city_id
        FROM (
            SELECT DISTINCT ON (snapshot.department_id)
                snapshot.department_id, city.id AS city_id
            FROM availability_snapshots AS snapshot
            JOIN scan_runs AS run ON run.id = snapshot.scan_run_id
            JOIN cities AS city ON city.zuche_city_id = run.zuche_city_id
            ORDER BY snapshot.department_id, run.completed_at DESC NULLS LAST, run.started_at DESC
        ) AS history
        WHERE department.id = history.department_id
    """)
    op.execute("""
        UPDATE departments AS department
        SET first_seen_at = COALESCE(history.first_seen_at, CURRENT_TIMESTAMP),
            last_seen_at = COALESCE(history.last_seen_at, CURRENT_TIMESTAMP),
            last_synced_at = COALESCE(history.last_seen_at, CURRENT_TIMESTAMP),
            discovery_source = 'LEGACY_SCAN',
            active_state = 'DISCOVERED'
        FROM (
            SELECT snapshot.department_id,
                   MIN(run.started_at) AS first_seen_at,
                   MAX(COALESCE(run.completed_at, run.started_at)) AS last_seen_at
            FROM availability_snapshots AS snapshot
            JOIN scan_runs AS run ON run.id = snapshot.scan_run_id
            GROUP BY snapshot.department_id
        ) AS history
        WHERE department.id = history.department_id
    """)
    op.execute("""
        UPDATE departments
        SET first_seen_at = COALESCE(first_seen_at, CURRENT_TIMESTAMP),
            last_seen_at = COALESCE(last_seen_at, CURRENT_TIMESTAMP),
            last_synced_at = COALESCE(last_synced_at, CURRENT_TIMESTAMP),
            discovery_source = COALESCE(discovery_source, 'LEGACY_SCAN'),
            active_state = COALESCE(active_state, 'DISCOVERED')
    """)
    for column in ("first_seen_at", "last_seen_at", "last_synced_at", "discovery_source", "active_state"):
        op.alter_column("departments", column, nullable=False)

    op.create_table(
        "department_discovery_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("city_id", sa.Integer(), sa.ForeignKey("cities.id"), nullable=False),
        sa.Column("preset", sa.String(length=32), nullable=False),
        sa.Column("radius_km", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("spacing_km", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("max_requests", sa.Integer(), nullable=False),
        sa.Column("pickup_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("return_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", discovery_status, nullable=False, server_default="PENDING"),
        sa.Column("planned_point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_point_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_summary", sa.Text(), nullable=True),
        sa.CheckConstraint("radius_km > 0", name="ck_department_discovery_run_radius_positive"),
        sa.CheckConstraint("spacing_km > 0", name="ck_department_discovery_run_spacing_positive"),
        sa.CheckConstraint("max_requests > 0", name="ck_department_discovery_run_max_requests_positive"),
        sa.CheckConstraint("pickup_time < return_time", name="ck_department_discovery_run_valid_period"),
        sa.CheckConstraint(
            "planned_point_count >= 0 AND completed_point_count >= 0 AND request_count >= 0 "
            "AND new_department_count >= 0 AND updated_department_count >= 0",
            name="ck_department_discovery_run_counts_nonnegative",
        ),
    )
    op.create_index("ix_department_discovery_runs_city_id", "department_discovery_runs", ["city_id"])
    # 数据库兜底与仓储事务锁同时存在：PostgreSQL 并发创建同城任务不会产生两个活动任务。
    op.create_index(
        "uq_department_discovery_active_city",
        "department_discovery_runs",
        ["city_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )

    op.create_table(
        "department_discovery_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("department_discovery_runs.id"), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", point_status, nullable=False, server_default="PENDING"),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_department_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.UniqueConstraint("run_id", "latitude", "longitude", name="uq_department_discovery_point_coordinate"),
        sa.CheckConstraint("round_number >= 1", name="ck_department_discovery_point_round_positive"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND response_department_count >= 0 AND new_department_count >= 0",
            name="ck_department_discovery_point_counts_nonnegative",
        ),
    )
    op.create_index("ix_department_discovery_points_run_id", "department_discovery_points", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_department_discovery_points_run_id", table_name="department_discovery_points")
    op.drop_table("department_discovery_points")
    op.drop_index("uq_department_discovery_active_city", table_name="department_discovery_runs")
    op.drop_index("ix_department_discovery_runs_city_id", table_name="department_discovery_runs")
    op.drop_table("department_discovery_runs")

    op.drop_column("departments", "active_state")
    op.drop_column("departments", "discovery_source")
    op.drop_column("departments", "last_synced_at")
    op.drop_column("departments", "last_seen_at")
    op.drop_column("departments", "first_seen_at")
    op.drop_column("departments", "self_service_return")
    op.drop_column("departments", "self_service_pickup")
    op.drop_column("departments", "is_open_24h")
    op.drop_column("departments", "business_hours")
    op.drop_column("departments", "district")
    op.drop_index("ix_departments_city_id", table_name="departments")
    op.drop_constraint("fk_departments_city_id", "departments", type_="foreignkey")
    op.drop_column("departments", "city_id")

    point_status.drop(op.get_bind(), checkfirst=True)
    discovery_status.drop(op.get_bind(), checkfirst=True)
    department_activity_state.drop(op.get_bind(), checkfirst=True)
