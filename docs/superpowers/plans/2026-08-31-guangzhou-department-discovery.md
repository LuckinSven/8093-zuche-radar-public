# 广州神州网点发现实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在第一阶段匿名城市目录基础上，交付可限速、可停止、可恢复的广州网点渐进遍历与覆盖展示页面。

**Architecture:** PostgreSQL 持久化发现任务和确定性网格点，后台运行时每次只处理一个点并保存进度；每个响应按神州 `deptId` upsert 最新网点。页面显示已发现数量、请求和新增趋势，明确不宣称绝对完整。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL 16、Alembic、APScheduler、httpx、Jinja2、原生 JavaScript、pytest、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-08-31-map-cache-department-discovery-design.md`

## Global Constraints

- 依赖计划 `2026-08-31-map-cache-session-city-catalog.md` 已完成并迁移到 `0008`。
- 默认只允许一个广州活动任务，并发数固定为 1。
- 默认请求间隔不低于 2 秒并带小幅抖动；匿名接口失败即保存安全错误，不回退认证。
- 用户可停止和继续；容器重启把运行中任务改为中断，不自动重跑。
- `deptId` 是网点唯一标识；一次未返回不得删除网点。
- 页面使用“已发现网点”，不使用“全部网点”。
- 第一阶段不默认遍历全国，不猜测或高频探测未知接口。

---

### Task 1: 网点目录和发现任务持久化

**Files:**
- Create: `alembic/versions/0009_add_department_discovery.py`
- Modify: `app/models.py`
- Create: `app/repositories/departments.py`
- Create: `tests/test_department_repository.py`

**Interfaces:**
- Produces: 扩展 `Department`；`DepartmentDiscoveryRun`、`DepartmentDiscoveryPoint`；`DepartmentRepository.upsert()`；`DepartmentDiscoveryRepository.create_run()`、`next_pending_point()`、`get_run()`、`mark_running()`、`mark_stopped()`、`mark_interrupted()`、`interrupt_stale_runs()`。

- [ ] **Step 1: 写 `deptId` 去重、最新字段覆盖和任务恢复失败测试**

```python
from datetime import UTC, datetime

def test_department_upsert_keeps_identity_and_latest_fields(session, city):
    repo = DepartmentRepository(session)
    first = repo.upsert(city, {"deptId": 88, "deptName": "旧名", "lat": 23.1, "lon": 113.2})
    second = repo.upsert(city, {"deptId": 88, "deptName": "新名", "lat": 23.2, "lon": 113.3})
    assert second.id == first.id
    assert second.name == "新名"

def test_running_discovery_is_marked_interrupted_on_startup(session):
    discovery_repo = DepartmentDiscoveryRepository(session)
    run = discovery_repo.create(city_id=1, preset="quick", radius_km=35,
        spacing_km=10, max_requests=80,
        pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        return_time=datetime(2026, 9, 6, 9, 0, tzinfo=UTC))
    discovery_repo.mark_running(run)
    assert discovery_repo.interrupt_stale_runs() == 1
```

- [ ] **Step 2: 运行测试并确认缺少模型/迁移**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_department_repository.py`

- [ ] **Step 3: 实现模型、迁移和仓储**

网点增加城市、行政区、营业/自助字段和发现时间；任务保存 preset、半径、间距、上限、租期、计数和状态；点位按任务与量化坐标唯一，保存轮次、来源、状态、返回/新增数量和安全错误。

- [ ] **Step 4: 运行测试、检查单一 `0009` head 并提交**

```bash
git add alembic/versions/0009_add_department_discovery.py app/models.py app/repositories/departments.py tests/test_department_repository.py
git commit -m "feat: add department discovery persistence"
```

---

### Task 2: 确定性广州网格规划

**Files:**
- Create: `app/departments/planner.py`
- Create: `app/departments/__init__.py`
- Create: `tests/test_department_planner.py`

**Interfaces:**
- Produces: `DiscoveryPreset` 和 `build_grid(center_lat, center_lon, radius_km, spacing_km)`。

- [ ] **Step 1: 写圆形范围、稳定顺序和去重失败测试**

```python
def test_grid_is_deterministic_inside_radius_and_contains_center():
    first = build_grid(23.1291, 113.2644, radius_km=35, spacing_km=10)
    second = build_grid(23.1291, 113.2644, radius_km=35, spacing_km=10)
    assert first == second
    assert first[0] == (23.1291, 113.2644)
    assert all(haversine_km((23.1291, 113.2644), point) <= 35.01 for point in first)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_department_planner.py`

- [ ] **Step 3: 实现无需地图 API 的网格**

纬度每公里约 `1/111.32` 度，经度按中心纬度余弦换算；点位量化 6 位小数并按中心、由近到远、纬经度稳定排序。预设固定为快速 `35/10/80`、标准 `70/10/180`、深度 `90/7.5/500`（半径 km/间距 km/最大请求）。

- [ ] **Step 4: 运行测试并提交**

```bash
git add app/departments/__init__.py app/departments/planner.py tests/test_department_planner.py
git commit -m "feat: plan deterministic department coverage grids"
```

---

### Task 3: 限速、停止和恢复的发现运行器

**Files:**
- Create: `app/departments/service.py`
- Modify: `app/scheduling.py`
- Modify: `app/main.py`
- Create: `tests/test_department_discovery_service.py`
- Modify: `tests/test_rebuild_scheduling_maintenance.py`

**Interfaces:**
- Produces: `DepartmentDiscoveryService.create_run()`、`process_next_point()`、`stop()`、`resume()`；运行时 `department_discovery` 作业。

- [ ] **Step 1: 写逐点提交、请求上限、停止和无新增结束测试**

```python
@pytest.mark.asyncio
async def test_runner_persists_each_point_and_stops_at_request_limit(
        session, guangzhou, fake_gateway):
    repository = DepartmentDiscoveryRepository(session)
    service = DepartmentDiscoveryService(repository, DepartmentRepository(session),
                                         fake_gateway, sleep=lambda _: None)
    run = service.create_run(guangzhou, preset="quick", max_requests=2)
    await service.process_next_point(run.id)
    await service.process_next_point(run.id)
    assert repository.get_run(run.id).request_count == 2
    assert repository.get_run(run.id).status == "COMPLETED_LIMIT"

def test_stop_and_resume_only_change_pending_work(session, discovery_run):
    repository = DepartmentDiscoveryRepository(session)
    service = DepartmentDiscoveryService(repository, DepartmentRepository(session),
                                         fake_gateway, sleep=lambda _: None)
    service.stop(discovery_run.id)
    assert repository.get_run(discovery_run.id).status == "STOPPED"
    service.resume(discovery_run.id)
    assert repository.get_run(discovery_run.id).status == "RUNNING"
```

- [ ] **Step 2: 运行测试并确认服务不存在**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_department_discovery_service.py`

- [ ] **Step 3: 实现任务创建与逐点处理**

任务创建生成预设网格并加入现有扫描点、网点和缓存地铁站种子后去重。每次处理一个 pending 点：构造 09:00 到次日 09:00 查询，调用无参数匿名客户端，解析 `deptHangModels`，按 `deptId` upsert，更新点和任务计数并提交。匿名接口失败只保存安全摘要，不回退认证。

- [ ] **Step 4: 接入运行时**

`RadarRuntime.start()` 将遗留 RUNNING 标记 INTERRUPTED；活动任务由固定间隔作业每次处理一个点。停止不再取新点，继续把 STOPPED/INTERRUPTED 改回 RUNNING。同城活动任务通过数据库检查互斥。

- [ ] **Step 5: 运行服务和调度测试并提交**

```bash
git add app/departments/service.py app/scheduling.py app/main.py tests/test_department_discovery_service.py tests/test_rebuild_scheduling_maintenance.py
git commit -m "feat: run resumable department discovery"
```

---

### Task 4: 网点 API 与系统设置二级页面

**Files:**
- Create: `app/api/departments.py`
- Modify: `app/api/router.py`
- Modify: `app/web.py`
- Create: `app/templates/settings_departments.html`
- Modify: `app/templates/settings.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Create: `tests/test_departments_api.py`
- Modify: `tests/test_rebuild_web.py`

**Interfaces:**
- Produces: 网点列表/统计、任务创建/停止/继续/详情 API；`GET /settings/departments`。

- [ ] **Step 1: 写 API 状态机和页面失败测试**

```python
@pytest.mark.asyncio
async def test_only_one_active_city_discovery_can_be_created(client):
    first = await client.post("/api/departments/discovery-runs", json={"city_id": 1, "preset": "quick"})
    second = await client.post("/api/departments/discovery-runs", json={"city_id": 1, "preset": "quick"})
    assert first.status_code == 201
    assert second.status_code == 409

@pytest.mark.asyncio
async def test_department_page_says_discovered_not_complete(client):
    response = await client.get("/settings/departments")
    assert "已发现网点" in response.text
    assert "全部网点" not in response.text
```

- [ ] **Step 2: 运行测试并确认 404**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_departments_api.py tests/test_rebuild_web.py`

- [ ] **Step 3: 实现固定 API 和状态校验**

网点列表支持城市、行政区、关键词、活跃状态和分页；任务只接受固定 preset 和合理日期；停止/继续仅允许合法状态，冲突返回 409，找不到返回 404。

- [ ] **Step 4: 实现页面**

展示已发现/含坐标网点数、行政区分布、请求/覆盖点、新增趋势、最近任务和安全错误；提供同步城市、创建预设任务、停止、继续、筛选和详情。按钮请求期间禁用，外部字段使用 `esc()`。

- [ ] **Step 5: 运行 API 与页面测试并提交**

```bash
git add app/api/departments.py app/api/router.py app/web.py app/templates/settings_departments.html app/templates/settings.html app/static/app.js app/static/app.css tests/test_departments_api.py tests/test_rebuild_web.py
git commit -m "feat: add department discovery workspace"
```

---

### Task 5: 完整验收、文档与部署

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新中文运维说明**

说明三个遍历预设、请求上限、停止/继续、匿名接口失败即安全报错、不回退认证、覆盖不是完整性保证，以及如何低频逐步扩大广州覆盖。

- [ ] **Step 2: 完整测试和新库迁移**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q && /tmp/zuche-radar-test-env/bin/alembic heads && docker compose config -q && git diff --check`

在独立临时数据库从零迁移到 `0009`，确认任务/点位表、网点扩展字段、缓存和广州种子。

- [ ] **Step 3: Docker 重建和低频广州验收**

重建后确认 app/db healthy、遗留任务恢复逻辑、创建“快速”广州任务并只处理有限点位；确认网点按 `deptId` 去重、任务可停止/继续、页面统计一致。验收不运行深度 500 请求任务。

- [ ] **Step 4: 安全检查、提交和推送**

检查 Git、日志和响应不含 AK 或认证信息；提交 README，完成代码审查后按仓库规则推送当前分支，不创建 PR。
