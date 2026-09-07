# 广州全城车型扫描与永久车型库实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current conversation. Do not create subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不更换前端技术栈的前提下，实现按明确租期手动遍历广州全部网点、永久积累车型、按车型聚合可取网点与价格，并提供流畅清新的全城车型和车型库页面。

**Architecture:** 使用 PostgreSQL 保存可停止和恢复的全城扫描任务，每个网点是一个独立扫描点；后台调度器每轮最多并发处理 3 个点，网络等待期间不持有数据库会话。扫描结果按任务、车型和可取网点原子去重，PostgreSQL 负责聚合、筛选和分页，原生 JavaScript 只局部更新当前页面并按需加载网点报价。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLAlchemy 2、PostgreSQL 16、Alembic、APScheduler、Jinja2、原生 JavaScript、CSS、pytest、Docker Compose

**Spec:** `docs/superpowers/specs/2026-09-02-guangzhou-citywide-model-library-design.md`

## Global Constraints

- 所有用户可见文案和新增文档使用中文。
- 保持 FastAPI、Jinja、原生 JavaScript 和 CSS；不引入 Vue、React、Node 构建链或前端包管理器。
- 全城扫描只允许手动触发，首版并发固定为 3。
- 默认租期为北京时间 09:00 到次日 09:00，租期最长 30 天。
- 距鱼珠距离使用鱼珠地铁站服务点坐标 `23.101610, 113.432649` 计算直线距离。
- 车型、人工状态和精简历史永久保留；详细报价和原始响应保留 60 天。
- 不实现 Cookie、登录、下单、支付、自动全城扫描或百度驾车距离批量计算。
- 任何网络 `await` 都不能持有 SQLAlchemy 会话。
- 所有写接口拒绝额外字段，所有上游和数据库错误对用户脱敏。
- 每个任务遵循测试驱动：先运行新增测试并确认预期失败，再写生产代码。
- 每个任务只暂存计划列出的文件，禁止使用 `git add .`、`git add -A` 或 `git add --all`。

---

## 文件结构

### 新增后端文件

- `app/citywide/domain.py`：全城扫描不可变领取快照和内部结果类型。
- `app/citywide/repository.py`：任务状态机、点位领取、报价原子去重和汇总写入。
- `app/citywide/service.py`：短事务领取、匿名外呼、并发控制和安全落库。
- `app/citywide/catalog.py`：全城车型与永久车型库的数据库侧聚合查询。
- `app/api/citywide.py`：全城扫描、车型聚合和车型库 API。
- `app/static/citywide.js`：首页任务进度、全城车型和车型库的局部交互。
- `app/static/citywide.css`：三类核心页面布局、响应式和轻量动画。
- `app/static/theme-refresh.css`：现有页面共用的清新配色覆盖层。
- `app/templates/citywide_models.html`：指定租期的全城车型页。
- `app/templates/model_library.html`：永久车型库页。
- `alembic/versions/0013_add_citywide_model_library.py`：新表、索引、枚举和车型字段迁移。

### 修改后端文件

- `app/models.py`：声明全城任务、点位、报价、汇总和原始响应模型，扩展车型字段。
- `app/maintenance.py`：分批清理 60 天详细报价和原始响应。
- `app/scheduling.py`：注册全城扫描处理和维护作业。
- `app/main.py`：向应用状态发布全城扫描服务。
- `app/api/router.py`：注册全城 API。
- `app/api_catalog.py`：为新增接口提供中文摘要、说明和调用示例。
- `app/web.py`：注册全城车型和车型库页面。
- `app/templates/base.html`：调整导航并支持页面专用 CSS/JS 块。
- `app/templates/discovery.html`：加入全城任务配置和进度，保留现有鱼珠快速扫描入口。
- `app/templates/data_table.html`：改为高级报价明细并移除内部双滚动。
- `app/static/app.css`：删除与新主题冲突的固定宽表格规则。

### 新增测试文件

- `tests/test_citywide_models.py`：模型约束和迁移等价结构。
- `tests/test_citywide_repository.py`：状态机、并发领取、去重优先级和汇总。
- `tests/test_citywide_service.py`：网络事务边界、并发、重试和安全错误。
- `tests/test_citywide_api.py`：严格 API 契约、任务控制和错误翻译。
- `tests/test_citywide_catalog.py`：数据库聚合、三态、筛选、分页和按需报价。
- `tests/test_citywide_maintenance.py`：60 天分层保留。
- `tests/test_citywide_web.py`：页面结构、导航和前端交互契约。

---

### Task 1: 数据库模型与 Alembic 迁移

**Files:**
- Modify: `app/models.py`
- Create: `alembic/versions/0013_add_citywide_model_library.py`
- Create: `tests/test_citywide_models.py`

**Interfaces:**
- Produces: `CitywideScanStatus`、`CitywidePointStatus`、`CitywideScanRun`、`CitywideScanPoint`、`CitywideOffer`、`CitywideModelSummary`、`CitywideRawPayload`。
- Produces: `VehicleModel.first_seen_at`、`last_seen_at`、`latest_description`、`image_url`、`energy_type`、`energy_source`。
- Consumes: 现有 `City`、`Department`、`VehicleModel` 外键和 PostgreSQL UUID/JSONB 支持。

- [ ] **Step 1: 写模型约束失败测试**

在 `tests/test_citywide_models.py` 创建真实数据库测试，验证活动任务唯一、租期有效、点位唯一、报价唯一和汇总唯一：

```python
def test_citywide_schema_enforces_one_active_run_and_unique_offer(session, city, department, vehicle_model):
    first = CitywideScanRun(city_id=city.id, pickup_time=PICKUP, return_time=RETURN,
                            status=CitywideScanStatus.RUNNING)
    session.add(first)
    session.flush()
    session.add(CitywideScanRun(city_id=city.id, pickup_time=PICKUP, return_time=RETURN,
                                status=CitywideScanStatus.PENDING))
    with pytest.raises(IntegrityError):
        session.flush()

def test_vehicle_model_keeps_permanent_catalog_fields(session):
    model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05",
                         first_seen_at=SEEN, last_seen_at=SEEN,
                         energy_type="新能源", energy_source="UPSTREAM")
    session.add(model)
    session.flush()
    assert model.first_seen_at == SEEN
```

- [ ] **Step 2: 运行模型测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_models.py -v`

Expected: collection FAIL，提示 `CitywideScanRun` 等类型尚不存在。

- [ ] **Step 3: 声明枚举、表和约束**

在 `app/models.py` 增加以下类型和等价字段：

```python
class CitywideScanStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"

class CitywidePointStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class CitywideScanRun(Base):
    __tablename__ = "citywide_scan_runs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    pickup_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    return_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[CitywideScanStatus] = mapped_column(Enum(CitywideScanStatus))
    planned_point_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_point_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_point_count: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    available_model_count: Mapped[int] = mapped_column(Integer, default=0)
    new_model_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
```

表级约束必须包含 `pickup_time < return_time`、所有计数非负、同一城市仅允许一个 `PENDING/RUNNING` 任务的 PostgreSQL/SQLite 部分唯一索引。点位唯一键为 `(run_id, department_id)`，报价唯一键为 `(run_id, vehicle_model_id, department_id)`，汇总唯一键为 `(run_id, vehicle_model_id)`。

- [ ] **Step 4: 编写可升级和可回退迁移**

创建 `0013_add_citywide_model_library.py`，设置：

```python
revision = "0013_add_citywide_model_library"
down_revision = "0012_fix_department_distance_units"
```

迁移必须创建所有新表和索引，为 `vehicle_models` 增加可空扩展列，并用已有 `scan_runs.started_at` 回填 `first_seen_at/last_seen_at`；`downgrade()` 按外键逆序删除新表、索引、列和枚举。

- [ ] **Step 5: 验证 ORM 建库和历史迁移兼容性**

保持 `app/migration_schema_v1.py` 为冻结的 v1 历史结构，避免旧库从头升级时提前创建第 13 版表。测试环境由当前 ORM 元数据建库；真实升级只由 `0013_add_citywide_model_library.py` 负责。然后运行：

Run: `.venv312/bin/python -m pytest tests/test_citywide_models.py tests/test_rebuild_acceptance.py -v`

Expected: PASS。

- [ ] **Step 6: 提交模型任务**

```bash
git add app/models.py alembic/versions/0013_add_citywide_model_library.py tests/test_citywide_models.py docs/superpowers/plans/2026-09-02-guangzhou-citywide-model-library.md
git commit -m "feat: 增加全城扫描数据模型"
```

---

### Task 2: 任务状态机、并发领取与报价去重仓储

**Files:**
- Create: `app/citywide/__init__.py`
- Create: `app/citywide/domain.py`
- Create: `app/citywide/repository.py`
- Create: `tests/test_citywide_repository.py`

**Interfaces:**
- Consumes: Task 1 的模型和 `app.departments.planner.haversine_km`。
- Produces: `CitywidePointClaim`、`CitywideClaimBatch`。
- Produces: `CitywideRepository.create_run()`、`claim_points()`、`complete_point()`、`fail_point()`、`stop()`、`resume()`、`retry_failed()`、`interrupt_stale_runs()`。

- [ ] **Step 1: 写状态机和领取失败测试**

测试必须覆盖固定网点快照、最多领取 3 点、领取令牌、防重复领取和非法状态转换：

```python
def test_create_run_snapshots_only_guangzhou_departments_with_coordinates(session, guangzhou, departments):
    run = CitywideRepository(session).create_run(
        city_id=guangzhou.id, pickup_time=PICKUP, return_time=RETURN)
    assert run.planned_point_count == 91
    assert session.scalar(select(func.count()).select_from(CitywideScanPoint)
                          .where(CitywideScanPoint.run_id == run.id)) == 91

def test_claim_points_never_exceeds_three_and_uses_claim_tokens(session, run):
    batch = CitywideRepository(session).claim_points(run.id, limit=3)
    assert len(batch.claims) == 3
    assert len({claim.claim_token for claim in batch.claims}) == 3
```

- [ ] **Step 2: 运行仓储测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_repository.py -v`

Expected: collection FAIL，提示 `app.citywide.repository` 不存在。

- [ ] **Step 3: 定义不可变领取类型**

在 `app/citywide/domain.py` 定义：

```python
@dataclass(frozen=True, slots=True)
class CitywidePointClaim:
    run_id: UUID
    point_id: int
    claim_token: UUID
    city_id: int
    zuche_city_id: str
    anchor_department_id: int
    anchor_zuche_dept_id: int
    anchor_name: str
    latitude: float
    longitude: float
    pickup_time: datetime
    return_time: datetime

@dataclass(frozen=True, slots=True)
class CitywideClaimBatch:
    state: str
    claims: tuple[CitywidePointClaim, ...] = ()
```

- [ ] **Step 4: 实现状态机和原子领取**

`CitywideRepository.claim_points(run_id, limit=3)` 必须锁定任务，使用 `FOR UPDATE SKIP LOCKED` 领取点，并在提交前增加 `request_count` 和点位 `attempt_count`。SQLite 测试环境使用确定性串行分支。

状态转换规则：

```python
ALLOWED = {
    "stop": {CitywideScanStatus.RUNNING: CitywideScanStatus.STOPPED},
    "resume": {CitywideScanStatus.STOPPED: CitywideScanStatus.RUNNING,
               CitywideScanStatus.INTERRUPTED: CitywideScanStatus.RUNNING},
    "retry_failed": {CitywideScanStatus.PARTIAL: CitywideScanStatus.RUNNING},
}
```

启动恢复时将遗留 `PENDING/RUNNING` 任务标记为 `INTERRUPTED`，不自动继续。

- [ ] **Step 5: 写报价去重优先级失败测试**

```python
def test_self_anchor_offer_wins_over_nearby_duplicate(session, run, model, returned_department):
    repo = CitywideRepository(session)
    repo.upsert_offer(run.id, model.id, returned_department.id,
                      source_point_id=nearby_point.id, source_is_self=False,
                      source_distance_km=2.4, package_price=118)
    repo.upsert_offer(run.id, model.id, returned_department.id,
                      source_point_id=self_point.id, source_is_self=True,
                      source_distance_km=0, package_price=128)
    offer = session.scalar(select(CitywideOffer))
    assert offer.package_price == 128
    assert offer.source_is_self is True
```

- [ ] **Step 6: 实现 PostgreSQL 原子去重和受影响车型汇总**

使用 PostgreSQL 方言的 `insert()` 配合 `on_conflict_do_update()` 和条件更新，更新条件为新观察 `source_is_self` 更优，或双方均非自身观察且新 `source_distance_km` 更小。每个成功点只重新计算本点涉及的车型汇总：唯一网点数、`avg/min/max` 价格、最近鱼珠距离和最近网点。

- [ ] **Step 7: 运行仓储测试并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_repository.py tests/test_department_repository.py -v`

Expected: PASS。

```bash
git add app/citywide/__init__.py app/citywide/domain.py app/citywide/repository.py tests/test_citywide_repository.py
git commit -m "feat: 实现全城扫描状态机"
```

---

### Task 3: 全城扫描服务与永久车型更新

**Files:**
- Create: `app/citywide/service.py`
- Create: `tests/test_citywide_service.py`
- Modify: `app/zuche/parser.py`
- Modify: `app/domain.py`
- Modify: `tests/test_rebuild_zuche_adapter.py`

**Interfaces:**
- Consumes: `CitywideRepository.claim_points()` 和现有 `ZucheClient.choose_car(ScanQuery)`。
- Produces: `CitywideScanService.create_run()`、`process_active_run()`、`stop()`、`resume()`、`retry_failed()`。
- Produces: 完整的分组目录字段 `ParsedGroup.group_low_price_desc`、`sort_num`、`model_name`、`model_low_price_desc`、`model_image_url`。

- [ ] **Step 1: 写网络事务边界和三并发失败测试**

```python
@pytest.mark.asyncio
async def test_process_active_run_closes_sessions_during_three_concurrent_requests(engine):
    service = CitywideScanService(tracking_factory, gateway_factory)
    result = await service.process_active_run()
    assert result.processed == 3
    assert max_gateway_concurrency == 3
    assert sessions_open_during_gateway == [0, 0, 0]
```

测试网关返回完整真实结构的 `deptHangModels` 和 `modelGroups`，不要只返回测试用最小字段。

- [ ] **Step 2: 运行服务测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_service.py -v`

Expected: collection FAIL，提示 `CitywideScanService` 不存在。

- [ ] **Step 3: 完整展开选车分组字段**

扩展 `ParsedGroup`：

```python
class ParsedGroup(BaseModel):
    model_id: int
    group_id: int
    group_name: str
    group_low_price_desc: str | None = None
    sort_num: int | None = None
    model_name: str | None = None
    model_low_price_desc: str | None = None
    model_image_url: str | None = None
```

`parse_choose_car()` 必须遍历全部 `modelGroups[].modelItems[]`，保存 `groupId/name/lowPriceDesc/sortNum` 和车型 `modelId/modelName/lowPriceDesc/modelImgUrl`。报价中的 `modelGroupId` 作为分组目录缺失时的补充关联，不覆盖完整目录。

- [ ] **Step 4: 实现短事务服务**

核心入口固定为下表，实施时不得改名或改变同步/异步边界：

| 方法 | 参数 | 返回值 | 职责 |
|---|---|---|---|
| `create_run` | `city_id: int, pickup_time: datetime, return_time: datetime` | `CitywideScanRun` | 校验租期并为广州全部启用网点创建扫描点 |
| `process_active_run` | 无 | `Awaitable[CitywideProcessResult]` | 领取最多 3 个点、并发外呼、短事务保存 |
| `stop` | `run_id: UUID` | `CitywideScanRun` | 将运行中任务切换为已停止 |
| `resume` | `run_id: UUID` | `CitywideScanRun` | 继续已停止或已中断任务 |
| `retry_failed` | `run_id: UUID` | `CitywideScanRun` | 重排部分完成任务中的失败点 |

`process_active_run()` 短事务领取最多 3 个点，关闭会话后用 `asyncio.gather()` 外呼，再逐个用短事务落库。每次原始响应 gzip 压缩写入 `CitywideRawPayload`。

- [ ] **Step 5: 更新永久车型但保护人工信息**

扫描首次遇到车型时设置 `first_seen_at/last_seen_at`；再次遇到只更新 `last_seen_at`、名称和非空上游字段。能源来源为人工注解时，不写回 `VehicleModel.energy_type`。图片和描述缺失时保留已有值。

- [ ] **Step 6: 实现重试和安全错误**

网络超时、连接失败和 HTTP 5xx 由点位 `attempt_count < 3` 控制重排；业务错误、解析错误和第三次失败进入 `FAILED`。用户错误摘要限定为“神州匿名接口网络请求失败”“神州匿名接口响应格式错误”或“扫描结果保存失败”。

- [ ] **Step 7: 运行服务与解析测试并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_service.py tests/test_rebuild_zuche_adapter.py tests/test_rebuild_scan_service.py -v`

Expected: PASS。

```bash
git add app/citywide/service.py app/domain.py app/zuche/parser.py tests/test_citywide_service.py tests/test_rebuild_zuche_adapter.py
git commit -m "feat: 实现广州全城扫描服务"
```

---

### Task 4: 严格 API、运行时调度与任务恢复

**Files:**
- Create: `app/api/citywide.py`
- Create: `tests/test_citywide_api.py`
- Modify: `app/api/router.py`
- Modify: `app/main.py`
- Modify: `app/scheduling.py`
- Modify: `tests/test_rebuild_scheduling_maintenance.py`

**Interfaces:**
- Consumes: Task 3 的 `CitywideScanService`。
- Produces: `/api/citywide-scans` 任务生命周期接口。
- Produces: `app.state.citywide_scan_service`。

- [ ] **Step 1: 写 API 契约失败测试**

```python
class CreateCitywideScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city_id: Annotated[int, Field(gt=0, strict=True)]
    pickup_time: datetime
    return_time: datetime

@pytest.mark.asyncio
async def test_create_citywide_scan_validates_period_and_returns_progress(engine):
    response = await client.post("/api/citywide-scans", json={
        "city_id": city_id,
        "pickup_time": "2026-09-06T09:00:00+08:00",
        "return_time": "2026-09-07T09:00:00+08:00",
    })
    assert response.status_code == 201
    assert response.json()["planned_point_count"] == 91
```

同时测试额外字段、布尔城市 ID、过去时间、超过 30 天租期、重复活动任务、非法停止/继续和内部错误脱敏。

- [ ] **Step 2: 运行 API 测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_api.py -v`

Expected: 404 或 import FAIL。

- [ ] **Step 3: 实现任务 API 和中文序列化**

在 `app/api/citywide.py` 注册以下固定契约：

| 方法和路径 | 处理函数 | 成功状态 | 返回内容 |
|---|---|---:|---|
| `POST /citywide-scans` | `create_citywide_scan` | 201 | 新任务及初始进度 |
| `GET /citywide-scans` | `list_citywide_scans` | 200 | 分页任务历史 |
| `GET /citywide-scans/{run_id}` | `get_citywide_scan` | 200 | 单任务进度和错误统计 |
| `POST /citywide-scans/{run_id}/stop` | `stop_citywide_scan` | 200 | 已停止任务 |
| `POST /citywide-scans/{run_id}/resume` | `resume_citywide_scan` | 200 | 已恢复任务 |
| `POST /citywide-scans/{run_id}/retry-failed` | `retry_failed_citywide_scan` | 200 | 已重排任务 |

所有处理函数从 `request.app.state.citywide_scan_service` 取服务；领域校验错误统一映射为 409 或 422，未知异常记录内部日志后只返回中文通用错误。

- [ ] **Step 4: 注册单实例后台处理作业**

`RadarRuntime` 构造 `CitywideScanService`，启动时中断遗留任务，并注册：

```python
self.scheduler.add_job(
    self._run_citywide_scan,
    IntervalTrigger(seconds=2, jitter=1),
    id="citywide-scan",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
```

同步包装器只执行 `asyncio.run(self.citywide_scan_service.process_active_run())`。

- [ ] **Step 5: 运行 API 和调度测试并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_api.py tests/test_rebuild_scheduling_maintenance.py -v`

Expected: PASS。

```bash
git add app/api/citywide.py app/api/router.py app/main.py app/scheduling.py tests/test_citywide_api.py tests/test_rebuild_scheduling_maintenance.py
git commit -m "feat: 增加全城扫描任务接口"
```

---

### Task 5: PostgreSQL 车型聚合、三态和按需网点报价

**Files:**
- Create: `app/citywide/catalog.py`
- Create: `tests/test_citywide_catalog.py`
- Modify: `app/api/citywide.py`

**Interfaces:**
- Consumes: `CitywideModelSummary`、`CitywideOffer`、`VehicleModel`、`PersonalVehicleState`。
- Produces: `CitywideModelFilters`、`ModelLibraryFilters`、`CitywideCatalog.search()`、`library()`、`offers()`、`detail()`。
- Produces: `/api/citywide-models`、`/api/citywide-models/{model_id}/offers`、`/api/model-library`、`/api/model-library/{model_id}`。

- [ ] **Step 1: 写聚合和三态失败测试**

```python
def test_citywide_catalog_averages_unique_departments_and_computes_fish_distance(session, completed_run):
    result = CitywideCatalog(session).search(CitywideModelFilters(run_id=completed_run.id))
    sea_lion = next(item for item in result["items"] if item["model_name"] == "比亚迪海狮05")
    assert sea_lion["department_count"] == 7
    assert sea_lion["average_price"] == 134.29
    assert sea_lion["minimum_price"] == 128
    assert sea_lion["maximum_price"] == 148
    assert sea_lion["nearest_fish_distance_km"] == 11.7

def test_unseen_model_status_depends_on_run_completeness(session, permanent_model):
    assert status_for(completed_run, permanent_model) == "NOT_FOUND"
    assert status_for(partial_run, permanent_model) == "INCOMPLETE"
```

- [ ] **Step 2: 运行目录测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_catalog.py -v`

Expected: collection FAIL，提示 `CitywideCatalog` 不存在。

- [ ] **Step 3: 实现数据库侧筛选与分页**

定义严格查询模型：

```python
class CitywideModelFilters(BaseModel):
    run_id: UUID
    q: str = Field(default="", max_length=100)
    department_id: int | None = Field(default=None, gt=0)
    max_fish_distance_km: float | None = Field(default=None, ge=0)
    availability: Literal["AVAILABLE", "NOT_FOUND", "INCOMPLETE"] | None = None
    first_seen_from: date | None = None
    last_seen_from: date | None = None
    energy_type: str = Field(default="", max_length=64)
    personal_state: PersonalState | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
```

使用 SQL `LEFT JOIN` 汇总和个人状态，使用 `EXISTS` 筛选网点，使用 `COUNT(*) OVER()` 或独立数据库计数返回分页总数。禁止调用 `.all()` 后在 Python 中筛选或分页。

- [ ] **Step 4: 实现按需报价和永久车型库**

```python
def offers(self, run_id: UUID, zuche_model_id: int) -> list[dict]:
    """按 fish_distance_km、网点名称排序，只读取该车型报价。"""

def library(self, filters: ModelLibraryFilters) -> dict:
    """从 VehicleModel 开始查询，永远不因当前任务缺失删除车型。"""

def detail(self, zuche_model_id: int, run_id: UUID | None) -> dict:
    """返回永久档案、人工状态、精简历史和当前任务状态。"""
```

- [ ] **Step 5: 注册读取 API 并验证 OpenAPI 强类型参数**

新增 GET 路由，404 统一为“车型不存在”或“全城扫描任务不存在”，数据库错误统一为“车型数据读取失败，请稍后重试”。

- [ ] **Step 6: 运行目录和 API 测试并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_catalog.py tests/test_citywide_api.py -v`

Expected: PASS。

```bash
git add app/citywide/catalog.py app/api/citywide.py tests/test_citywide_catalog.py tests/test_citywide_api.py
git commit -m "feat: 增加全城车型聚合查询"
```

---

### Task 6: 60 天详细数据清理

**Files:**
- Modify: `app/maintenance.py`
- Modify: `app/scheduling.py`
- Create: `tests/test_citywide_maintenance.py`
- Modify: `tests/test_rebuild_scheduling_maintenance.py`

**Interfaces:**
- Consumes: `CitywideOffer.observed_at`、`CitywideRawPayload.captured_at`。
- Produces: `CitywideDetailMaintenance.purge(cutoff, batch_size=1000) -> dict[str, int]`。

- [ ] **Step 1: 写分层保留失败测试**

```python
def test_citywide_maintenance_deletes_old_details_but_keeps_models_and_summaries(session):
    result = CitywideDetailMaintenance(session).purge(CUTOFF, batch_size=1000)
    assert result == {"offers": 1, "raw_payloads": 1}
    assert session.get(VehicleModel, model_id) is not None
    assert session.get(CitywideModelSummary, summary_id) is not None
    assert session.get(PersonalVehicleState, model_id) is not None
```

- [ ] **Step 2: 运行维护测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_maintenance.py -v`

Expected: import FAIL。

- [ ] **Step 3: 实现分批清理**

每次先查询最多 `batch_size` 个过期 ID，再按 ID 删除，循环到没有记录。禁止删除 `CitywideScanRun`、`CitywideModelSummary`、`VehicleModel`、人工状态或注解。

- [ ] **Step 4: 接入现有每日维护作业**

`RadarRuntime._purge_raw_payloads()` 在同一维护窗口依次执行现有 `RawPayloadMaintenance` 和 `CitywideDetailMaintenance`，每个批次提交，日志只输出删除数量。

- [ ] **Step 5: 运行维护测试并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_maintenance.py tests/test_rebuild_scheduling_maintenance.py -v`

Expected: PASS。

```bash
git add app/maintenance.py app/scheduling.py tests/test_citywide_maintenance.py tests/test_rebuild_scheduling_maintenance.py
git commit -m "feat: 清理过期全城报价明细"
```

---

### Task 7: 找车首页、全城车型和车型库页面骨架

**Files:**
- Modify: `app/web.py`
- Modify: `app/templates/base.html`
- Modify: `app/templates/discovery.html`
- Create: `app/templates/citywide_models.html`
- Create: `app/templates/model_library.html`
- Modify: `app/templates/model_detail.html`
- Modify: `app/templates/data_table.html`
- Create: `tests/test_citywide_web.py`
- Modify: `tests/test_rebuild_web.py`

**Interfaces:**
- Consumes: Task 4 和 Task 5 的 API。
- Produces: `/citywide`、`/library` 页面和现有 `/models/{model_id}` 扩展视图。

- [ ] **Step 1: 写页面结构失败测试**

```python
@pytest.mark.asyncio
async def test_citywide_navigation_and_pages_expose_accessible_controls():
    home = await client.get("/")
    citywide = await client.get("/citywide")
    library = await client.get("/library")
    assert 'id="citywide-scan-form"' in home.text
    assert 'id="citywide-scan-progress"' in home.text
    assert 'id="citywide-model-filters"' in citywide.text
    assert 'id="citywide-model-list"' in citywide.text
    assert 'id="model-library-filters"' in library.text
    assert 'href="/data"' in citywide.text
```

同时断言主导航包含“找车、全城车型、车型库、网点、设置”，不把“报价明细”放入主导航。

- [ ] **Step 2: 运行页面测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_web.py -v`

Expected: 404 或缺少元素 FAIL。

- [ ] **Step 3: 注册页面路由和专用资源块**

`base.html` 增加：

在 `<head>` 结束前增加 `{% block page_styles %}{% endblock %}`，在 `</body>` 前增加 `{% block page_scripts %}{% endblock %}`；子模板分别通过这两个块加载 `citywide.css` 和 `citywide.js`。

`/citywide` 和 `/library` 模板只输出语义结构、加载状态和空状态，不在模板内嵌 JSON 或上游内容。

- [ ] **Step 4: 重组首页但保留鱼珠快速扫描**

首页顶层为全城扫描表单和最近任务；现有 `manual-scan-form` 放入默认收起的 `<details>`，标题为“鱼珠附近快速扫描”，确保旧接口和测试继续可用。

- [ ] **Step 5: 扩展车型详情和高级明细入口**

车型详情增加首次/最近发现、当前租期三态、精简历史和当前网点报价容器。`/data` 标题改为“报价明细”，保留 CSV 和旧查询参数。

- [ ] **Step 6: 运行页面回归并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_web.py tests/test_rebuild_web.py tests/test_web_pages.py -v`

Expected: PASS。

```bash
git add app/web.py app/templates/base.html app/templates/discovery.html app/templates/citywide_models.html app/templates/model_library.html app/templates/model_detail.html app/templates/data_table.html tests/test_citywide_web.py tests/test_rebuild_web.py
git commit -m "feat: 增加全城车型与车型库页面"
```

---

### Task 8: 原生 JavaScript 局部更新、进度和按需展开

**Files:**
- Create: `app/static/citywide.js`
- Modify: `app/templates/discovery.html`
- Modify: `app/templates/citywide_models.html`
- Modify: `app/templates/model_library.html`
- Modify: `app/templates/model_detail.html`
- Modify: `tests/test_citywide_web.py`

**Interfaces:**
- Consumes: `/api/citywide-scans`、`/api/citywide-models`、`/offers` 和 `/api/model-library`。
- Produces: `initCitywideScanPage()`、`initCitywideModelsPage()`、`initModelLibraryPage()`、`loadModelOffers()`。

- [ ] **Step 1: 写前端行为契约失败测试**

在 `tests/test_citywide_web.py` 读取实际页面和脚本，断言页面专用脚本、请求取消、可见性暂停、按需报价和无整页轮询：

```python
def test_citywide_script_cancels_stale_filters_and_lazy_loads_offers():
    script = Path("app/static/citywide.js").read_text(encoding="utf-8")
    assert "new AbortController()" in script
    assert "controller.abort()" in script
    assert "/offers" in script
    assert "visibilitychange" in script
    assert "innerHTML=payload.items.map" not in script
```

- [ ] **Step 2: 运行前端契约测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_web.py::test_citywide_script_cancels_stale_filters_and_lazy_loads_offers -v`

Expected: FAIL，因为文件不存在。

- [ ] **Step 3: 实现首页任务控制和轻量进度轮询**

`initCitywideScanPage()` 创建任务后仅更新进度区。状态为 `RUNNING/PENDING` 时每 2 秒读取单个任务；页面隐藏时取消计时器，恢复可见时立即刷新。停止、继续、重试按钮在请求期间禁用。

- [ ] **Step 4: 实现车型列表的局部替换和请求取消**

筛选状态保存在 `URLSearchParams`。每次筛选先取消上一请求，只替换 `#citywide-model-list` 的行节点和分页节点；租期标题、筛选控件和已展开车型状态不重建。

禁止一次返回或渲染全部网点报价。

- [ ] **Step 5: 实现报价按需加载和缓存**

```javascript
const offerCache=new Map();
async function loadModelOffers(runId,modelId){
  const key=`${runId}:${modelId}`;
  if(!offerCache.has(key)) offerCache.set(key,api(`/api/citywide-models/${modelId}/offers?run_id=${encodeURIComponent(runId)}`));
  return offerCache.get(key);
}
```

点击车型行后才请求报价；再次展开复用缓存。失败时只在该车型详情区域显示错误。

- [ ] **Step 6: 实现车型库筛选和详情状态**

车型库默认按最近发现倒序，搜索、能源和个人标签使用相同请求取消机制。浏览器返回时从 URL 恢复筛选和页码。

- [ ] **Step 7: 运行页面测试并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_web.py tests/test_rebuild_web.py -v`

Expected: PASS。

```bash
git add app/static/citywide.js app/templates/discovery.html app/templates/citywide_models.html app/templates/model_library.html app/templates/model_detail.html tests/test_citywide_web.py
git commit -m "feat: 优化全城车型页面交互"
```

---

### Task 9: 清新配色、响应式布局与报价明细性能样式

**Files:**
- Create: `app/static/citywide.css`
- Create: `app/static/theme-refresh.css`
- Modify: `app/static/app.css`
- Modify: `app/templates/base.html`
- Modify: `app/templates/citywide_models.html`
- Modify: `app/templates/model_library.html`
- Modify: `app/templates/data_table.html`
- Modify: `tests/test_citywide_web.py`
- Modify: `tests/test_rebuild_web.py`

**Interfaces:**
- Consumes: Task 7 页面结构和 Task 8 状态类名。
- Produces: 无前端框架的轻磨砂主题、车型行展开过渡和窄屏三列布局。

- [ ] **Step 1: 写响应式和降级失败测试**

```python
def test_citywide_styles_are_lightweight_responsive_and_reduce_motion_safe():
    css = Path("app/static/citywide.css").read_text(encoding="utf-8")
    theme = Path("app/static/theme-refresh.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@supports (backdrop-filter:" in theme
    assert "min-width:1480px" not in css
    assert "100vh" not in css
```

- [ ] **Step 2: 运行样式测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_citywide_web.py::test_citywide_styles_are_lightweight_responsive_and_reduce_motion_safe -v`

Expected: FAIL，因为样式文件不存在。

- [ ] **Step 3: 实现主题颜色和轻磨砂降级**

`theme-refresh.css` 定义浅米白到浅灰绿静态背景、低饱和青绿强调、暖橙价格、浅边框和弱阴影。仅顶部导航、筛选栏和扫描状态在 `@supports` 中启用轻度 `backdrop-filter`；数据行保持接近不透明。

- [ ] **Step 4: 实现车型列表与展开过渡**

桌面使用五列聚合行；窄于 820px 隐藏次要日期列，窄于 600px 使用车型、可取范围、均价三列。详情使用 `grid-template-rows: 0fr` 到 `1fr` 的约 180ms 过渡，不动画模糊或阴影。

- [ ] **Step 5: 移除报价明细双滚动**

删除 `.data-sheet` 的视口高度限制和 `min-width:1480px`；桌面允许自然页面滚动，窄屏将次要列折叠到可展开详情，不缩小正文文本。

- [ ] **Step 6: 运行页面测试并提交**

Run: `.venv312/bin/python -m pytest tests/test_citywide_web.py tests/test_rebuild_web.py -v`

Expected: PASS。

```bash
git add app/static/citywide.css app/static/theme-refresh.css app/static/app.css app/templates/base.html app/templates/citywide_models.html app/templates/model_library.html app/templates/data_table.html tests/test_citywide_web.py tests/test_rebuild_web.py
git commit -m "style: 更新车型页面配色与布局"
```

---

### Task 10: API 台账、完整回归、真实扫描与 Docker 部署

**Files:**
- Modify: `app/api_catalog.py`
- Modify: `README.md`
- Modify: `tests/test_settings_api.py`
- Modify: `tests/test_rebuild_acceptance.py`

**Interfaces:**
- Consumes: 前九个任务的完整系统。
- Produces: 可在局域网使用、API 台账完整、GitHub 已推送的部署版本。

- [ ] **Step 1: 写 API 台账和验收失败测试**

```python
def test_api_catalog_documents_citywide_find_car_flow():
    catalog = build_api_catalog(create_app().openapi(), "http://服务器局域网IP:8093")
    paths = {(item["method"], item["path"]) for group in catalog["groups"] for item in group["items"]}
    assert ("POST", "/api/citywide-scans") in paths
    assert ("GET", "/api/citywide-models") in paths
    assert ("GET", "/api/model-library") in paths
```

- [ ] **Step 2: 运行台账测试并确认失败**

Run: `.venv312/bin/python -m pytest tests/test_settings_api.py::test_api_catalog_documents_citywide_find_car_flow -v`

Expected: FAIL，新增接口缺少中文说明或示例。

- [ ] **Step 3: 更新中文 API 台账和部署文档**

在 `app/api_catalog.py` 增加全城扫描、任务查询、车型聚合、报价和车型库的中文摘要与 `curl` 示例。`README.md` 增加全城扫描使用顺序、三态解释、60 天保留和“非最终结算价”说明。

- [ ] **Step 4: 运行完整自动化验证**

Run: `git diff --check && docker compose config -q && .venv312/bin/python -m pytest -q`

Expected: `git diff --check` 与 Compose 配置退出 0，pytest 0 failures。

- [ ] **Step 5: 构建并部署 Docker**

Run: `docker compose up -d --build app`

等待应用健康后运行：

```bash
docker compose ps
curl -fsS http://127.0.0.1:8093/healthz
curl -fsS http://127.0.0.1:8093/citywide
curl -fsS http://127.0.0.1:8093/library
```

Expected: 应用和数据库均为 `healthy`，健康检查返回 `status=ok`，两个页面返回 200。

- [ ] **Step 6: 执行一次广州真实全城扫描验收**

使用页面选择最近一个未来租期，手动创建任务。确认计划网点数等于当前广州目录数量，任务最终为 `COMPLETED` 或明确列出失败点；在全城车型页搜索“比亚迪海狮05”，核对唯一网点数量、均价、价格区间和距鱼珠最近距离。

如果上游暂时失败，不伪造成功结果；保留任务和安全错误，修复或重试后再完成验收。

- [ ] **Step 7: 最终状态检查、提交和推送**

```bash
git status --short --branch
git add app/api_catalog.py README.md tests/test_settings_api.py tests/test_rebuild_acceptance.py
git commit -m "docs: 完善全城车型使用说明"
git push -u origin HEAD
```

提交前确认工作树只包含本计划文件，任何不明用户改动都不暂存。最终报告局域网地址、分支、提交、测试数量、真实扫描点数和已知上游限制。
