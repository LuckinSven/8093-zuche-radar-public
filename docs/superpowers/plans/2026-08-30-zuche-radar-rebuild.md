# 神州车型雷达重做版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从数据、扫描服务、API、页面和 Docker 部署完整重建可在局域网使用的个人神州车型雷达。

**Architecture:** FastAPI 模块化单体提供 JSON API 与 Jinja 页面，PostgreSQL 保存永久标准化历史和 60 天压缩原始响应。神州网关、纯解析器、扫描编排、变化检测、查询服务、个人信息、调度和维护彼此隔离，手动与自动扫描共用同一扫描服务。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL 16、httpx、APScheduler、Jinja2、原生 JavaScript/CSS、pytest、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-08-30-zuche-radar-rebuild-design.md`

## 全局约束

- 服务端口固定为 `8093`，容器监听 `0.0.0.0`。
- 个人使用，不实现鉴权、多租户、神州登录、下单或支付。
- PostgreSQL 是唯一运行数据库，不使用 SQLite 替代测试或生产存储。
- 车型以神州 `modelId` 唯一，网点以神州 `deptId` 唯一。
- 标准化历史永久保存，gzip 原始 JSON 严格保存 60 天。
- 自动扫描默认关闭，手动扫描为主流程。
- 所有页面价格均标注为基础/列表价格和“非最终结算价”。
- 用户文档、页面文案和运维说明全部使用中文。

---

## 文件结构

```text
app/
  main.py                 # 应用工厂与生命周期
  settings.py             # 环境配置
  database.py             # 引擎、事务和依赖
  domain.py               # 领域枚举与不可变数据对象
  models.py               # SQLAlchemy 模型
  repositories/           # 按聚合拆分的数据访问
  zuche/client.py         # 神州网关
  zuche/parser.py         # 纯响应解析
  scanning/service.py     # 扫描编排
  scanning/changes.py     # 变化检测
  discovery.py            # 聚合、筛选和详情查询
  personal.py             # 个人状态与人工补充
  scheduling.py           # 扫描点调度
  maintenance.py          # 60 天清理
  api/                    # 扫描、发现、管理和个人信息 API
  web/                    # 页面路由、模板和静态资源
alembic/
scripts/acceptance_scan.py
tests/
  fixtures/guangzhou_choose_car_redacted.json
docker-compose.yml
Dockerfile
README.md
```

### 任务 1：建立干净的运行骨架与数据库迁移

**文件：** 重写 `app/main.py`、`app/settings.py`、`app/database.py`、`app/models.py`、`alembic/*`、`Dockerfile`、`docker-compose.yml`；测试 `tests/test_health.py`、`tests/test_schema.py`。

**接口：** 产出 `create_app() -> FastAPI`、`session_scope()`、`GET /healthz`，以及规格中的全部数据库表和约束。

- [ ] 写测试：健康检查返回 `{"service":"zuche-radar","status":"ok","database":"ok"}`；两个同名不同 `modelId` 的车型必须保存为两行；自动扫描点默认 `enabled=false`。
- [ ] 运行 `pytest tests/test_health.py tests/test_schema.py -v`，确认旧实现无法满足新接口。
- [ ] 删除旧临时模块边界，按文件结构实现设置、数据库生命周期、模型关系与显式 Alembic 迁移；健康检查执行 `SELECT 1`。
- [ ] 运行 `alembic upgrade head`、目标测试和 `docker compose config -q`，确认通过且无警告。
- [ ] 仅暂存任务文件，提交 `feat: rebuild application and database foundation`。

### 任务 2：重建神州网关与完整解析器

**文件：** 创建 `app/domain.py`、`app/zuche/client.py`、`app/zuche/parser.py`、脱敏夹具和 `tests/test_zuche_adapter.py`。

**接口：** 产出 `ZucheClient.resolve_city(lat, lon)`、`ZucheClient.choose_car(query)`、`parse_choose_car(payload, query) -> ParsedScan` 和 `ZucheGatewayError`。

- [ ] 写测试：请求必须发往 `/api/gw.do`、URI 查询参数正确、`data` 是 JSON 表单字段；解析结果保留网点距离、神州车型组、价格、可预订状态与 `modelDesc` 推导属性。
- [ ] 运行测试，确认因新包和领域对象不存在而失败。
- [ ] 实现异步客户端、20 秒超时、安全响应校验、城市 ID 解析和无副作用纯解析器；不把名字当作 ID。
- [ ] 运行目标测试，随后用已验证广州参数执行一次只读接口探测，确认响应结构仍兼容。
- [ ] 提交 `feat: rebuild zuche gateway adapter`。

### 任务 3：实现事务安全的扫描、历史快照和变化检测

**文件：** 创建 `app/repositories/scans.py`、`app/scanning/service.py`、`app/scanning/changes.py`、`tests/test_scan_service.py`。

**接口：** 产出 `ScanService.run(query, trigger) -> ScanResult`，事件类型为 `FIRST_SEEN`、`REAPPEARED`、`DISAPPEARED`、`PRICE_CHANGED`、`CLOSER_DEPARTMENT`。

- [ ] 写测试：首次成功扫描生成首次出现事件和 gzip 原始响应；第二次价格变化生成价格事件；网关失败保留失败扫描且旧快照数量不变；相同车型在更近网点出现生成更近网点事件。
- [ ] 运行测试，确认新扫描边界不存在而失败。
- [ ] 实现先提交 `PENDING`、两次可重试错误重试、单事务成功持久化、失败回滚与失败状态单独提交；历史比较仅使用相同扫描点和租期的最近成功扫描。
- [ ] 运行扫描与数据库集成测试，检查原始数据可被 gzip 解压且不包含请求 Cookie。
- [ ] 提交 `feat: rebuild scan persistence and change detection`。

### 任务 4：实现发现查询、历史、车型详情和完整筛选

**文件：** 创建 `app/repositories/discovery.py`、`app/discovery.py`、`tests/test_discovery.py`。

**接口：** `DiscoveryService.search(filters)`、`get_model_detail(model_id)`、`list_scan_history()`；筛选包含组、价格、距离、可租、车身、座位、能源、个人状态、变化类型。

- [ ] 写 PostgreSQL 集成测试，构造多个车型/网点/组/状态，逐项验证筛选并验证组合条件使用交集。
- [ ] 运行测试，确认查询服务不存在而失败。
- [ ] 实现车型中心聚合：最低基础价、最近网点、全部候选网点、原生组、推导字段和事件；源字段与推导字段分开返回。
- [ ] 运行发现与扫描测试，确认同名车型不合并且 Decimal 价格序列化稳定。
- [ ] 提交 `feat: add complete vehicle discovery queries`。

### 任务 5：实现个人状态和可信人工补充

**文件：** 创建 `app/repositories/personal.py`、重写 `app/personal.py`、`tests/test_personal.py`。

**接口：** `set_personal_state(model_id, state, note, rented_on)`、`add_annotation(model_id, field, value, source, confidence, verified_at)`、`list_annotations(model_id)`。

- [ ] 写测试：更新状态后发现筛选可见；人工补充必须保留来源和可信度；空字段和非法可信度被拒绝；人工信息不覆盖神州源字段。
- [ ] 运行测试，确认新仓储和校验不存在而失败。
- [ ] 实现单一当前状态、不可变人工补充修订和 `LOW/MEDIUM/HIGH` 校验。
- [ ] 运行个人与发现测试。
- [ ] 提交 `feat: rebuild personal vehicle data`。

### 任务 6：实现完整 JSON API

**文件：** 创建 `app/api/dependencies.py`、`app/api/scans.py`、`app/api/discovery.py`、`app/api/admin.py`、`app/api/personal.py`、`app/api/router.py`、`tests/test_api.py`。

**接口：** `POST /api/scans`、`GET /api/scans/{id}`、`GET /api/history`、`GET /api/discovery`、城市和扫描点 CRUD、个人状态更新、人工补充新增/查询。

- [ ] 写 API 测试：成功扫描返回 201 和数量；还车不晚于取车返回字段级 422；未知模型返回 404；筛选参数均传递到查询服务；扫描点创建默认关闭。
- [ ] 运行测试，确认旧单文件路由不满足契约。
- [ ] 实现每请求独立数据库会话、Pydantic 请求响应模型、稳定错误结构和路由拆分；真实扫描失败返回已保存的 `FAILED` 结果。
- [ ] 运行全部 API、服务和仓储测试。
- [ ] 提交 `feat: add complete radar api`。

### 任务 7：实现调度、启动恢复和 60 天维护

**文件：** 创建 `app/scheduling.py`、`app/maintenance.py`、`tests/test_scheduling.py`、`tests/test_maintenance.py`，修改 `app/main.py`。

**接口：** `ProbeScheduler.reconcile()`、`RawPayloadMaintenance.purge(now) -> int`；生命周期启动调度器，关闭时安全停止。

- [ ] 写测试：关闭扫描点不创建任务；分钟间隔和 cron 正确；第 60 天保留、第 61 天删除；清理后标准化快照仍存在。
- [ ] 运行测试，确认实现不存在而失败。
- [ ] 实现数据库驱动调度、默认无任务、配置变更重建、每日维护任务和关闭清理。
- [ ] 运行调度、维护及扫描失败保护测试。
- [ ] 提交 `feat: add probe scheduling and retention maintenance`。

### 任务 8：重做四个完整页面

**文件：** 重写 `app/web/*`、模板、CSS、JavaScript，创建 `tests/test_web.py`。

**接口：** `/`、`/models/{modelId}`、`/history`、`/admin`；浏览器仅调用 `/api/*`。

- [ ] 写页面测试：首页包含广州预设、地点/经纬度、四种时间方式、所有筛选和价格免责声明；详情含源信息、推导信息、个人信息、人工补充与历史；历史和管理页含所需控件。
- [ ] 运行测试，确认旧简化页面失败。
- [ ] 实现响应式页面、加载/空/错误状态、无障碍表单、车型卡片、详情图表或表格、探针编辑和中文反馈。
- [ ] 使用浏览器执行手动扫描到车型卡片的完整流程，并在手机宽度检查布局。
- [ ] 运行页面和 API 测试。
- [ ] 提交 `feat: rebuild complete radar web interface`。

### 任务 9：Docker、验收脚本、文档和最终部署

**文件：** 重写 `Dockerfile`、`docker-compose.yml`、`.dockerignore`、`.env.example`、`README.md`，创建 `scripts/acceptance_scan.py`、`tests/test_acceptance.py`。

**接口：** `docker compose up -d --build` 自动迁移并启动；验收脚本输出扫描 ID、状态、网点数、报价数和车型数。

- [ ] 写验收测试：脚本对成功结果退出 0、失败结果非 0 且不打印 Cookie 或原始响应；Compose 应用健康检查依赖数据库健康。
- [ ] 运行测试，确认旧部署配置不满足完整验收。
- [ ] 实现非 root 应用镜像、容器内迁移、健康检查、重启策略、持久化卷、局域网端口和中文运维文档。
- [ ] 运行 `pytest -v`、`alembic upgrade head`、`docker compose config -q` 和 `git diff --check`。
- [ ] 执行 `docker compose up -d --build`，验证数据库与应用 healthy、`/healthz`、首页和 API。
- [ ] 使用广州中心、09:00 至次日 09:00 执行真实验收扫描；成功时验证发现页结果，外部失败时验证失败历史已保存。
- [ ] 检查 Git 状态和差异，只暂存本任务文件，提交 `feat: deliver rebuilt docker radar system` 并推送分支。

## 计划自检

- 规格中的接口、数据保留、变化事件、个人信息、页面、调度、全国扩展边界和 Docker 局域网部署均对应一个任务。
- 所有生产行为均先写会失败的测试，再实现并运行目标测试。
- `ScanQuery`、`ParsedScan`、`ScanResult`、`DiscoveryService` 和仓储边界在产生任务中定义，后续任务只消费公开接口。
- 最终验收同时覆盖自动测试、迁移、容器健康和真实广州扫描。
