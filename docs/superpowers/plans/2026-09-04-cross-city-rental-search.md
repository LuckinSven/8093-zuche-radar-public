# 跨城异地还车专项搜索 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有系统中提供按精确车型、多个取车城市和准确 14 天租期扫描的跨城异地还车页面。

**Architecture:** 扩展现有按车型找车任务，让一个跨城任务的样本锚点来自多个城市，并把取车城市与还车城市分别传给神州接口。复用现有后台状态机、报价表与永久车型库，增加跨城专用 API、结果汇总和轻量页面。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL 16、Alembic、Jinja2、原生 JavaScript/CSS、pytest、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-09-04-cross-city-rental-search-design.md`

## Global Constraints

- 所有用户界面和文档使用中文。
- 不引入登录、Cookie、铁路账号或自动定时扫描。
- 单任务最多 2000 次上游请求。
- 保持现有广州周末“按车型找车”行为兼容。
- 按神州稳定车型 ID 精确选择车型。

---

### Task 1: 区分取车城市和还车城市

**Files:**
- Modify: `app/domain.py`
- Modify: `app/zuche/client.py`
- Modify: `app/model_search/domain.py`
- Modify: `app/model_search/service.py`
- Test: `tests/test_zuche_client_and_parser.py`
- Test: `tests/test_model_search_service.py`

**Interfaces:**
- Consumes: 现有 `ScanQuery` 和 `ZucheClient.choose_car()`。
- Produces: `ScanQuery.return_city_id` 可选字段，以及领取样本中明确的 `return_zuche_city_id`。

- [x] **Step 1: Write the failing tests**：断言跨城查询发送不同的 `pickupCityId` 与 `returnCityId`，普通查询仍默认同城。
- [x] **Step 2: Run tests to verify they fail**：运行两个目标测试文件，确认失败原因是缺少还车城市字段。
- [x] **Step 3: Write minimal implementation**：为查询和领取对象增加还车城市，构造上游请求时使用该字段。
- [x] **Step 4: Run tests to verify they pass**：目标测试全部通过。

### Task 2: 持久化跨城任务和准确租期

**Files:**
- Create: `alembic/versions/0016_add_cross_city_search.py`
- Modify: `app/models.py`
- Modify: `app/model_search/repository.py`
- Test: `tests/test_cross_city_search_repository.py`

**Interfaces:**
- Consumes: 车型库 ID、城市 ID、`RentalWindow`。
- Produces: `ModelSearchRepository.create_cross_city_run(...)`，创建多城市基础样本并执行 2000 请求上限校验。

- [x] **Step 1: Write the failing repository tests**：覆盖精确车型、多城市两租期、错误车型、无网点城市和超预算。
- [x] **Step 2: Run tests to verify they fail**。
- [x] **Step 3: Add migration and model fields**：增加搜索类型、还车城市、目标还车点、所选城市、租期和铁路费用 JSON。
- [x] **Step 4: Implement cross-city run creation and completion**：跨城任务不进入小时精扫，领取时从锚点网点解析取车城市。
- [x] **Step 5: Run repository and existing model-search tests**。

### Task 3: 跨城任务 API 与城市结果汇总

**Files:**
- Modify: `app/api/model_search.py`
- Modify: `app/model_search/catalog.py`
- Modify: `app/api_catalog.py`
- Test: `tests/test_cross_city_search_api.py`

**Interfaces:**
- Consumes: `create_cross_city_run(...)` 和现有任务控制方法。
- Produces: `/api/cross-city-search-runs` 创建、列表、详情、停止、继续和结果接口。

- [x] **Step 1: Write failing API tests**：验证中文校验、精确模型、两组 14 天窗口、任务进度和三态结果。
- [x] **Step 2: Run tests to verify they fail**。
- [x] **Step 3: Implement strict request schemas and routes**。
- [x] **Step 4: Implement city/window/department result aggregation**：按预计总成本排序，失败样本使城市结果为不完整。
- [x] **Step 5: Run API and catalog tests**。

### Task 4: 跨城专项页面

**Files:**
- Create: `app/templates/cross_city_search.html`
- Create: `app/static/cross_city_search.js`
- Create: `app/static/cross_city_search.css`
- Modify: `app/web.py`
- Modify: `app/templates/base.html`
- Test: `tests/test_cross_city_search_web.py`

**Interfaces:**
- Consumes: 城市目录同步 API、车型库 API、跨城任务 API。
- Produces: `/cross-city-search` 用户页面。

- [x] **Step 1: Write failing web tests**：验证导航、精确车型输入、两组租期、计划还车点、城市分组、任务进度和结果容器。
- [x] **Step 2: Run tests to verify they fail**。
- [x] **Step 3: Implement template and responsive styles**。
- [x] **Step 4: Implement browser behavior**：启动时逐城同步目录，显示同步进度，创建任务并轮询；支持停止、继续和结果展开。
- [x] **Step 5: Run web tests**。

### Task 5: 运行态接入、文档和验收

**Files:**
- Modify: `app/scheduling.py`
- Modify: `README.md`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_settings_api.py`

**Interfaces:**
- Consumes: 扩展后的 `ModelSearchService`。
- Produces: 重启恢复、60 天清理说明和 API 台账。

- [x] **Step 1: Write failing integration tests**：覆盖跨城任务与 API 台账。
- [x] **Step 2: Run tests to verify they fail**。
- [x] **Step 3: Update runtime, API catalog and Chinese documentation**。
- [x] **Step 4: Run focused tests, full pytest and Docker build**。
- [x] **Step 5: Inspect diff, stage only task files, commit and push current branch**。
