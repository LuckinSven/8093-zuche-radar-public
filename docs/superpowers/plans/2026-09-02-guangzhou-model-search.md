# 广州按车型反向找车实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 在现有神州车型雷达中增加手动“按车型找车”任务，自动扫描未来四个周末的 24/48 小时租期和逐小时样本，按车型、租期、变体、网点展示可信结果。

**架构：** 新增独立 `app/model_search/` 模块，包含日期规划、短事务仓储、后台执行和结果聚合；继续复用 `ZucheClient`、`parse_choose_car()`、永久车型库和鱼珠距离计算。FastAPI 只负责创建、控制和读取任务，APScheduler 每两秒推进一个活动任务；前端只轮询轻量任务状态，结果按需分页展开。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Jinja2、原生 JavaScript/CSS、pytest、Docker Compose。

**规格：** `docs/superpowers/specs/2026-09-02-guangzhou-model-search-design.md`

## 全局约束

- 所有页面、接口错误和文档使用中文。
- 只支持广州；城市通过数据库中名称为“广州”的有效城市解析，不把神州城市编号硬编码为数据库主键。
- 所有任务只能手动创建，不增加定时创建或推送。
- 不处理登录、Cookie、预约或下单。
- 单任务请求量 2000 次以上降为单并发，5000 次为硬上限。
- 成功样本跨任务缓存 2 小时；失败、停止或格式异常样本不复用。
- 任务和标准化结果保留 60 天；永久车型库只增不删。
- 不覆盖人工或 AI 高可信能源字段。
- 不启用子代理，按本计划在当前会话顺序执行。

---

### 任务 1：日期规划、领域状态和数据库迁移

**文件：**
- 新建：`app/model_search/__init__.py`
- 新建：`app/model_search/domain.py`
- 新建：`app/model_search/planner.py`
- 修改：`app/models.py`
- 新建：`alembic/versions/0015_add_model_search.py`
- 新建：`tests/test_model_search_planner.py`
- 新建：`tests/test_model_search_models.py`

**接口：**
- 产出：`future_weekend_windows(now, weekend_count=4) -> tuple[RentalWindow, ...]`
- 产出：`hourly_windows(base_date) -> tuple[RentalWindow, ...]`
- 产出：`ModelSearchRunStatus`、`ModelSearchPhase`、`ModelSearchSampleStatus`、`ModelSearchSampleKind`
- 产出数据库实体：`ModelSearchRun`、`ModelSearchTarget`、`ModelSearchSample`、`ModelSearchOffer`

- [ ] **步骤 1：编写未来周末与小时采样失败测试**

```python
def test_future_weekend_windows_create_eight_base_periods():
    now = datetime(2026, 9, 2, 12, tzinfo=SHANGHAI)
    windows = future_weekend_windows(now)
    assert len(windows) == 8
    assert windows[0].pickup == datetime(2026, 9, 5, 9, tzinfo=SHANGHAI)
    assert windows[0].return_time == datetime(2026, 9, 6, 9, tzinfo=SHANGHAI)
    assert windows[1].return_time == datetime(2026, 9, 7, 9, tzinfo=SHANGHAI)

def test_hourly_windows_cover_08_to_20_for_both_durations():
    windows = hourly_windows(date(2026, 9, 5))
    assert len(windows) == 26
    assert {item.pickup.hour for item in windows} == set(range(8, 21))
    assert {item.duration_hours for item in windows} == {24, 48}
```

- [ ] **步骤 2：运行测试并确认缺少模块而失败**

运行：`pytest tests/test_model_search_planner.py -q`

预期：导入 `app.model_search.planner` 失败。

- [ ] **步骤 3：实现不可变租期值对象和规划器**

```python
@dataclass(frozen=True, slots=True)
class RentalWindow:
    pickup: datetime
    return_time: datetime

    @property
    def duration_hours(self) -> int:
        return int((self.return_time - self.pickup).total_seconds() // 3600)

def future_weekend_windows(now: datetime, weekend_count: int = 4) -> tuple[RentalWindow, ...]: ...
def hourly_windows(base_date: date) -> tuple[RentalWindow, ...]: ...
```

规划器使用 `Asia/Shanghai`，从尚未开始的最近周六算起；小时扫描包含 08:00–20:00，任务创建时通过请求键去除基础扫描已有的 09:00 组合。

- [ ] **步骤 4：编写数据库约束失败测试**

验证以下约束：目标唯一键 `run_id + vehicle_model_id`；样本唯一键 `run_id + anchor_department_id + pickup_time + return_time`；报价唯一键 `sample_id + vehicle_model_id + department_id`；所有计数非负；取车时间早于还车时间。

- [ ] **步骤 5：增加 ORM 实体和 Alembic 迁移**

`ModelSearchRun` 保存城市、状态、阶段、目标指纹、预计/计划/成功/失败/缓存/请求计数、候选网点数、已发现变体数、可取网点数及生命周期时间。

`ModelSearchTarget` 保存创建任务时的车型名称、内部车型 ID、神州车型 ID 快照。

`ModelSearchSample` 保存阶段、查询锚点、准确租期、状态、领取令牌、尝试次数、退避时间、缓存来源、安全错误摘要和响应计数。

`ModelSearchOffer` 保存样本、目标车型、返回网点、日均/套餐价、可预订状态、库存类型、原始描述、距鱼珠距离和验证时间。

- [ ] **步骤 6：运行规划器和模型测试**

运行：`pytest tests/test_model_search_planner.py tests/test_model_search_models.py -q`

预期：全部通过。

- [ ] **步骤 7：提交领域与迁移**

```bash
git add app/model_search/__init__.py app/model_search/domain.py app/model_search/planner.py app/models.py alembic/versions/0015_add_model_search.py tests/test_model_search_planner.py tests/test_model_search_models.py
git commit -m "feat: 增加按车型找车任务模型"
```

### 任务 2：短事务仓储、目标解析和样本缓存

**文件：**
- 新建：`app/model_search/repository.py`
- 新建：`tests/test_model_search_repository.py`

**接口：**
- 消费：任务 1 的 ORM 实体与 `RentalWindow`
- 产出：`ModelSearchRepository.create_run(city_id, model_names, now)`
- 产出：`ModelSearchRepository.claim_samples(run_id, limit, now) -> ModelSearchClaimBatch`
- 产出：`complete_sample()`、`fail_sample()`、`stop()`、`resume()`、`interrupt_stale_runs()`
- 产出：`seed_fine_samples(run_id)` 和 `find_recent_cache(sample, target_fingerprint, cutoff)`

- [ ] **步骤 1：编写任务创建失败测试**

覆盖：空目标拒绝；车型库外名称拒绝；同名多个神州 ID 全部快照；多名称去重；广州网点快照生成 8 个基础租期；同一请求只生成一个样本。

```python
run = repository.create_run(
    city_id=city.id,
    model_names=["比亚迪海狮05", "丰田bZ5"],
    now=datetime(2026, 9, 2, 12, tzinfo=SHANGHAI),
)
assert run.planned_sample_count == 8 * 91
assert target_ids == {4952, 5187, 5188}
```

- [ ] **步骤 2：运行仓储测试并确认失败**

运行：`pytest tests/test_model_search_repository.py -q`

预期：缺少 `ModelSearchRepository`。

- [ ] **步骤 3：实现创建与领取短事务**

创建任务时：锁定广州活动任务约束；规范化并排序车型名称；解析全部同名变体；计算 SHA-256 目标指纹；保存目标和基础样本。领取使用 PostgreSQL `FOR UPDATE SKIP LOCKED`，不在数据库会话中等待网络。

- [ ] **步骤 4：实现候选网点并集和精扫样本**

候选网点来自 `citywide_offers` 与当前基础扫描目标报价的网点并集，按神州网点 ID 去重。对四个周六生成 08:00–20:00、24/48 小时样本，并用数据库唯一约束去除 09:00 重复组合。

基础阶段结束后计算最终计划数：超过 5000 时把任务置为 `BUDGET_EXCEEDED`，不生成部分精扫样本；超过 2000 时记录执行并发为 1，否则为 2。

- [ ] **步骤 5：实现两小时缓存和任务状态机**

缓存只复用相同城市、锚点、租期、请求版本及目标指纹的已完成样本。复用时复制该样本的目标报价，标记 `cache_source_sample_id`，增加缓存计数但不增加上游请求数。

状态机必须支持等待、基础扫描、小时精扫、完成、部分完成、停止、中断和预算超限。失败样本存在时任务为部分完成；停止后不领取新样本；继续只恢复待处理样本。

- [ ] **步骤 6：运行仓储测试**

运行：`pytest tests/test_model_search_repository.py -q`

预期：全部通过。

- [ ] **步骤 7：提交仓储层**

```bash
git add app/model_search/repository.py tests/test_model_search_repository.py
git commit -m "feat: 增加按车型找车任务仓储"
```

### 任务 3：后台执行器和运行时恢复

**文件：**
- 新建：`app/model_search/service.py`
- 修改：`app/citywide/service.py`
- 修改：`app/scheduling.py`
- 修改：`app/main.py`
- 新建：`tests/test_model_search_service.py`
- 修改：`tests/test_rebuild_scheduling_maintenance.py`

**接口：**
- 消费：`ModelSearchRepository`、`ZucheClient.choose_car()`、`parse_choose_car()`
- 产出：`ModelSearchService.create_run()`、`process_active_run()`、`stop()`、`resume()`、`interrupt_stale_runs()`
- 产出：`application.state.model_search_service`

- [ ] **步骤 1：编写并发、会话边界和结果过滤失败测试**

```python
result = await service.process_active_run()
assert tracker.open_count == 0
assert gateway.maximum_concurrency == 2
assert saved_offer_model_ids == selected_target_ids
assert permanent_library_contains_non_target is True
```

同时验证超过 2000 次时最大并发为 1；格式错误不进入成功缓存；网络超时按退避时间重试；停止时在途请求可安全落库。

- [ ] **步骤 2：运行服务测试并确认失败**

运行：`pytest tests/test_model_search_service.py -q`

预期：缺少 `ModelSearchService`。

- [ ] **步骤 3：抽取并复用车型库更新函数**

把 `app/citywide/service.py` 中永久车型写入逻辑整理为可复用公共函数，保持现有行为：空字段不覆盖、人工和 AI 来源不覆盖、首次发现不变化、最近发现更新。

- [ ] **步骤 4：实现网络请求和标准化落库**

服务先在短事务中领取样本，再关闭会话并并行请求。每个响应解析全部车型以更新永久库，但只有 `ModelSearchTarget.vehicle_model_id` 对应报价写入 `ModelSearchOffer`。报价的实际网点使用返回 `deptId` 解析，距离统一以网点坐标到鱼珠计算。

- [ ] **步骤 5：实现失败退避和阶段推进**

超时、连接失败、5xx、429 最多重试 3 次，退避为 2、4、8 秒并写入 `next_attempt_at`；格式异常和业务拒绝直接记为最终失败。基础样本耗尽时生成精扫样本；精扫耗尽时完成任务。

- [ ] **步骤 6：接入 APScheduler 和重启恢复**

`RadarRuntime` 每两秒调用一次 `process_active_run()`，`max_instances=1`。应用启动时把遗留等待/运行样本恢复为待处理，并把活动任务标记为“已中断”，等待用户手动继续。

- [ ] **步骤 7：运行服务与运行时测试**

运行：`pytest tests/test_model_search_service.py tests/test_rebuild_scheduling_maintenance.py tests/test_citywide_service.py -q`

预期：全部通过，现有全城扫描行为无回归。

- [ ] **步骤 8：提交执行器**

```bash
git add app/model_search/service.py app/citywide/service.py app/scheduling.py app/main.py tests/test_model_search_service.py tests/test_rebuild_scheduling_maintenance.py
git commit -m "feat: 执行按车型周末扫描任务"
```

### 任务 4：结果聚合、控制 API 和清理策略

**文件：**
- 新建：`app/model_search/catalog.py`
- 新建：`app/api/model_search.py`
- 修改：`app/api/router.py`
- 修改：`app/maintenance.py`
- 修改：`app/scheduling.py`
- 新建：`tests/test_model_search_api.py`
- 新建：`tests/test_model_search_catalog.py`
- 修改：`tests/test_citywide_maintenance.py`

**接口：**
- `POST /api/model-search-runs`
- `GET /api/model-search-runs`
- `GET /api/model-search-runs/{run_id}`
- `POST /api/model-search-runs/{run_id}/stop`
- `POST /api/model-search-runs/{run_id}/resume`
- `GET /api/model-search-runs/{run_id}/results`
- `GET /api/model-search-runs/{run_id}/periods`
- `GET /api/model-library/options`

- [ ] **步骤 1：编写严格 API 契约失败测试**

创建请求固定为：

```json
{"model_names":["比亚迪海狮05","丰田bZ5"]}
```

测试空数组、重复后为空、未知车型、额外字段、非广州环境、活动任务冲突均返回中文 4xx；内部数据库和上游异常不得泄露。

- [ ] **步骤 2：编写可信结果聚合失败测试**

覆盖：成功报价为 `AVAILABLE`；所有相关样本完成且无报价为 `NOT_FOUND`；任一样本待处理、停止或失败为 `INCOMPLETE`；同名变体分别返回；准确租期不推断；均价/最低/最高只使用有效价格。

- [ ] **步骤 3：实现 API 与数据库分页聚合**

任务列表每页默认 20 条。结果先按目标车型名称分页，再通过独立 periods 接口按需读取准确租期、变体和网点；默认 `availability=AVAILABLE`，允许 `ALL`、`NOT_FOUND`、`INCOMPLETE`。

- [ ] **步骤 4：实现车型库多选搜索**

`GET /api/model-library/options?q=海狮&limit=20` 按车型名称聚合，返回名称、变体数量和最近发现时间。最少输入 1 个字符；空查询返回最近发现的前 20 个名称。

- [ ] **步骤 5：实现 60 天分批清理**

新增维护器按外键顺序删除过期报价、样本、目标和任务；每批最多 1000 条。不得删除永久车型、人工标签、全城扫描精简历史。

- [ ] **步骤 6：运行 API、聚合和清理测试**

运行：`pytest tests/test_model_search_api.py tests/test_model_search_catalog.py tests/test_citywide_maintenance.py -q`

预期：全部通过。

- [ ] **步骤 7：提交后端接口**

```bash
git add app/model_search/catalog.py app/api/model_search.py app/api/router.py app/maintenance.py app/scheduling.py tests/test_model_search_api.py tests/test_model_search_catalog.py tests/test_citywide_maintenance.py
git commit -m "feat: 增加按车型找车接口"
```

### 任务 5：首页、任务记录和三级结果页面

**文件：**
- 修改：`app/templates/discovery.html`
- 修改：`app/templates/scan_runs.html`
- 新建：`app/templates/model_search_results.html`
- 修改：`app/web.py`
- 修改：`app/static/citywide.js`
- 修改：`app/static/citywide.css`
- 修改：`app/templates/base.html`
- 新建：`tests/test_model_search_web.py`
- 修改：`tests/test_citywide_web.py`

**接口：**
- 新增网页：`GET /model-search/{run_id}`
- 消费：任务 4 的任务、车型选项和结果 API

- [ ] **步骤 1：编写页面结构失败测试**

验证：首页第一卡标题“广州全城车型扫描”；`fish-quick-scan` 位于第一卡内部并默认收起；第二卡包含 `model-search-form` 和多车型选择容器；旧 `citywide-model-filters` 不在首页；扫描记录包含“全城扫描/按车型找车”页签；结果页包含三级按需展开容器和可信性提示。

- [ ] **步骤 2：运行页面测试并确认失败**

运行：`pytest tests/test_model_search_web.py tests/test_citywide_web.py -q`

预期：新页面和控件缺失。

- [ ] **步骤 3：调整首页信息架构**

把附近快速扫描的 `<details>` 移入全城扫描卡片底部。第二卡使用可搜索多选框、已选标签、默认采样规则、请求量说明、启动按钮和最新任务进度。最近汇总保持紧凑，不在首页展示车型详情。

- [ ] **步骤 4：实现任务记录双页签**

默认打开用户上次选择的页签；全城扫描继续使用现有接口，按车型找车读取新接口。运行中每 3 秒只更新当前页签；页面不可见时暂停轮询。

- [ ] **步骤 5：实现三级结果列表**

第一层按车型名称，第二层为准确租期，第三层为车型 ID 与可取网点。相邻整点成功样本可显示“连续采样可租”，展开必须保留每个准确时间。失败或待处理显示“扫描不完整”，不能显示“无车”。

- [ ] **步骤 6：完成响应式样式和可访问性**

桌面端使用紧凑数据行，移动端按车型/租期/网点分块；按钮不悬浮遮挡；统一字号；键盘可操作多选和展开；动态状态使用 `aria-live`；遵守 `prefers-reduced-motion`。

- [ ] **步骤 7：运行前端结构和现有页面回归测试**

运行：`pytest tests/test_model_search_web.py tests/test_citywide_web.py tests/test_rebuild_web.py -q`

预期：全部通过。

- [ ] **步骤 8：提交页面**

```bash
git add app/templates/discovery.html app/templates/scan_runs.html app/templates/model_search_results.html app/templates/base.html app/web.py app/static/citywide.js app/static/citywide.css tests/test_model_search_web.py tests/test_citywide_web.py
git commit -m "feat: 增加按车型找车页面"
```

### 任务 6：API 台账、中文文档、完整验证和部署

**文件：**
- 修改：`app/api_catalog.py`
- 修改：`README.md`
- 修改：`docs/superpowers/specs/2026-09-02-guangzhou-model-search-design.md`
- 新建：`tests/test_model_search_acceptance.py`
- 修改：`tests/test_web_pages.py`

**接口：**
- 消费：任务 1–5 的全部用户能力
- 产出：系统设置 API 台账中的中文说明、参数和调用示例

- [ ] **步骤 1：编写端到端验收失败测试**

以 2 个周末和少量网点的测试夹具缩小数据量，验证：多车型创建、基础扫描、候选网点并集、小时精扫、缓存命中、结果三级读取、停止继续以及非目标车型更新永久库。

- [ ] **步骤 2：补充 API 台账和 README**

为全部新接口增加中文摘要、详细说明和可复制请求示例。README 增加“按车型找车”的使用步骤、请求量解释、结果可信性、60 天保留和异店还车限制。

- [ ] **步骤 3：运行新增功能测试**

运行：`pytest tests/test_model_search_planner.py tests/test_model_search_models.py tests/test_model_search_repository.py tests/test_model_search_service.py tests/test_model_search_api.py tests/test_model_search_catalog.py tests/test_model_search_web.py tests/test_model_search_acceptance.py -q`

预期：全部通过。

- [ ] **步骤 4：运行完整测试**

运行：`pytest -q`

预期：全部通过，无失败和错误。

- [ ] **步骤 5：检查迁移和代码质量**

运行：

```bash
alembic upgrade head
alembic current
git diff --check
```

预期：数据库位于 `0015_add_model_search`，差异检查无输出。

- [ ] **步骤 6：提交文档和验收测试**

```bash
git add app/api_catalog.py README.md docs/superpowers/specs/2026-09-02-guangzhou-model-search-design.md tests/test_model_search_acceptance.py tests/test_web_pages.py
git commit -m "docs: 完善按车型找车说明"
```

- [ ] **步骤 7：重建并验证 Docker 服务**

运行：

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8093/healthz
```

预期：应用和 PostgreSQL 均为 healthy，健康接口返回 `status=ok` 和 `database=ok`。

- [ ] **步骤 8：只暂存本任务文件并推送**

检查 `git status` 和提交内容，不暂存现有 `.superpowers/`。若存在未提交的本任务文件，逐个暂存后提交；最后执行：

```bash
git push -u origin HEAD
```

预期：`origin/codex/zuche-radar-rebuild` 指向最终提交。
