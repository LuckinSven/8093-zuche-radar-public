# 系统设置与百度地图 API 配置实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为神州车型雷达增加可扩展的系统设置页面，完整支持百度地图服务端 AK 的本机保存、遮罩管理以及地点提示和坐标转换双服务验证。

**Architecture:** 使用 PostgreSQL `integration_settings` 表保存提供商配置，代码注册固定的 `baidu_maps` 提供商。独立百度客户端封装外部 HTTP，设置服务负责业务校验和脱敏，FastAPI 暴露固定配置与测试接口，Jinja 页面提供非技术化操作界面。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL 16/JSONB、Alembic、httpx、Pydantic 2、Jinja2、原生 JavaScript、pytest。

**Spec:** `docs/superpowers/specs/2026-08-31-system-settings-baidu-api-design.md`

## Global Constraints

- 所有新增页面文案、API 错误和文档使用中文。
- 系统继续无鉴权并只面向可信局域网，不新增登录系统。
- 完整 AK 只能进入请求内存和本机 PostgreSQL，不得进入 Git、日志、响应或测试快照。
- 外部请求 URL 固定在代码中，数据库不得配置任意 URL。
- 百度客户端超时为 10 秒，地点提示和坐标转换分别报告结果。
- 新行为必须先写失败测试并确认按预期失败，再写最小实现。
- Docker 端口保持 `8093`，数据库继续使用持久卷。

---

### Task 1: 集成配置持久化

**Files:**
- Create: `alembic/versions/0007_add_integration_settings.py`
- Create: `app/integrations/__init__.py`
- Create: `app/integrations/repository.py`
- Modify: `app/models.py`
- Create: `tests/test_integration_settings_repository.py`

**Interfaces:**
- Consumes: SQLAlchemy `Session` 和现有 `Base`。
- Produces: `IntegrationSetting` ORM 模型；`IntegrationSettingsRepository.get(provider)`、`save(provider, enabled, config, secret_value)`、`clear_secret(provider)`。

- [ ] **Step 1: 写持久化失败测试**

```python
def test_baidu_setting_can_preserve_and_clear_secret(session):
    repository = IntegrationSettingsRepository(session)
    item = repository.save("baidu_maps", True,
        {"default_region": "广州", "test_keyword": "三溪地铁站"}, "secret-ak")
    session.commit()

    preserved = repository.save("baidu_maps", False,
        {"default_region": "深圳", "test_keyword": "福田站"}, None)
    assert preserved.secret_value == "secret-ak"
    assert preserved.config_json["default_region"] == "深圳"

    repository.clear_secret("baidu_maps")
    assert repository.get("baidu_maps").secret_value is None
    assert repository.get("baidu_maps").enabled is False
```

- [ ] **Step 2: 运行测试并确认失败原因**

Run: `.venv312/bin/python -m pytest -q tests/test_integration_settings_repository.py`

Expected: FAIL，提示 `app.integrations.repository` 或 `IntegrationSetting` 尚不存在。

- [ ] **Step 3: 添加 ORM 模型与显式迁移**

```python
class IntegrationSetting(Base):
    __tablename__ = "integration_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

迁移 `0007_add_integration_settings` 使用 `op.create_table` 和唯一索引，不导入当前 ORM 模型，不写入 AK。冻结的 v1 迁移元数据保持历史结构，不加入新表。

- [ ] **Step 4: 实现仓储的保留和清除语义**

```python
class IntegrationSettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, provider: str) -> IntegrationSetting | None:
        return self.session.scalar(select(IntegrationSetting).where(
            IntegrationSetting.provider == provider))

    def save(self, provider: str, enabled: bool, config: dict,
             secret_value: str | None) -> IntegrationSetting:
        item = self.get(provider)
        if item is None:
            item = IntegrationSetting(provider=provider)
            self.session.add(item)
        item.enabled = enabled
        item.config_json = config
        if secret_value is not None:
            item.secret_value = secret_value
        self.session.flush()
        return item

    def clear_secret(self, provider: str) -> None:
        item = self.get(provider)
        if item is not None:
            item.secret_value = None
            item.enabled = False
            self.session.flush()
```

`secret_value is None` 表示保留原密钥；新记录允许先保存停用状态和空密钥。

- [ ] **Step 5: 运行持久化测试与迁移检查**

Run: `.venv312/bin/python -m pytest -q tests/test_integration_settings_repository.py`

Expected: PASS。

Run: `.venv312/bin/alembic heads`

Expected: 只有 `0007_add_integration_settings (head)`。

- [ ] **Step 6: 提交持久化任务**

```bash
git add alembic/versions/0007_add_integration_settings.py app/models.py app/integrations/__init__.py app/integrations/repository.py tests/test_integration_settings_repository.py
git commit -m "feat: add integration settings persistence"
```

---

### Task 2: 百度地图客户端

**Files:**
- Create: `app/integrations/baidu_maps.py`
- Create: `tests/test_baidu_maps_client.py`

**Interfaces:**
- Consumes: 百度服务端 AK；可选 `httpx.AsyncBaseTransport`。
- Produces: `BaiduPlace`、`Gcj02Coordinate`、`BaiduMapsError`；`BaiduMapsClient.suggest(region, keyword)`；`BaiduMapsClient.convert_bd09_to_gcj02(latitude, longitude)`。

- [ ] **Step 1: 写地点提示和坐标转换成功失败测试**

```python
@pytest.mark.asyncio
async def test_baidu_client_returns_place_and_gcj02_coordinate():
    transport = httpx.MockTransport(fake_baidu_success)
    async with BaiduMapsClient("test-ak", transport=transport) as client:
        places = await client.suggest("广州", "三溪地铁站")
        coordinate = await client.convert_bd09_to_gcj02(
            places[0].latitude, places[0].longitude)
    assert places[0].name == "三溪地铁站"
    assert coordinate.longitude == 113.416

@pytest.mark.asyncio
async def test_baidu_client_turns_business_error_into_safe_chinese_error():
    transport = httpx.MockTransport(fake_invalid_ak)
    async with BaiduMapsClient("must-not-leak", transport=transport) as client:
        with pytest.raises(BaiduMapsError, match="百度地图验证失败") as captured:
            await client.suggest("广州", "三溪地铁站")
    assert "must-not-leak" not in str(captured.value)
```

完整假响应包含百度的 `status`、`message`、`result[].name/address/location.lat/location.lng` 以及坐标转换 `result[].x/y`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv312/bin/python -m pytest -q tests/test_baidu_maps_client.py`

Expected: FAIL，提示 `app.integrations.baidu_maps` 尚不存在。

- [ ] **Step 3: 实现固定端点和安全错误映射**

```python
class BaiduMapsClient:
    def __init__(self, ak: str, transport=None) -> None:
        self.ak = ak
        self.client = httpx.AsyncClient(base_url="https://api.map.baidu.com",
            timeout=10, transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def suggest(self, region: str, keyword: str) -> list[BaiduPlace]:
        data = await self._get("/place/v3/suggestion", {
            "query": keyword, "region": region, "region_limit": "true"})
        results = data.get("result")
        if not isinstance(results, list):
            raise BaiduMapsError("百度地点提示响应格式异常")
        places = [BaiduPlace(name=x["name"], address=x.get("address") or "",
            latitude=x["location"]["lat"], longitude=x["location"]["lng"])
            for x in results if isinstance(x, dict) and isinstance(x.get("location"), dict)]
        if not places:
            raise BaiduMapsError("百度地图未找到匹配地点")
        return places

    async def convert_bd09_to_gcj02(self, latitude: float,
                                    longitude: float) -> Gcj02Coordinate:
        data = await self._get("/geoconv/v2/", {
            "coords": f"{longitude},{latitude}", "model": 5})
        results = data.get("result")
        if not isinstance(results, list) or not results:
            raise BaiduMapsError("百度坐标转换响应格式异常")
        return Gcj02Coordinate(latitude=results[0]["y"], longitude=results[0]["x"])

    async def _get(self, path: str, params: dict) -> dict:
        try:
            response = await self.client.get(path, params=params | {"ak": self.ak})
            response.raise_for_status()
            data = response.json()
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError,
                ValueError) as error:
            raise BaiduMapsError("百度地图网络请求失败") from error
        if not isinstance(data, dict) or data.get("status") != 0:
            raise BaiduMapsError("百度地图验证失败")
        return data
```

地点无结果抛出“百度地图未找到匹配地点”；超时、传输错误、非 2xx、非零业务状态和字段缺失分别映射为不含 AK 的中文 `BaiduMapsError`。

- [ ] **Step 4: 运行客户端测试**

Run: `.venv312/bin/python -m pytest -q tests/test_baidu_maps_client.py`

Expected: PASS，且覆盖成功、无结果、业务错误、HTTP 错误、超时和畸形响应。

- [ ] **Step 5: 提交客户端任务**

```bash
git add app/integrations/baidu_maps.py tests/test_baidu_maps_client.py
git commit -m "feat: add baidu maps service client"
```

---

### Task 3: 设置服务与 API

**Files:**
- Create: `app/integrations/service.py`
- Create: `app/api/settings.py`
- Modify: `app/api/router.py`
- Modify: `app/main.py`
- Create: `tests/test_settings_api.py`

**Interfaces:**
- Consumes: `IntegrationSettingsRepository`、`BaiduMapsClient` 和 FastAPI 会话工厂。
- Produces: `GET /api/settings/integrations`、`PUT /api/settings/integrations/baidu_maps`、`DELETE /api/settings/integrations/baidu_maps/secret`、`POST /api/settings/integrations/baidu_maps/test`。

- [ ] **Step 1: 写安全读取和更新失败测试**

```python
@pytest.mark.asyncio
async def test_settings_api_masks_secret_and_blank_update_preserves_it(client):
    saved = await client.put("/api/settings/integrations/baidu_maps", json={
        "enabled": True, "ak": "abcdef123456", "default_region": "广州",
        "test_keyword": "三溪地铁站"})
    assert saved.json()["masked_secret"] == "abcd****3456"
    assert "abcdef123456" not in saved.text

    updated = await client.put("/api/settings/integrations/baidu_maps", json={
        "enabled": False, "ak": "", "default_region": "深圳",
        "test_keyword": "福田站"})
    assert updated.json()["configured"] is True
```

- [ ] **Step 2: 写测试连接和清除失败测试**

```python
@pytest.mark.asyncio
async def test_settings_test_returns_two_safe_steps(client_with_fake_baidu):
    response = await client_with_fake_baidu.post(
        "/api/settings/integrations/baidu_maps/test")
    assert response.json()["place_suggestion"]["name"] == "三溪地铁站"
    assert response.json()["coordinate_conversion"]["gcj02_longitude"] == 113.416
    assert "secret-ak" not in response.text

@pytest.mark.asyncio
async def test_clear_secret_disables_integration(client):
    response = await client.delete(
        "/api/settings/integrations/baidu_maps/secret")
    assert response.status_code == 204
```

- [ ] **Step 3: 运行 API 测试并确认失败**

Run: `.venv312/bin/python -m pytest -q tests/test_settings_api.py`

Expected: FAIL，设置路由返回 404。

- [ ] **Step 4: 实现服务校验、遮罩和测试编排**

```python
def mask_secret(value: str | None) -> str | None:
    return f"{value[:4]}****{value[-4:]}" if value else None

def serialize_baidu_setting(item: IntegrationSetting | None) -> dict:
    config = item.config_json if item else {}
    return {
        "provider": "baidu_maps",
        "display_name": "百度地图",
        "enabled": bool(item and item.enabled),
        "configured": bool(item and item.secret_value),
        "masked_secret": mask_secret(item.secret_value if item else None),
        "default_region": config.get("default_region", "广州"),
        "test_keyword": config.get("test_keyword", "三溪地铁站"),
        "updated_at": item.updated_at if item else None,
    }
```

`IntegrationSettingsService.save_baidu()` 在 `payload.ak` 是非空白字符串时去除首尾空白并替换密钥，否则向仓储传 `None` 保留原值；保存前以“新 AK 或旧 AK”判断启用是否合法。`test_baidu()` 读取已启用配置，以 `time.perf_counter()` 计算 `elapsed_ms`，依次调用 `suggest()` 和 `convert_bd09_to_gcj02()` 并组装设计文档中的固定响应。启用但无密钥、测试时未配置或停用返回 422；外部错误返回 502。

- [ ] **Step 5: 注册固定 API 路由与测试客户端工厂**

生产默认工厂为 `lambda ak: BaiduMapsClient(ak)`；测试可通过 `app.state.baidu_client_factory` 注入固定传输客户端。API 不接受 URL 或提供商路径变量。

- [ ] **Step 6: 运行 API 和相关回归测试**

Run: `.venv312/bin/python -m pytest -q tests/test_settings_api.py tests/test_rebuild_api.py`

Expected: PASS。

- [ ] **Step 7: 提交设置 API 任务**

```bash
git add app/integrations/service.py app/api/settings.py app/api/router.py app/main.py tests/test_settings_api.py
git commit -m "feat: add integration settings api"
```

---

### Task 4: 系统设置页面

**Files:**
- Create: `app/templates/settings.html`
- Modify: `app/templates/base.html`
- Modify: `app/web.py`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `tests/test_rebuild_web.py`

**Interfaces:**
- Consumes: Task 3 的四个设置 API。
- Produces: `/settings` 页面、主导航“设置”、百度配置表单和两步测试结果。

- [ ] **Step 1: 写页面行为失败测试**

```python
@pytest.mark.asyncio
async def test_settings_page_has_masked_api_configuration_workspace():
    response = await client.get("/settings")
    assert response.status_code == 200
    assert 'data-page="settings"' in response.text
    assert 'id="baidu-settings-form"' in response.text
    assert 'id="test-baidu"' in response.text
    assert 'id="baidu-test-result"' in response.text
    assert 'type="password"' in response.text
```

- [ ] **Step 2: 运行页面测试并确认失败**

Run: `.venv312/bin/python -m pytest -q tests/test_rebuild_web.py::test_settings_page_has_masked_api_configuration_workspace`

Expected: FAIL，`/settings` 返回 404。

- [ ] **Step 3: 实现路由、模板和导航**

模板包含状态徽章、AK 密码输入、掩码提示、默认城市、测试关键词、启用开关、保存、测试、清除按钮，以及“仅限可信局域网”的安全提示。

- [ ] **Step 4: 实现前端加载、保存、测试和清除**

```javascript
async function initSettings(){
  const form=$('#baidu-settings-form'),result=$('#baidu-test-result');
  const load=async()=>{
    const data=await api('/api/settings/integrations');
    const item=data.items.find(x=>x.provider==='baidu_maps');
    form.elements.enabled.checked=item.enabled;
    form.elements.default_region.value=item.default_region;
    form.elements.test_keyword.value=item.test_keyword;
    $('#baidu-secret-status').textContent=item.masked_secret||'尚未配置';
  };
  form.onsubmit=async event=>{
    event.preventDefault();
    const values=formBody(form);
    values.enabled=form.elements.enabled.checked;
    await api('/api/settings/integrations/baidu_maps',json('PUT',values));
    form.elements.ak.value='';
    await load();
  };
  $('#test-baidu').onclick=async()=>{
    const data=await api('/api/settings/integrations/baidu_maps/test',{method:'POST'});
    result.innerHTML=`<b>验证成功</b><p>${esc(data.place_suggestion.name)}</p>`;
  };
  $('#clear-baidu-secret').onclick=async()=>{
    if(!confirm('确定清除百度地图 AK？'))return;
    await api('/api/settings/integrations/baidu_maps/secret',{method:'DELETE'});
    await load();
  };
  await load();
}
```

所有百度返回的名称、地址和错误在插入 HTML 前使用现有 `esc()`；按钮请求期间禁用，完成后恢复。

- [ ] **Step 5: 运行页面与 XSS 回归测试**

Run: `.venv312/bin/python -m pytest -q tests/test_rebuild_web.py tests/test_settings_api.py`

Expected: PASS。

- [ ] **Step 6: 提交设置页面任务**

```bash
git add app/templates/settings.html app/templates/base.html app/web.py app/static/app.js app/static/app.css tests/test_rebuild_web.py
git commit -m "feat: add system settings page"
```

---

### Task 5: 文档、真实配置与部署验收

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1-4 的完整设置子系统。
- Produces: 中文运维说明、真实百度测试证据、迁移到 `0007` 的 healthy Docker 部署。

- [ ] **Step 1: 更新中文 README**

说明设置页入口、AK 仅保存于本机数据库、测试按钮的两个步骤、数据库备份含密钥、不要将 8093 暴露公网，以及清除密钥操作。

- [ ] **Step 2: 运行完整自动化和迁移验证**

Run: `.venv312/bin/python -m pytest -q`

Expected: 全部 PASS。

Run: `.venv312/bin/alembic heads && docker compose config -q && git diff --check`

Expected: 单一 `0007` head、Compose 配置有效、无空白错误。

在独立临时 PostgreSQL 数据库从零执行 `alembic upgrade head`，确认 `integration_settings` 存在且广州种子城市仍存在，然后删除该临时数据库。

- [ ] **Step 3: 重建 Docker 并检查健康状态**

Run: `docker compose up -d --build`

Expected: `app` 与 `db` 均为 healthy，`/healthz` 返回 200，生产数据库版本为 `0007_add_integration_settings`。

- [ ] **Step 4: 仅在本机配置真实 AK**

通过本机 `PUT /api/settings/integrations/baidu_maps` 写入用户提供的 AK、启用状态、广州和三溪地铁站。命令不得打印请求体或响应中的完整 AK，不创建包含密钥的临时文件。

- [ ] **Step 5: 执行真实百度双服务测试**

调用 `POST /api/settings/integrations/baidu_maps/test`，确认：

- `ok=true`；
- 返回至少一个广州地点；
- BD-09 和 GCJ-02 坐标均存在；
- 日志、API 响应、Git 差异不包含完整 AK。

- [ ] **Step 6: 最终复验与提交**

Run: `.venv312/bin/python -m pytest -q && docker compose config -q && git diff --check`

Expected: 全绿且容器 healthy。

```bash
git add README.md
git commit -m "docs: document baidu settings operations"
git push -u origin HEAD
```

提交前逐文件暂存本任务内容，不使用 `git add .`、`git add -A` 或强制推送。
