# 车型能源 AI 补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用用户自行配置的 OpenAI 兼容接口，后台分批补全车型能源大类和细分类型，并在车型库与全城车型中同步展示可追溯结果。

**Architecture:** 车型库继续作为当前能源结论的唯一来源；新增独立的 `enrichment` 领域模块管理 OpenAI 兼容客户端、结构化结果校验、任务仓储和任务服务。APScheduler 每两秒推进一个活动任务，页面只创建和控制任务；每批结果独立提交并保留修改前后快照。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLAlchemy 2、PostgreSQL 16、Alembic、httpx、APScheduler、Jinja2、原生 JavaScript/CSS、Docker Compose、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-ai-energy-enrichment-design.md`

## Global Constraints

- 所有用户可见文案、错误和后续文档使用中文。
- 服务端口保持 `8093`，继续支持 Docker 局域网访问。
- 不增加登录、鉴权或用户体系。
- API Key 只保存于 `integration_settings.secret_value`，任何读取接口、日志和异常响应均不得返回明文。
- 只调用用户配置的 OpenAI 兼容 `/chat/completions`，不依赖 Responses API、内置网页搜索或 OpenAI SDK。
- 第一版只补全能源大类和能源细分；不修改车型 ID、名称、原始描述和图片。
- 任务仅手动触发，不增加定时计划。
- 人工能源来源优先级最高，AI 和后续扫描均不得覆盖。
- 每个任务严格遵循 RED → GREEN → REFACTOR；只暂存当前任务文件，禁止 `git add .`。
- 用户已要求不使用子代理；本计划采用当前会话内联执行。

---

## File Structure

### 新增文件

- `alembic/versions/0014_add_vehicle_energy_enrichment.py`：新增车型能源字段、任务表、结果表、约束和索引。
- `app/enrichment/__init__.py`：领域包入口。
- `app/enrichment/domain.py`：枚举、提示输入、结构化建议、Token 用量和一致性校验。
- `app/enrichment/client.py`：OpenAI 兼容 `/chat/completions` 客户端和安全错误映射。
- `app/enrichment/repository.py`：任务、结果、车型选择、认领、统计和状态迁移的数据访问。
- `app/enrichment/service.py`：两阶段补全流程、自动应用门槛和任务控制。
- `app/api/enrichment.py`：补全任务的创建、列表、详情和控制接口。
- `app/templates/settings_ai_enrichment.html`：OpenAI 兼容接口配置和测试页面。
- `app/templates/enrichment_runs.html`：AI 补全历史页面。
- `app/static/enrichment.js`：设置表单、车型库任务进度、任务历史交互。
- `app/static/enrichment.css`：AI 设置、进度和历史的响应式样式。
- `tests/test_enrichment_models.py`：迁移后模型约束和默认值。
- `tests/test_enrichment_client.py`：请求格式、JSON 校验、Token 和安全错误。
- `tests/test_enrichment_repository.py`：任务选择、认领、幂等和统计。
- `tests/test_enrichment_service.py`：两阶段流程、优先级和控制状态机。
- `tests/test_enrichment_api.py`：任务 HTTP 契约和错误边界。
- `tests/test_enrichment_web.py`：设置、车型库入口、历史页面和同步展示。

### 修改文件

- `app/models.py`：新增车型能源字段、四组补全枚举和两个 ORM 模型。
- `app/citywide/service.py`：扩大扫描禁止覆盖的 AI 来源集合。
- `app/citywide/catalog.py`：序列化能源细分、来源、置信度和更新时间。
- `app/integrations/service.py`：序列化、保存、测试和清除 OpenAI 兼容配置。
- `app/api/settings.py`：新增 AI 配置请求模型和设置接口。
- `app/api/router.py`：注册补全任务路由。
- `app/scheduling.py`：构造补全服务、恢复中断任务并注册推进作业。
- `app/main.py`：注入 OpenAI 兼容客户端工厂并暴露补全服务状态。
- `app/web.py`：新增设置和任务历史页面路由。
- `app/templates/_settings_tabs.html`：增加“AI 补全”标签。
- `app/templates/model_library.html`：增加补全按钮、紧凑进度和能源来源列支持。
- `app/templates/citywide_models.html`、`app/templates/_model_variant_detail.html`：同步展示能源细分和来源。
- `app/static/citywide.js`、`app/static/citywide.css`：使用新的能源展示字段并为任务区域预留布局。
- `app/api_catalog.py`：登记设置和补全任务 API。
- `README.md`：增加中文配置、操作和故障恢复说明。

---

### Task 1: 数据库迁移与领域类型

**Files:**
- Create: `alembic/versions/0014_add_vehicle_energy_enrichment.py`
- Create: `app/enrichment/__init__.py`
- Create: `app/enrichment/domain.py`
- Modify: `app/models.py`
- Modify: `app/citywide/service.py`
- Test: `tests/test_enrichment_models.py`
- Test: `tests/test_citywide_service.py`

**Interfaces:**
- Produces: `EnergySubtype`、`EnergyConfidence`、`EnrichmentRunStatus`、`EnrichmentScope`、`EnrichmentResultStatus`、`EnrichmentStage` 枚举。
- Produces: `ModelPrompt`、`EnergySuggestion`、`TokenUsage` Pydantic 模型。
- Produces: `VehicleEnrichmentRun`、`VehicleEnrichmentResult` ORM 模型。
- Produces: `validate_energy_pair(energy_type: str, subtype: EnergySubtype) -> None`。

- [ ] **Step 1: 写领域枚举和一致性校验的失败测试**

```python
def test_energy_pair_rejects_cross_category_subtype():
    with pytest.raises(ValueError, match="能源大类与细分类型不一致"):
        validate_energy_pair("燃油", EnergySubtype.PURE_ELECTRIC)

def test_enrichment_models_keep_one_result_per_run_and_vehicle(session):
    model = VehicleModel(zuche_model_id=900001, name="测试车型")
    run = VehicleEnrichmentRun(scope=EnrichmentScope.PENDING_ONLY)
    session.add_all([model, run])
    session.flush()
    session.add_all([
        VehicleEnrichmentResult(run_id=run.id, vehicle_model_id=model.id),
        VehicleEnrichmentResult(run_id=run.id, vehicle_model_id=model.id),
    ])
    with pytest.raises(IntegrityError):
        session.flush()
```

- [ ] **Step 2: 运行测试并确认因类型和表不存在而失败**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_models.py`

Expected: FAIL，导入 `app.enrichment.domain` 或 ORM 类型失败。

- [ ] **Step 3: 定义领域类型和结构化建议**

```python
class EnergySubtype(StrEnum):
    GASOLINE = "汽油"
    DIESEL = "柴油"
    PURE_ELECTRIC = "纯电"
    PLUG_IN_HYBRID = "插电混动"
    RANGE_EXTENDER = "增程"
    HYBRID = "油电混动"
    OTHER = "其他"
    UNKNOWN = "未知"

class EnergyConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class EnergySuggestion(BaseModel):
    model_id: int
    energy_type: Literal["燃油", "新能源", "未知"]
    energy_subtype: EnergySubtype
    confidence: EnergyConfidence
    rationale: str = Field(min_length=1, max_length=1000)
    sources: list[str] = Field(default_factory=list, max_length=10)
```

`validate_energy_pair` 允许燃油对应汽油、柴油、油电混动、其他、未知；新能源对应纯电、插电混动、增程、其他、未知；未知只能对应未知。

- [ ] **Step 4: 增加 ORM 字段、任务表和结果表**

在 `VehicleModel` 增加 `energy_subtype`、`energy_confidence`、`energy_updated_at`。任务表使用 UUID 主键，结果表对 `(run_id, vehicle_model_id)` 建唯一约束，并对 `run_id`、`vehicle_model_id`、`status` 建索引。所有计数增加非负 CheckConstraint；活动任务增加 `status IN ('PENDING','RUNNING')` 的 PostgreSQL 和 SQLite 部分唯一索引。

- [ ] **Step 5: 写 Alembic 0014 升降级**

迁移必须先创建 PostgreSQL enum，再添加字段和表；降级反向删除索引、表、字段和 enum。不得改写现有 `energy_type` 与 `energy_source` 数据。

- [ ] **Step 6: 扩大扫描保护来源并运行测试**

```python
MANUAL_ENERGY_SOURCES = {"MANUAL", "USER", "人工", "AI_BATCH", "AI_FOCUSED"}
```

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_models.py tests/test_citywide_service.py`

Expected: PASS。

- [ ] **Step 7: 提交任务 1**

```bash
git add alembic/versions/0014_add_vehicle_energy_enrichment.py app/enrichment/__init__.py app/enrichment/domain.py app/models.py app/citywide/service.py tests/test_enrichment_models.py tests/test_citywide_service.py
git commit -m "feat: 增加车型能源补全数据模型"
```

---

### Task 2: OpenAI 兼容客户端与严格结果校验

**Files:**
- Create: `app/enrichment/client.py`
- Test: `tests/test_enrichment_client.py`

**Interfaces:**
- Consumes: `ModelPrompt`、`EnergySuggestion`、`TokenUsage`、`validate_energy_pair`。
- Produces: `OpenAICompatibleClient(api_key: str, base_url: str, model: str, timeout_seconds: int, max_retries: int)`。
- Produces: `async classify(models: list[ModelPrompt], focused: bool = False) -> CompletionBatch`。
- Produces: `async test_connection() -> ConnectionTestResult`。
- Produces: `EnrichmentClientError(public_message: str, retryable: bool)`，异常文本不含 API Key 或上游响应原文。

- [ ] **Step 1: 写成功请求和安全失败的测试**

```python
@pytest.mark.asyncio
async def test_client_posts_json_mode_and_returns_validated_suggestions(httpx_mock):
    httpx_mock.add_response(json={
        "choices": [{"message": {"content": json.dumps({"items": [{
            "model_id": 4952, "energy_type": "新能源", "energy_subtype": "插电混动",
            "confidence": "HIGH", "rationale": "车型公开命名明确", "sources": []
        }]}, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
    })
    result = await client.classify([ModelPrompt(model_id=4952, name="比亚迪海狮05", description="1.5T插电混动")])
    request = httpx_mock.get_request()
    assert request.url.path.endswith("/chat/completions")
    assert json.loads(request.content)["response_format"] == {"type": "json_object"}
    assert result.usage.total_tokens == 110

@pytest.mark.asyncio
async def test_client_error_never_exposes_secret(httpx_mock):
    httpx_mock.add_response(status_code=401, text="Bearer sk-private-secret invalid")
    with pytest.raises(EnrichmentClientError) as error:
        await client.classify([prompt])
    assert "sk-private-secret" not in str(error.value)
```

- [ ] **Step 2: 运行测试并确认因客户端不存在而失败**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_client.py`

Expected: FAIL，无法导入 `OpenAICompatibleClient`。

- [ ] **Step 3: 实现请求、提示词和响应解析**

请求体固定包含 `model`、`messages`、`response_format={"type":"json_object"}`、`temperature=0` 和足够的 `max_tokens`。系统提示必须列出完整枚举、能源配对规则、输入输出 JSON 示例，并要求不知道时返回未知，不得猜测。

```python
async def classify(self, models: list[ModelPrompt], focused: bool = False) -> CompletionBatch:
    response = await self._post({
        "model": self.model,
        "messages": build_messages(models, focused),
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": max(1200, len(models) * 220),
    })
    return parse_completion(response, expected_ids={item.model_id for item in models})
```

- [ ] **Step 4: 增加空内容、非法 JSON、重复 ID、未知 ID、遗漏 ID、非法枚举和大类冲突测试**

每个输入使用独立测试，错误均抛出 `EnrichmentClientError`；HTTP 408、429 和 5xx 标记 `retryable=True`，400、401、403、404 标记为不可重试。

- [ ] **Step 5: 实现最小测试连接**

`test_connection()` 发送一辆固定示例车，要求返回 `新能源/纯电/HIGH`，并返回 `ok`、`elapsed_ms`、`model` 和 Token 用量，不返回响应原文。

- [ ] **Step 6: 运行客户端测试**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_client.py`

Expected: PASS。

- [ ] **Step 7: 提交任务 2**

```bash
git add app/enrichment/client.py tests/test_enrichment_client.py
git commit -m "feat: 增加 OpenAI 兼容车型分类客户端"
```

---

### Task 3: AI 补全设置接口与页面

**Files:**
- Modify: `app/integrations/service.py`
- Modify: `app/api/settings.py`
- Modify: `app/main.py`
- Modify: `app/web.py`
- Modify: `app/templates/_settings_tabs.html`
- Create: `app/templates/settings_ai_enrichment.html`
- Create: `app/static/enrichment.js`
- Create: `app/static/enrichment.css`
- Test: `tests/test_settings_api.py`
- Test: `tests/test_enrichment_web.py`

**Interfaces:**
- Consumes: `IntegrationSettingsRepository` 和 `OpenAICompatibleClient`。
- Produces: `serialize_ai_enrichment_setting(item) -> dict`。
- Produces: `save_ai_enrichment(enabled, api_key, base_url, model, batch_size, timeout_seconds, max_retries) -> dict`。
- Produces: `async test_ai_enrichment(client_factory) -> dict`。
- Produces HTTP: `PUT /api/settings/integrations/openai-compatible`、`DELETE .../secret`、`POST .../test`。

- [ ] **Step 1: 写配置脱敏、验证和页面控件的失败测试**

```python
@pytest.mark.asyncio
async def test_ai_setting_round_trip_masks_api_key(client):
    response = await client.put("/api/settings/integrations/openai-compatible", json={
        "enabled": True, "api_key": "sk-1234567890", "base_url": "https://api.deepseek.com",
        "model": "custom-model", "batch_size": 15, "timeout_seconds": 90, "max_retries": 2,
    })
    assert response.status_code == 200
    assert response.json()["masked_secret"] == "sk-1****7890"
    assert "sk-1234567890" not in response.text
```

页面测试断言 `/settings/ai-enrichment` 包含配置表单、API 测试、脱敏状态和清除密钥按钮。

- [ ] **Step 2: 运行测试并确认路由不存在**

Run: `.venv312/bin/python -m pytest -q tests/test_settings_api.py tests/test_enrichment_web.py`

Expected: FAIL，AI 设置接口或页面返回 404。

- [ ] **Step 3: 扩展集成设置服务**

`list_integrations()` 同时返回 `baidu_maps` 与 `openai_compatible_enrichment`，现有百度配置顺序保持第一项以避免旧页面回归。保存时规范化 Base URL：去除尾部 `/`，只允许 `http://` 或 `https://`；启用时必须已有密钥且模型非空。

- [ ] **Step 4: 增加严格请求模型和设置 API**

```python
class AIEnrichmentSettingsInput(BaseModel):
    enabled: bool = False
    api_key: str | None = Field(default=None, max_length=512)
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=128)
    batch_size: int = Field(default=15, ge=1, le=30)
    timeout_seconds: int = Field(default=90, ge=10, le=300)
    max_retries: int = Field(default=2, ge=0, le=5)
```

测试接口从数据库读取配置，通过注入工厂创建客户端；捕获 `EnrichmentClientError` 并返回安全中文 502。

- [ ] **Step 5: 增加设置页面和前端交互**

设置标签增加“AI 补全”；表单使用原生输入，API Key 留空表示保留旧值。JS 依据 `provider` 查找配置，不能再依赖 `items[0]`。测试结果显示耗时、模型和 Token，不展示提示词或响应原文。

- [ ] **Step 6: 运行设置与页面测试**

Run: `.venv312/bin/python -m pytest -q tests/test_settings_api.py tests/test_enrichment_web.py tests/test_rebuild_web.py`

Expected: PASS。

- [ ] **Step 7: 提交任务 3**

```bash
git add app/integrations/service.py app/api/settings.py app/main.py app/web.py app/templates/_settings_tabs.html app/templates/settings_ai_enrichment.html app/static/enrichment.js app/static/enrichment.css tests/test_settings_api.py tests/test_enrichment_web.py tests/test_rebuild_web.py
git commit -m "feat: 增加 AI 补全接口配置"
```

---

### Task 4: 任务仓储与状态机

**Files:**
- Create: `app/enrichment/repository.py`
- Create: `app/enrichment/service.py`
- Test: `tests/test_enrichment_repository.py`
- Test: `tests/test_enrichment_service.py`

**Interfaces:**
- Consumes: Task 1 ORM/枚举和 Task 2 `OpenAICompatibleClient`。
- Produces: `EnrichmentRepository.create_run(scope, model_name, endpoint_label) -> VehicleEnrichmentRun`。
- Produces: `claim_work(run_id) -> ClaimedWork | None`，`ClaimedWork(stage, result_ids, prompts, claim_token)`。
- Produces: `save_suggestions(claimed, completion, source) -> None`、`fail_claim(claimed, message, retryable) -> None`。
- Produces: `VehicleEnrichmentService.create_run(scope)`, `process_active_run()`, `stop()`, `resume()`, `retry_failed()`, `interrupt_stale_runs()`。

- [ ] **Step 1: 写默认范围、全量范围和活动任务互斥测试**

```python
def test_pending_scope_selects_unknown_low_confidence_or_missing_subtype(session):
    run = repository.create_run(EnrichmentScope.PENDING_ONLY, "model-x", "api.example")
    selected = session.scalars(select(VehicleEnrichmentResult).where(
        VehicleEnrichmentResult.run_id == run.id)).all()
    assert {item.vehicle_model_id for item in selected} == {unknown.id, low.id, missing_subtype.id}

def test_create_run_returns_existing_active_run(session):
    first = repository.create_run(EnrichmentScope.PENDING_ONLY, "model-x", "api.example")
    second = repository.create_run(EnrichmentScope.ALL, "model-x", "api.example")
    assert second.id == first.id
```

- [ ] **Step 2: 运行仓储测试并确认失败**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_repository.py`

Expected: FAIL，仓储不存在。

- [ ] **Step 3: 实现任务创建、认领和聚合统计**

任务创建一次性写入候选结果行。认领使用 `SELECT ... FOR UPDATE SKIP LOCKED`（SQLite 测试退化为普通事务锁），批量阶段最多取配置批量数，复核阶段一次取一条。认领写入随机 `claim_token`，只有持有相同 token 的保存操作能提交。

- [ ] **Step 4: 写两阶段应用规则的失败测试**

```python
@pytest.mark.asyncio
async def test_high_batch_applies_and_medium_moves_to_focused(engine):
    await service.process_active_run()
    with Session(engine) as session:
        high = session.get(VehicleModel, high_model.id)
        medium_result = session.scalar(select(VehicleEnrichmentResult).where(
            VehicleEnrichmentResult.vehicle_model_id == medium_model.id))
        assert high.energy_source == "AI_BATCH"
        assert medium_result.stage == EnrichmentStage.FOCUSED
        assert medium_result.status == EnrichmentResultStatus.PENDING
```

另写人工来源不覆盖、复核高置信应用、复核不确定保持未知、非法响应整批失败、重试次数和 Token 聚合测试。

- [ ] **Step 5: 实现服务处理和优先级**

`process_active_run()` 每次只认领一个工作单元；批量阶段调用 `classify(prompts, focused=False)`，复核阶段调用 `classify([prompt], focused=True)`。写车型时再次在事务内检查当前来源，避免任务运行期间人工值被旧结果覆盖。

- [ ] **Step 6: 实现停止、继续、失败重试和中断恢复**

允许 `RUNNING -> STOPPED`、`STOPPED/INTERRUPTED -> RUNNING`；只有 `PARTIAL` 可重试失败项。启动恢复把遗留 `RUNNING` 任务和结果改为 `INTERRUPTED/PENDING`，保留已应用记录。

- [ ] **Step 7: 运行仓储与服务测试**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_repository.py tests/test_enrichment_service.py tests/test_citywide_service.py`

Expected: PASS。

- [ ] **Step 8: 提交任务 4**

```bash
git add app/enrichment/repository.py app/enrichment/service.py tests/test_enrichment_repository.py tests/test_enrichment_service.py tests/test_citywide_service.py
git commit -m "feat: 实现车型能源补全任务"
```

---

### Task 5: 任务 API 与运行时推进

**Files:**
- Create: `app/api/enrichment.py`
- Modify: `app/api/router.py`
- Modify: `app/scheduling.py`
- Modify: `app/main.py`
- Test: `tests/test_enrichment_api.py`
- Test: `tests/test_rebuild_scheduling_maintenance.py`

**Interfaces:**
- Consumes: `VehicleEnrichmentService`。
- Produces: `/api/vehicle-enrichment-runs` 列表与创建接口，以及 `{run_id}` 详情、停止、继续、重试接口。
- Produces: `RadarRuntime.vehicle_enrichment_service` 和每两秒执行的 `vehicle-enrichment` 作业。

- [ ] **Step 1: 写 API 状态、统计和安全错误测试**

```python
@pytest.mark.asyncio
async def test_create_and_read_enrichment_run(client):
    created = await client.post("/api/vehicle-enrichment-runs", json={"scope": "PENDING_ONLY"})
    assert created.status_code == 201
    run = created.json()
    assert run["status_label"] == "等待中"
    assert run["total_count"] >= 1
    detail = await client.get(f"/api/vehicle-enrichment-runs/{run['id']}")
    assert detail.json()["id"] == run["id"]
```

还需覆盖未配置返回 422、上游失败不泄露密钥、非法状态返回 409、分页和失败车型摘要。

- [ ] **Step 2: 运行 API 测试并确认 404**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_api.py`

Expected: FAIL，新接口返回 404。

- [ ] **Step 3: 实现严格 API 请求和序列化**

创建请求仅接受 `scope: Literal["PENDING_ONLY", "ALL"]`，禁止额外字段。序列化输出包含全部计数、请求数、Token、时间、阶段和安全错误摘要；详情附带最多 100 条失败或未知结果摘要。

- [ ] **Step 4: 注册运行时服务与调度作业**

`RadarRuntime` 从 `IntegrationSettingsRepository` 动态读取每次任务所需配置，不在启动时缓存密钥。新增作业：

```python
self.scheduler.add_job(
    self._run_vehicle_enrichment,
    IntervalTrigger(seconds=2, jitter=1),
    id="vehicle-enrichment",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
```

启动时调用 `interrupt_stale_runs()`，关闭时沿用调度器现有关闭路径。

- [ ] **Step 5: 运行 API 与调度测试**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_api.py tests/test_rebuild_scheduling_maintenance.py tests/test_routes.py`

Expected: PASS。

- [ ] **Step 6: 提交任务 5**

```bash
git add app/api/enrichment.py app/api/router.py app/scheduling.py app/main.py tests/test_enrichment_api.py tests/test_rebuild_scheduling_maintenance.py tests/test_routes.py
git commit -m "feat: 接入车型补全任务 API"
```

---

### Task 6: 车型库入口、进度与历史页面

**Files:**
- Modify: `app/web.py`
- Modify: `app/templates/model_library.html`
- Create: `app/templates/enrichment_runs.html`
- Modify: `app/static/enrichment.js`
- Modify: `app/static/enrichment.css`
- Modify: `app/static/citywide.js`
- Modify: `app/static/citywide.css`
- Test: `tests/test_enrichment_web.py`
- Test: `tests/test_citywide_web.py`

**Interfaces:**
- Consumes: Task 5 的任务 HTTP API。
- Produces: `/library/ai-runs` 页面。
- Produces: 车型库“AI 补全车型信息”按钮、全量二级操作、进度控制和历史入口。

- [ ] **Step 1: 写车型库入口与历史页面失败测试**

```python
@pytest.mark.asyncio
async def test_library_exposes_ai_enrichment_without_cluttering_filters(client):
    library = await client.get("/library")
    history = await client.get("/library/ai-runs")
    assert 'id="start-ai-enrichment"' in library.text
    assert 'id="ai-enrichment-progress"' in library.text
    assert "重新识别全部车型" in library.text
    assert 'id="ai-enrichment-run-list"' in history.text
```

- [ ] **Step 2: 运行页面测试并确认入口缺失**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_web.py tests/test_citywide_web.py`

Expected: FAIL，入口和历史页面不存在。

- [ ] **Step 3: 实现车型库任务入口和紧凑进度**

默认按钮 POST `PENDING_ONLY`；全量操作使用原生确认框后 POST `ALL`。进度每三秒刷新活动任务，页面隐藏时停止轮询，恢复可见时立即刷新。停止、继续和重试按钮只在允许状态显示，按钮提交期间禁用。

- [ ] **Step 4: 实现任务历史分页**

历史页每页 20 条，显示模型、范围、状态、进度、更新/未知/失败、请求数和 Token。展开详情只请求当前任务，显示失败和未知车型及安全错误摘要。

- [ ] **Step 5: 完成响应式样式和可访问性**

桌面进度指标最多六列，820px 以下三列，600px 以下两列；所有动态区域使用 `aria-live="polite"`，停止和重试按钮保留至少 44px 触控高度，并遵守 `prefers-reduced-motion`。

- [ ] **Step 6: 运行页面测试**

Run: `.venv312/bin/python -m pytest -q tests/test_enrichment_web.py tests/test_citywide_web.py tests/test_rebuild_web.py`

Expected: PASS。

- [ ] **Step 7: 提交任务 6**

```bash
git add app/web.py app/templates/model_library.html app/templates/enrichment_runs.html app/static/enrichment.js app/static/enrichment.css app/static/citywide.js app/static/citywide.css tests/test_enrichment_web.py tests/test_citywide_web.py tests/test_rebuild_web.py
git commit -m "feat: 增加车型库 AI 补全工作区"
```

---

### Task 7: 两个车型页面同步能源展示

**Files:**
- Modify: `app/citywide/catalog.py`
- Modify: `app/templates/_model_variant_detail.html`
- Modify: `app/static/citywide.js`
- Modify: `app/static/citywide.css`
- Test: `tests/test_citywide_catalog.py`
- Test: `tests/test_citywide_web.py`

**Interfaces:**
- Consumes: `VehicleModel.energy_subtype`、`energy_source`、`energy_confidence`、`energy_updated_at`。
- Produces: `/api/model-library` 与 `/api/citywide-models` 每项新增 `energy_subtype`、`energy_source`、`energy_confidence`、`energy_updated_at`。

- [ ] **Step 1: 写同步序列化和页面展示失败测试**

```python
def test_library_and_citywide_share_enriched_energy_fields(session):
    model.energy_type = "新能源"
    model.energy_subtype = "增程"
    model.energy_source = "AI_FOCUSED"
    model.energy_confidence = "HIGH"
    library_item = catalog.library(ModelLibraryFilters())["items"][0]
    citywide_item = catalog.search(CitywideModelFilters(run_id=run.id))["items"][0]
    for item in (library_item, citywide_item):
        assert item["energy_subtype"] == "增程"
        assert item["energy_source"] == "AI_FOCUSED"
```

- [ ] **Step 2: 运行测试并确认字段缺失**

Run: `.venv312/bin/python -m pytest -q tests/test_citywide_catalog.py tests/test_citywide_web.py`

Expected: FAIL，响应中没有细分和来源字段。

- [ ] **Step 3: 扩展统一序列化**

`_serialize_model` 返回四个新增字段；缺失值保持 `null`，不伪造默认结论。API 筛选继续按能源大类执行，不在本期增加细分筛选。

- [ ] **Step 4: 更新两个列表的展示函数**

新增纯函数：

```javascript
const energyLabel=item=>item.energy_type
  ? `${item.energy_type}${item.energy_subtype&&item.energy_subtype!=='未知'?` · ${item.energy_subtype}`:''}`
  : '能源未知';
const energySourceLabel={AI_FOCUSED:'AI复核',AI_BATCH:'AI判断',UPSTREAM:'神州推导',MANUAL:'人工确认'};
```

车型库能源列显示结论和弱化来源；全城车型摘要显示结论，展开详情显示来源、置信度和更新时间。

- [ ] **Step 5: 运行目录和页面测试**

Run: `.venv312/bin/python -m pytest -q tests/test_citywide_catalog.py tests/test_citywide_web.py tests/test_citywide_api.py`

Expected: PASS。

- [ ] **Step 6: 提交任务 7**

```bash
git add app/citywide/catalog.py app/templates/_model_variant_detail.html app/static/citywide.js app/static/citywide.css tests/test_citywide_catalog.py tests/test_citywide_web.py tests/test_citywide_api.py
git commit -m "feat: 同步展示 AI 能源结论"
```

---

### Task 8: API 台账、中文文档与端到端验收

**Files:**
- Modify: `app/api_catalog.py`
- Modify: `README.md`
- Modify: `tests/test_settings_api.py`
- Modify: `tests/test_rebuild_acceptance.py`

**Interfaces:**
- Consumes: Tasks 1–7 的最终 HTTP 接口和页面。
- Produces: 设置页 API 台账条目、中文运维文档和完整 Docker 验收证据。

- [ ] **Step 1: 写 API 台账与验收失败测试**

```python
def test_api_catalog_describes_ai_enrichment_endpoints(engine):
    catalog = build_api_catalog(app_for(engine).openapi(), "http://服务器局域网IP:8093")
    items = {(item["method"], item["path"]): item
             for group in catalog["groups"] for item in group["items"]}
    assert ("POST", "/api/vehicle-enrichment-runs") in items
    assert "API Key" not in items[("POST", "/api/vehicle-enrichment-runs")]["example"]
```

验收测试创建配置、测试连接、创建任务、推进批量与复核、读取车型库同步字段，并验证响应中不含测试密钥。

- [ ] **Step 2: 运行测试并确认台账缺失**

Run: `.venv312/bin/python -m pytest -q tests/test_settings_api.py tests/test_rebuild_acceptance.py`

Expected: FAIL，API 台账没有补全接口。

- [ ] **Step 3: 补全 API 台账和 README**

README 增加：OpenAI 兼容配置、API 测试、默认补全、全量重识别、停止/继续、失败重试、Token 查看、Docker 重启恢复、密钥清除和“未知不猜测”说明。文档不得包含真实密钥、内部上游响应或个人信息。

- [ ] **Step 4: 运行所有测试和迁移检查**

```bash
git diff --check
.venv312/bin/python -m pytest -q
docker compose build app
```

Expected: 全部退出码为 0，无失败、错误或警告。

- [ ] **Step 5: 部署并验证真实容器**

```bash
docker compose up -d --build app
docker compose ps
curl -fsS http://127.0.0.1:8093/healthz
curl -fsS http://127.0.0.1:8093/settings/ai-enrichment
curl -fsS http://127.0.0.1:8093/library
curl -fsS http://127.0.0.1:8093/library/ai-runs
```

Expected: 应用和数据库均为 healthy；健康接口返回 `status=ok`；三个页面均返回 200。

- [ ] **Step 6: 提交任务 8**

```bash
git add app/api_catalog.py README.md tests/test_settings_api.py tests/test_rebuild_acceptance.py
git commit -m "docs: 完善 AI 补全验收与说明"
```

- [ ] **Step 7: 最终检查并推送**

```bash
git status --short --branch
git log --oneline -8
git push -u origin HEAD
```

Expected: 只有既有未跟踪 `.superpowers/` 目录，不包含未提交的任务文件；分支推送到 `origin/codex/zuche-radar-rebuild`，不创建或合并 PR。
