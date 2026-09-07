import httpx
import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.api.map_cache import ShortSessionMapCacheRepository
from app.integrations.baidu_maps import BaiduMapsError, BaiduPlace, Gcj02Coordinate
from app.integrations.map_cache_repository import MapCacheRepository
from app.integrations.map_service import MapLocationService
from app.integrations.repository import IntegrationSettingsRepository
from app.main import create_app


class FakeBaidu:
    def __init__(self, *, broken: bool = False, on_network=None):
        self.broken = broken
        self.on_network = on_network
        self.searches = []
        self.conversions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def suggest(self, region, keyword):
        if self.on_network:
            self.on_network()
        self.searches.append((region, keyword))
        if self.broken:
            raise BaiduMapsError("百度地图暂时不可用")
        return [BaiduPlace(
            uid="safe-uid", name=f"{keyword}候选", address="广州市天河区",
            province="广东省", city="广州市", district="天河区", adcode="440106",
            latitude=23.109, longitude=113.422,
        )]

    async def convert_bd09_to_gcj02(self, latitude, longitude):
        if self.on_network:
            self.on_network()
        self.conversions.append((latitude, longitude))
        if self.broken:
            raise BaiduMapsError("百度地图暂时不可用")
        return Gcj02Coordinate(latitude=23.103, longitude=113.416)


def app_for(engine, fake):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", True,
            {"default_region": "广州", "test_keyword": "三溪地铁站"},
            "test-only-ak",
        )
        session.commit()
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: fake
    return app


def boundary_session_factory(engine):
    state = {"armed": False, "callback": None, "running": False}

    class BoundarySession(Session):
        def commit(self):
            super().commit()
            if state["armed"] and not state["running"]:
                state["armed"] = False
                state["running"] = True
                try:
                    state["callback"]()
                finally:
                    state["running"] = False

    return sessionmaker(
        bind=engine, class_=BoundarySession, expire_on_commit=False), state


def test_map_cache_write_apis_publish_strong_request_schemas():
    schema = create_app().openapi()["paths"]

    refresh = schema["/api/settings/map-cache/refresh"]["post"]["requestBody"]
    delete_item = schema["/api/settings/map-cache/item"]["delete"]["requestBody"]
    clear = schema["/api/settings/map-cache"]["delete"]["requestBody"]
    assert refresh["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CacheItemInput")
    assert delete_item["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CacheItemInput")
    assert clear["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ClearCacheInput")


@pytest.mark.asyncio
async def test_location_search_reports_baidu_then_cache_without_second_network_call(engine):
    fake = FakeBaidu()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(engine, fake)),
            base_url="http://test") as client:
        first = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "三溪地铁站"})
        second = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "三溪地铁站"})

    assert first.status_code == 200
    assert first.json()["source"] == "baidu"
    assert second.status_code == 200
    assert second.json()["source"] == "cache"
    assert fake.searches == [("广州", "三溪地铁站")]


@pytest.mark.asyncio
async def test_search_cache_list_and_refresh_preserve_original_query_text(engine):
    fake = FakeBaidu()
    app = app_for(engine, fake)
    with app.state.session_factory() as session:
        item = MapCacheRepository(session).save_search(
            "GuangZhou", " 三溪  地铁站 ", [{"name": "旧结果"}])
        item_id = item.id
        session.commit()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/api/settings/map-cache", params={"kind": "search"})
        refreshed = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": item_id})

    assert listing.status_code == 200
    assert listing.json()["items"][0]["region"] == "GuangZhou"
    assert listing.json()["items"][0]["keyword"] == " 三溪  地铁站 "
    assert refreshed.status_code == 200
    assert fake.searches == [("GuangZhou", " 三溪  地铁站 ")]


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_secret", [False, True])
async def test_cached_location_still_works_when_baidu_is_disabled_or_cleared(
        engine, remove_secret):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        settings = IntegrationSettingsRepository(session)
        settings.save("baidu_maps", True, {}, "test-only-ak")
        MapCacheRepository(session).save_search(
            "广州", "三溪地铁站", [{"name": "本机缓存结果"}])
        if remove_secret:
            settings.clear_secret("baidu_maps")
        else:
            settings.save("baidu_maps", False, {}, None)
        session.commit()

    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: (_ for _ in ()).throw(
        AssertionError("缓存命中不应创建百度客户端"))
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "三溪地铁站"})

    assert response.status_code == 200
    assert response.json()["source"] == "cache"
    assert response.json()["items"] == [{"name": "本机缓存结果"}]


@pytest.mark.asyncio
async def test_cache_miss_without_enabled_baidu_returns_safe_chinese_422(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: (_ for _ in ()).throw(
        AssertionError("无 AK 时不应创建百度客户端"))

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "未缓存地点"})

    assert response.status_code == 422
    assert response.json()["detail"] == "百度地图配置尚未启用"


@pytest.mark.asyncio
async def test_force_refresh_without_enabled_baidu_returns_422_and_keeps_cache(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", False, {}, "test-only-ak")
        cached = MapCacheRepository(session).save_search(
            "广州", "三溪地铁站", [{"name": "保留的缓存"}])
        item_id = cached.id
        session.commit()
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: (_ for _ in ()).throw(
        AssertionError("停用时不应创建百度客户端"))

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": item_id})

    assert response.status_code == 422
    assert response.json()["detail"] == "百度地图配置尚未启用"
    with factory() as session:
        assert MapCacheRepository(session).get_search(
            "广州", "三溪地铁站").results_json == [{"name": "保留的缓存"}]


@pytest.mark.asyncio
async def test_location_search_rejects_blank_input_and_maps_baidu_error_safely(engine):
    fake = FakeBaidu(broken=True)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(engine, fake)),
            base_url="http://test") as client:
        blank = await client.get("/api/locations/search", params={
            "region": " ", "keyword": "三溪地铁站"})
        broken = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "三溪地铁站"})

    assert blank.status_code == 422
    assert blank.json()["detail"] == "城市和地点关键词不能为空"
    assert broken.status_code == 502
    assert broken.json()["detail"] == "百度地图请求失败，请稍后重试或检查配置"
    assert "test-only-ak" not in broken.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "payload", "expected"), [
    ("GET", "/api/settings/map-cache?kind=unknown", None,
     "数据类型只能是全部、地点或坐标"),
    ("GET", "/api/settings/map-cache?page=0", None,
     "页码必须是大于 0 的整数"),
    ("GET", "/api/settings/map-cache?page=abc", None,
     "页码必须是大于 0 的整数"),
    ("GET", "/api/settings/map-cache?page_size=101", None,
     "每页数量必须是 1 到 100 的整数"),
    ("POST", "/api/settings/map-cache/refresh", {"kind": "other", "id": 1},
     "缓存类型只能是地点或坐标"),
    ("POST", "/api/settings/map-cache/refresh", {"kind": "search", "id": 0},
     "缓存编号必须是大于 0 的整数"),
    ("DELETE", "/api/settings/map-cache/item", {"kind": "search", "id": "abc"},
     "缓存编号必须是大于 0 的整数"),
    ("DELETE", "/api/settings/map-cache", {"confirmation": "错误短语"},
     "请输入“清空全部地图缓存”确认清理"),
])
async def test_map_cache_validation_errors_are_stable_chinese(
        engine, method, path, payload, expected):
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(engine, FakeBaidu())),
            base_url="http://test") as client:
        response = await client.request(method, path, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == expected


@pytest.mark.asyncio
async def test_map_cache_list_has_stable_stats_filters_and_pagination(engine):
    fake = FakeBaidu()
    app = app_for(engine, fake)
    with app.state.session_factory() as session:
        repo = MapCacheRepository(session)
        first = repo.save_search("广州", "三溪地铁站", [{
            "name": "<img src=x onerror=alert(1)>", "address": "测试地址",
            "latitude": 23.109, "longitude": 113.422,
        }])
        repo.hit_search(first)
        repo.save_search("深圳", "福田站", [{"name": "福田站"}])
        repo.save_coordinate("BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)
        session.commit()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/map-cache", params={
            "kind": "search", "region": "广州", "keyword": "三溪", "page": 1,
            "page_size": 1,
        })

    body = response.json()
    assert response.status_code == 200
    assert body["stats"]["search_count"] == 2
    assert body["stats"]["coordinate_count"] == 1
    assert body["stats"]["total_hit_count"] == 1
    assert body["stats"]["latest_refreshed_at"]
    assert body["pagination"] == {"page": 1, "page_size": 1, "total": 1, "pages": 1}
    assert body["items"][0]["kind"] == "search"
    assert body["items"][0]["region"] == "广州"
    assert body["items"][0]["last_hit_at"]
    assert body["items"][0]["details"][0]["name"].startswith("<img")


@pytest.mark.asyncio
async def test_map_cache_list_clamps_page_past_last_page(engine):
    app = app_for(engine, FakeBaidu())
    with app.state.session_factory() as session:
        repo = MapCacheRepository(session)
        repo.save_search("广州", "三溪地铁站", [{"name": "三溪地铁站"}])
        repo.save_search("广州", "广州南站", [{"name": "广州南站"}])
        session.commit()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/map-cache", params={
            "page": 999, "page_size": 1})

    assert response.status_code == 200
    assert response.json()["pagination"] == {
        "page": 2, "page_size": 1, "total": 2, "pages": 2}
    assert len(response.json()["items"]) == 1


@pytest.mark.asyncio
async def test_baidu_network_wait_never_holds_database_session(engine):
    tracker = {"open": 0}

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._tracker_closed = False
            tracker["open"] += 1

        def close(self):
            if not self._tracker_closed:
                tracker["open"] -= 1
                self._tracker_closed = True
            super().close()

    regular_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with regular_factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", True,
            {"default_region": "广州", "test_keyword": "三溪地铁站"},
            "test-only-ak",
        )
        session.commit()

    factory = sessionmaker(
        bind=engine, class_=TrackingSession, expire_on_commit=False)
    fake = FakeBaidu(on_network=lambda: (
        tracker["open"] == 0
        or (_ for _ in ()).throw(AssertionError("百度等待期间仍持有数据库会话"))))
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: fake

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        searched = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "三溪地铁站"})
        listing = await client.get("/api/settings/map-cache")
        item_id = listing.json()["items"][0]["id"]
        refreshed = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": item_id})

    assert searched.status_code == 200
    assert refreshed.status_code == 200
    assert tracker["open"] == 0


@pytest.mark.asyncio
async def test_normal_cache_save_returns_snapshot_when_deleted_at_commit_boundary(engine):
    regular_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with regular_factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", True, {}, "test-only-ak")
        session.commit()
    factory, boundary = boundary_session_factory(engine)

    def clear_after_commit():
        with regular_factory() as session:
            MapCacheRepository(session).clear()
            session.commit()

    boundary.update(armed=True, callback=clear_after_commit)
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: FakeBaidu()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test") as client:
        response = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "提交边界新地点"})

    assert response.status_code == 200
    assert response.json()["source"] == "baidu"
    assert response.json()["items"][0]["name"] == "提交边界新地点候选"
    with regular_factory() as session:
        assert MapCacheRepository(session).list_searches() == []


@pytest.mark.asyncio
async def test_normal_cache_hit_returns_snapshot_when_deleted_at_commit_boundary(engine):
    regular_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with regular_factory() as session:
        MapCacheRepository(session).save_search(
            "广州", "三溪地铁站", [{"name": "提交前缓存"}])
        session.commit()
    factory, boundary = boundary_session_factory(engine)

    def clear_after_commit():
        with regular_factory() as session:
            MapCacheRepository(session).clear()
            session.commit()

    boundary.update(armed=True, callback=clear_after_commit)
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: (_ for _ in ()).throw(
        AssertionError("缓存命中不应创建百度客户端"))
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test") as client:
        response = await client.get("/api/locations/search", params={
            "region": "广州", "keyword": "三溪地铁站"})

    assert response.status_code == 200
    assert response.json()["source"] == "cache"
    assert response.json()["items"] == [{"name": "提交前缓存"}]
    with regular_factory() as session:
        assert MapCacheRepository(session).list_searches() == []


@pytest.mark.asyncio
async def test_normal_coordinate_save_returns_snapshot_when_cleared_at_commit_boundary(engine):
    regular_factory = sessionmaker(bind=engine, expire_on_commit=False)
    factory, boundary = boundary_session_factory(engine)

    def clear_after_commit():
        with regular_factory() as session:
            MapCacheRepository(session).clear()
            session.commit()

    boundary.update(armed=True, callback=clear_after_commit)
    result = await MapLocationService(
        ShortSessionMapCacheRepository(factory), lambda: FakeBaidu(),
    ).convert(23.109, 113.422)

    assert result["source"] == "baidu"
    assert result["latitude"] == 23.103
    with regular_factory() as session:
        assert MapCacheRepository(session).list_coordinates() == []


@pytest.mark.asyncio
async def test_normal_coordinate_hit_returns_snapshot_when_cleared_at_commit_boundary(engine):
    regular_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with regular_factory() as session:
        MapCacheRepository(session).save_coordinate(
            "BD-09", "GCJ-02", 23.109, 113.422, 23.100, 113.410)
        session.commit()
    factory, boundary = boundary_session_factory(engine)

    def clear_after_commit():
        with regular_factory() as session:
            MapCacheRepository(session).clear()
            session.commit()

    boundary.update(armed=True, callback=clear_after_commit)
    result = await MapLocationService(
        ShortSessionMapCacheRepository(factory),
        lambda: (_ for _ in ()).throw(
            AssertionError("坐标缓存命中不应创建百度客户端")),
    ).convert(23.109, 113.422)

    assert result["source"] == "cache"
    assert result["latitude"] == 23.1
    with regular_factory() as session:
        assert MapCacheRepository(session).list_coordinates() == []


@pytest.mark.asyncio
async def test_protected_search_refresh_returns_snapshot_when_deleted_after_commit(engine):
    regular_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with regular_factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", True, {}, "test-only-ak")
        cached = MapCacheRepository(session).save_search(
            "广州", "三溪地铁站", [{"name": "旧结果"}])
        item_id = cached.id
        session.commit()
    factory, boundary = boundary_session_factory(engine)

    def delete_after_commit():
        with regular_factory() as session:
            MapCacheRepository(session).delete_search(item_id)
            session.commit()

    boundary.update(armed=True, callback=delete_after_commit)
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: FakeBaidu()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test") as client:
        response = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": item_id})

    assert response.status_code == 200
    assert response.json()["source"] == "baidu"
    with regular_factory() as session:
        assert MapCacheRepository(session).list_searches() == []


@pytest.mark.asyncio
async def test_protected_coordinate_refresh_returns_snapshot_when_cleared_after_commit(engine):
    regular_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with regular_factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", True, {}, "test-only-ak")
        cached = MapCacheRepository(session).save_coordinate(
            "BD-09", "GCJ-02", 23.109, 113.422, 23.100, 113.410)
        item_id = cached.id
        session.commit()
    factory, boundary = boundary_session_factory(engine)

    def clear_after_commit():
        with regular_factory() as session:
            MapCacheRepository(session).clear()
            session.commit()

    boundary.update(armed=True, callback=clear_after_commit)
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: FakeBaidu()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test") as client:
        response = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "coordinate", "id": item_id})

    assert response.status_code == 200
    assert response.json()["source"] == "baidu"
    assert response.json()["latitude"] == 23.103
    with regular_factory() as session:
        assert MapCacheRepository(session).list_coordinates() == []


@pytest.mark.asyncio
async def test_refresh_delete_and_clear_use_fixed_endpoints_and_safe_missing_404(engine):
    fake = FakeBaidu()
    app = app_for(engine, fake)
    with app.state.session_factory() as session:
        repo = MapCacheRepository(session)
        search = repo.save_search("广州", "旧地点", [{"name": "旧结果"}])
        coordinate = repo.save_coordinate(
            "BD-09", "GCJ-02", 23.109, 113.422, 23.100, 113.410)
        search_id, coordinate_id = search.id, coordinate.id
        session.commit()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        refreshed = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": search_id})
        refreshed_coordinate = await client.post(
            "/api/settings/map-cache/refresh", json={
                "kind": "coordinate", "id": coordinate_id})
        deleted = await client.request("DELETE", "/api/settings/map-cache/item", json={
            "kind": "coordinate", "id": coordinate_id})
        missing_delete = await client.request(
            "DELETE", "/api/settings/map-cache/item", json={
                "kind": "coordinate", "id": coordinate_id})
        missing_refresh = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": 999999})
        bad_clear = await client.request("DELETE", "/api/settings/map-cache", json={
            "confirmation": "不是确认文字"})
        cleared = await client.request("DELETE", "/api/settings/map-cache", json={
            "confirmation": "清空全部地图缓存"})

    assert refreshed.status_code == 200
    assert refreshed.json()["source"] == "baidu"
    assert refreshed.json()["items"][0]["name"] == "旧地点候选"
    assert refreshed_coordinate.status_code == 200
    assert refreshed_coordinate.json()["source"] == "baidu"
    assert refreshed_coordinate.json()["latitude"] == 23.103
    assert deleted.status_code == 204
    assert missing_delete.status_code == 404
    assert missing_delete.json()["detail"] == "没有找到这条地图缓存"
    assert missing_refresh.status_code == 404
    assert missing_refresh.json()["detail"] == "没有找到这条地图缓存"
    assert bad_clear.status_code == 422
    assert cleared.status_code == 204


@pytest.mark.asyncio
async def test_failed_refresh_keeps_previous_result(engine):
    fake = FakeBaidu(broken=True)
    app = app_for(engine, fake)
    with app.state.session_factory() as session:
        cached = MapCacheRepository(session).save_search(
            "广州", "三溪地铁站", [{"name": "仍可使用的旧结果"}])
        item_id = cached.id
        session.commit()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": item_id})

    assert response.status_code == 502
    assert response.json()["detail"] == "百度地图请求失败，请稍后重试或检查配置"
    with app.state.session_factory() as session:
        assert MapCacheRepository(session).get_search(
            "广州", "三溪地铁站").results_json == [{"name": "仍可使用的旧结果"}]


@pytest.mark.asyncio
async def test_deleted_search_is_not_recreated_when_refresh_returns(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", True, {}, "test-only-ak")
        cached = MapCacheRepository(session).save_search(
            "广州", "三溪地铁站", [{"name": "旧结果"}])
        item_id = cached.id
        session.commit()

    def delete_during_network():
        with factory() as session:
            MapCacheRepository(session).delete_search(item_id)
            session.commit()

    fake = FakeBaidu(on_network=delete_during_network)
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: fake
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "search", "id": item_id})

    assert response.status_code == 409
    assert response.json()["detail"] == "缓存已被删除，刷新结果未保存"
    with factory() as session:
        assert MapCacheRepository(session).list_searches() == []


@pytest.mark.asyncio
async def test_cleared_coordinate_is_not_recreated_when_refresh_returns(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        IntegrationSettingsRepository(session).save(
            "baidu_maps", True, {}, "test-only-ak")
        cached = MapCacheRepository(session).save_coordinate(
            "BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)
        item_id = cached.id
        session.commit()

    def clear_during_network():
        with factory() as session:
            MapCacheRepository(session).clear()
            session.commit()

    fake = FakeBaidu(on_network=clear_during_network)
    app = create_app(session_factory=factory)
    app.state.baidu_client_factory = lambda _ak: fake
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/settings/map-cache/refresh", json={
            "kind": "coordinate", "id": item_id})

    assert response.status_code == 409
    assert response.json()["detail"] == "缓存已被删除，刷新结果未保存"
    with factory() as session:
        assert MapCacheRepository(session).list_coordinates() == []
