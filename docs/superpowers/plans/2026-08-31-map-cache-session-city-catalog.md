# 地图缓存与匿名城市目录实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有系统中交付 PostgreSQL 永久地图缓存、缓存二级页面和匿名开放城市目录同步。

**Architecture:** 地点检索和坐标转换使用独立仓储永久保存最新有效结果，业务服务执行 cache-aside 与强制刷新。神州目录客户端是无参数匿名工厂；匿名接口失败即返回安全错误，不回退认证。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL 16/JSONB、Alembic、httpx、Jinja2、原生 JavaScript、pytest、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-08-31-map-cache-department-discovery-design.md`

## Global Constraints

- 所有新增页面、API 错误和文档使用中文。
- 地点与坐标缓存永久保存，只保留最新有效结果。
- 百度测试绕过缓存；失败刷新不得覆盖已有成功缓存。
- 历史 Cookie/session fallback 已废弃，不实施、不提供设置或 API；匿名接口失败只返回安全错误。
- 外部 URL 固定在代码中；不实现手机号、验证码或自动登录。
- 城市目录同步的新城市默认 `enabled=False`，广州继续启用。
- Docker 端口保持 `8093`，PostgreSQL 数据卷保持不变。
- 所有生产行为先写失败测试并确认失败，再写最小实现。

---

### Task 1: 缓存和城市目录持久化

**Files:**
- Create: `alembic/versions/0008_add_map_cache_and_city_catalog.py`
- Modify: `app/models.py`
- Create: `app/integrations/map_cache_repository.py`
- Create: `tests/test_map_cache_repository.py`

**Interfaces:**
- Consumes: SQLAlchemy `Session`、现有 `City`。
- Produces: `MapSearchCache`、`CoordinateCache`；`MapCacheRepository.get_search()`、`save_search()`、`hit_search()`、`get_coordinate()`、`save_coordinate()`、`stats()`、`list_*()`、`delete_*()`、`clear()`。

- [ ] **Step 1: 写持久化失败测试**

```python
def test_search_cache_keeps_latest_result_and_counts_hits(session):
    repo = MapCacheRepository(session)
    item = repo.save_search("广州", "三溪地铁站", [{"name": "旧结果"}])
    repo.hit_search(item)
    updated = repo.save_search(" 广州 ", "三溪  地铁站", [{"name": "新结果"}])
    assert updated.id == item.id
    assert updated.results_json == [{"name": "新结果"}]
    assert updated.hit_count == 1

def test_coordinate_cache_is_unique_by_crs_and_normalized_coordinate(session):
    repo = MapCacheRepository(session)
    first = repo.save_coordinate("BD-09", "GCJ-02", 23.110319261, 113.422343706,
                                 23.104090425, 113.415894899)
    second = repo.save_coordinate("BD-09", "GCJ-02", 23.1103192611, 113.4223437061,
                                  23.1041, 113.4159)
    assert second.id == first.id
```

- [ ] **Step 2: 运行测试并确认因模型/仓储不存在而失败**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_map_cache_repository.py`

- [ ] **Step 3: 添加模型和迁移**

`MapSearchCache` 使用 `provider + normalized_region + normalized_keyword` 唯一约束；`CoordinateCache` 将源坐标量化为 8 位小数并以提供商、坐标系和源坐标建立唯一约束。城市增加 `code`、`en_name`、`catalog_active`、`first_seen_at`、`last_seen_at`、`catalog_synced_at`，迁移保留现有广州并令新字段可兼容旧数据。

- [ ] **Step 4: 实现仓储与标准化**

```python
from decimal import Decimal, ROUND_HALF_UP

def normalize_cache_text(value: str) -> str:
    return " ".join(value.strip().split()).casefold()

def normalize_coordinate(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
```

`get_search()` 使用两个标准化字段查询；`save_search()` 只替换 `results_json` 和 `refreshed_at`，保留 `first_fetched_at` 与 `hit_count`；`hit_search()` 原子递增计数。坐标方法先调用 `normalize_coordinate()` 再查询或 upsert，目标坐标随刷新覆盖。

- [ ] **Step 5: 运行测试和单头迁移检查**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_map_cache_repository.py && /tmp/zuche-radar-test-env/bin/alembic heads`

- [ ] **Step 6: 提交**

```bash
git add alembic/versions/0008_add_map_cache_and_city_catalog.py app/models.py app/integrations/map_cache_repository.py tests/test_map_cache_repository.py
git commit -m "feat: add persistent map cache storage"
```

---

### Task 2: 百度 cache-aside 服务

**Files:**
- Modify: `app/integrations/baidu_maps.py`
- Create: `app/integrations/map_service.py`
- Create: `tests/test_map_service.py`
- Modify: `tests/test_baidu_maps_client.py`

**Interfaces:**
- Consumes: `MapCacheRepository`、`BaiduMapsClient` 工厂。
- Produces: `MapLocationService.search(region, keyword, force_refresh=False)`、`convert(latitude, longitude, force_refresh=False)`。

- [ ] **Step 1: 写缓存命中、刷新和失败保留测试**

```python
@pytest.mark.asyncio
async def test_search_cache_hit_does_not_call_baidu(session):
    repo = MapCacheRepository(session)
    repo.save_search("广州", "三溪地铁站", [{"name": "三溪-地铁站",
        "address": "", "latitude": 23.11, "longitude": 113.42}])
    service = MapLocationService(repo, factory_that_must_not_run)
    result = await service.search("广州", "三溪地铁站")
    assert result["source"] == "cache"

@pytest.mark.asyncio
async def test_failed_force_refresh_keeps_old_cache(session):
    repo = MapCacheRepository(session)
    old = repo.save_search("广州", "三溪地铁站", [{"name": "旧结果"}])
    with pytest.raises(BaiduMapsError):
        await MapLocationService(repo, broken_factory).search(
            "广州", "三溪地铁站", force_refresh=True)
    assert repo.get_search("广州", "三溪地铁站").results_json == old.results_json
```

- [ ] **Step 2: 运行测试并确认服务不存在而失败**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_map_service.py`

- [ ] **Step 3: 扩展 `BaiduPlace` 的安全可选字段**

增加 `uid`、`province`、`city`、`district`、`adcode`，真实 v3 模拟响应使用 `results` 并覆盖这些字段；缺失字段保持 `None`。

- [ ] **Step 4: 实现 cache-aside 服务**

搜索命中返回 `source=cache`、`cached_at` 和候选列表；未命中或强制刷新调用百度，验证非空后事务性 upsert。坐标转换使用 `BD-09 → GCJ-02` 固定键并遵循同样规则。服务不捕获并伪装外部错误。

- [ ] **Step 5: 运行相关测试**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_map_service.py tests/test_baidu_maps_client.py tests/test_settings_api.py`

- [ ] **Step 6: 提交**

```bash
git add app/integrations/baidu_maps.py app/integrations/map_service.py tests/test_map_service.py tests/test_baidu_maps_client.py
git commit -m "feat: cache baidu place and coordinate results"
```

---

### Task 3: 地图缓存 API 与二级页面

**Files:**
- Create: `app/api/map_cache.py`
- Modify: `app/api/router.py`
- Modify: `app/web.py`
- Create: `app/templates/settings_map_cache.html`
- Modify: `app/templates/settings.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Create: `tests/test_map_cache_api.py`
- Modify: `tests/test_rebuild_web.py`

**Interfaces:**
- Produces: `GET /api/locations/search`、`GET /api/settings/map-cache`、单条刷新/删除、`DELETE /api/settings/map-cache`；`GET /settings/map-cache`。

- [ ] **Step 1: 写 API 和页面失败测试**

```python
@pytest.mark.asyncio
async def test_location_search_reports_cache_source(client):
    first = await client.get("/api/locations/search", params={"region": "广州", "keyword": "三溪地铁站"})
    second = await client.get("/api/locations/search", params={"region": "广州", "keyword": "三溪地铁站"})
    assert first.json()["source"] == "baidu"
    assert second.json()["source"] == "cache"

@pytest.mark.asyncio
async def test_map_cache_page_is_secondary_settings_page(client):
    response = await client.get("/settings/map-cache")
    assert 'id="map-cache-list"' in response.text
    assert 'href="/settings/map-cache"' in response.text
```

- [ ] **Step 2: 运行测试并确认 404**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_map_cache_api.py tests/test_rebuild_web.py`

- [ ] **Step 3: 实现固定 API**

列表支持 `kind`、城市、关键词和分页；刷新按缓存 ID 读取原城市/关键词；全部清理使用固定无路径变量端点。所有百度错误映射为安全 502，空参数映射为 422。

- [ ] **Step 4: 实现二级导航和缓存页面**

页面展示地点数、坐标数、命中数、更新时间、筛选、候选详情、来源、单条刷新/删除和二次确认的全部清理。所有外部字段插入 HTML 前使用 `esc()`。

- [ ] **Step 5: 运行 API 与页面测试**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_map_cache_api.py tests/test_rebuild_web.py tests/test_settings_api.py`

- [ ] **Step 6: 提交**

```bash
git add app/api/map_cache.py app/api/router.py app/web.py app/templates/settings_map_cache.html app/templates/settings.html app/static/app.js app/static/app.css tests/test_map_cache_api.py tests/test_rebuild_web.py
git commit -m "feat: add map cache settings workspace"
```

---

### 已废弃：原 Task 4 会话后备

原 Task 4 曾计划保存 Cookie、公开设置 API 并在匿名失败后回退认证。该任务已于 `dde1601` 废弃，不得实施、恢复或拆分为新的待办。本计划保留此标题仅用于追溯；当前替代契约是无参数匿名客户端工厂、`list_cities()` 目录解析，以及匿名接口失败时的安全错误。

---

### Task 5: 匿名开放城市目录同步

**Files:**
- Create: `app/zuche/catalog.py`
- Create: `app/repositories/cities.py`
- Create: `app/api/zuche_catalog.py`
- Modify: `app/api/router.py`
- Modify: `app/web.py`
- Create: `app/templates/settings_shenzhou.html`
- Modify: `app/templates/settings.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Create: `tests/test_city_catalog.py`
- Modify: `tests/test_rebuild_web.py`

**Interfaces:**
- Produces: `CityCatalogService.sync()`；`POST /api/zuche/cities/sync`、目录统计；`GET /settings/shenzhou`。

- [ ] **Step 1: 写城市 upsert 和广州启用测试**

```python
def test_catalog_sync_keeps_guangzhou_enabled_and_new_cities_disabled(session):
    service = CityCatalogService(CityRepository(session), fake_gateway)
    result = asyncio.run(service.sync())
    guangzhou = repository.get_by_zuche_id("14")
    new_city = repository.get_by_zuche_id("999")
    assert guangzhou.enabled is True
    assert new_city.enabled is False
    assert result["city_count"] == 2
```

- [ ] **Step 2: 运行测试并确认缺少目录服务**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_city_catalog.py`

- [ ] **Step 3: 实现目录解析和 upsert**

只接受含城市 ID、名称和合法经纬度的条目；同步不存在即新增、存在即更新目录字段；本次未出现的目录城市标记 `catalog_active=False`，不删除，不改变用户手工 `enabled`。

- [ ] **Step 4: 实现 API 与页面**

页面含匿名接口测试、开放城市统计和“同步城市目录”；不提供认证配置、会话开关、保存、验证或清除操作。匿名接口失败只展示安全错误。

- [ ] **Step 5: 运行相关回归并提交**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q tests/test_city_catalog.py tests/test_settings_api.py tests/test_rebuild_web.py tests/test_rebuild_api.py`

```bash
git add app/zuche/catalog.py app/repositories/cities.py app/api/zuche_catalog.py app/api/router.py app/web.py app/templates/settings_shenzhou.html app/templates/settings.html app/static/app.js app/static/app.css tests/test_city_catalog.py tests/test_rebuild_web.py
git commit -m "feat: add shenzhou catalog settings workspace"
```

---

### Task 6: 第一阶段文档与部署验收

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新中文 README**

说明永久缓存、缓存刷新、匿名城市同步不等于全国扫描，以及匿名接口失败即安全报错、不回退认证。

- [ ] **Step 2: 完整自动化与迁移验收**

Run: `/tmp/zuche-radar-test-env/bin/python -m pytest -q && /tmp/zuche-radar-test-env/bin/alembic heads && docker compose config -q && git diff --check`

在独立临时数据库从零迁移到 `0008`，确认两张缓存表、城市字段和广州种子存在，然后删除该临时库。

- [ ] **Step 3: Docker 与真实低频验收**

Run: `docker compose up -d --build`

确认容器 healthy；第一次真实地点搜索 `source=baidu`，第二次相同搜索 `source=cache`；百度测试仍真实调用；匿名城市同步成功且广州保持启用；匿名失败不会切换认证路径或写入敏感请求信息。

- [ ] **Step 4: 敏感信息检查和提交**

检查 Git、响应和应用日志不含 AK 或认证信息；逐文件暂存 README，提交：

```bash
git add README.md
git commit -m "docs: describe persistent map and catalog settings"
```
