import pytest

from app.integrations.baidu_maps import BaiduMapsError, BaiduPlace, Gcj02Coordinate
from app.integrations.map_cache_repository import MapCacheRepository
from app.integrations.map_service import MapLocationService


class FakeBaiduClient:
    def __init__(self, *, places=None, coordinate=None, error: Exception | None = None):
        self.places = places
        self.coordinate = coordinate
        self.error = error
        self.suggest_requests = []
        self.convert_requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def suggest(self, region: str, keyword: str):
        self.suggest_requests.append((region, keyword))
        if self.error:
            raise self.error
        return self.places

    async def convert_bd09_to_gcj02(self, latitude: float, longitude: float):
        self.convert_requests.append((latitude, longitude))
        if self.error:
            raise self.error
        return self.coordinate


def factory_that_must_not_run():
    raise AssertionError("缓存命中时不应创建百度客户端")


@pytest.mark.asyncio
async def test_search_cache_hit_does_not_call_baidu_and_counts_hit(session):
    repo = MapCacheRepository(session)
    cached = repo.save_search("广州", "三溪地铁站", [{
        "name": "三溪地铁站", "address": "", "latitude": 23.11,
        "longitude": 113.42,
    }])

    result = await MapLocationService(repo, factory_that_must_not_run).search(
        "广州", "三溪地铁站")

    assert result == {
        "source": "cache",
        "cached_at": cached.refreshed_at,
        "items": [{
            "name": "三溪地铁站", "address": "", "latitude": 23.11,
            "longitude": 113.42,
        }],
    }
    assert repo.get_search("广州", "三溪地铁站").hit_count == 1


@pytest.mark.asyncio
async def test_search_cache_miss_calls_baidu_and_saves_safe_place_fields(session):
    repo = MapCacheRepository(session)
    client = FakeBaiduClient(places=[BaiduPlace(
        uid="baidu-uid", name="三溪地铁站", address="广东省广州市天河区",
        province="广东省", city="广州市", district="天河区", adcode="440106",
        latitude=23.109, longitude=113.422,
    )])

    result = await MapLocationService(repo, lambda: client).search(
        "广州", "三溪地铁站")

    expected = [{
        "uid": "baidu-uid", "name": "三溪地铁站",
        "address": "广东省广州市天河区", "province": "广东省", "city": "广州市",
        "district": "天河区", "adcode": "440106", "latitude": 23.109,
        "longitude": 113.422,
    }]
    assert result["source"] == "baidu"
    assert result["items"] == expected
    assert result["cached_at"] == repo.get_search("广州", "三溪地铁站").refreshed_at
    assert repo.get_search("广州", "三溪地铁站").results_json == expected
    assert client.suggest_requests == [("广州", "三溪地铁站")]


@pytest.mark.asyncio
async def test_failed_force_refresh_keeps_old_search_cache(session):
    repo = MapCacheRepository(session)
    old = repo.save_search("广州", "三溪地铁站", [{"name": "旧结果"}])
    client = FakeBaiduClient(error=BaiduMapsError("百度地图网络请求失败"))

    with pytest.raises(BaiduMapsError, match="百度地图网络请求失败"):
        await MapLocationService(repo, lambda: client).search(
            "广州", "三溪地铁站", force_refresh=True)

    current = repo.get_search("广州", "三溪地铁站")
    assert current.results_json == old.results_json
    assert current.refreshed_at == old.refreshed_at


@pytest.mark.asyncio
async def test_empty_force_refresh_keeps_old_search_cache(session):
    repo = MapCacheRepository(session)
    old = repo.save_search("广州", "三溪地铁站", [{"name": "旧结果"}])
    client = FakeBaiduClient(places=[])

    with pytest.raises(BaiduMapsError, match="未找到匹配地点"):
        await MapLocationService(repo, lambda: client).search(
            "广州", "三溪地铁站", force_refresh=True)

    assert repo.get_search("广州", "三溪地铁站").results_json == old.results_json


@pytest.mark.asyncio
async def test_malformed_force_refresh_keeps_old_search_cache(session):
    repo = MapCacheRepository(session)
    old = repo.save_search("广州", "三溪地铁站", [{"name": "旧结果"}])
    client = FakeBaiduClient(places=[object()])

    with pytest.raises(BaiduMapsError, match="地点提示响应格式异常"):
        await MapLocationService(repo, lambda: client).search(
            "广州", "三溪地铁站", force_refresh=True)

    assert repo.get_search("广州", "三溪地铁站").results_json == old.results_json


@pytest.mark.asyncio
@pytest.mark.parametrize(("latitude", "longitude"), [
    (float("nan"), 113.422),
    (23.109, float("inf")),
    (91.0, 113.422),
])
async def test_invalid_place_force_refresh_keeps_old_search_cache(
        session, latitude, longitude):
    repo = MapCacheRepository(session)
    old = repo.save_search("广州", "三溪地铁站", [{"name": "旧结果"}])
    invalid = BaiduPlace.model_construct(
        name="非法地点", address="", latitude=latitude, longitude=longitude)
    client = FakeBaiduClient(places=[invalid])

    with pytest.raises(BaiduMapsError, match="地点提示响应格式异常"):
        await MapLocationService(repo, lambda: client).search(
            "广州", "三溪地铁站", force_refresh=True)

    assert repo.get_search("广州", "三溪地铁站").results_json == old.results_json


@pytest.mark.asyncio
async def test_coordinate_cache_hit_does_not_call_baidu(session):
    repo = MapCacheRepository(session)
    cached = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)

    result = await MapLocationService(repo, factory_that_must_not_run).convert(
        23.109, 113.422)

    assert result == {
        "source": "cache",
        "cached_at": cached.refreshed_at,
        "latitude": 23.103,
        "longitude": 113.416,
    }
    current = repo.get_coordinate("BD-09", "GCJ-02", 23.109, 113.422)
    assert current.hit_count == 1
    assert current.last_hit_at is not None


@pytest.mark.asyncio
async def test_coordinate_cache_miss_uses_bd09_to_gcj02_and_saves_result(session):
    repo = MapCacheRepository(session)
    client = FakeBaiduClient(coordinate=Gcj02Coordinate(
        latitude=23.103, longitude=113.416))

    result = await MapLocationService(repo, lambda: client).convert(23.109, 113.422)

    cached = repo.get_coordinate("BD-09", "GCJ-02", 23.109, 113.422)
    assert result == {
        "source": "baidu",
        "cached_at": cached.refreshed_at,
        "latitude": 23.103,
        "longitude": 113.416,
    }
    assert float(cached.target_latitude) == 23.103
    assert float(cached.target_longitude) == 113.416
    assert client.convert_requests == [(23.109, 113.422)]


@pytest.mark.asyncio
async def test_failed_force_refresh_keeps_old_coordinate_cache(session):
    repo = MapCacheRepository(session)
    old = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)
    client = FakeBaiduClient(error=BaiduMapsError("百度地图网络请求失败"))

    with pytest.raises(BaiduMapsError, match="百度地图网络请求失败"):
        await MapLocationService(repo, lambda: client).convert(
            23.109, 113.422, force_refresh=True)

    current = repo.get_coordinate("BD-09", "GCJ-02", 23.109, 113.422)
    assert current.target_latitude == old.target_latitude
    assert current.target_longitude == old.target_longitude
    assert current.refreshed_at == old.refreshed_at


@pytest.mark.asyncio
async def test_malformed_force_refresh_keeps_old_coordinate_cache(session):
    repo = MapCacheRepository(session)
    old = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)
    client = FakeBaiduClient(coordinate=None)

    with pytest.raises(BaiduMapsError, match="坐标转换响应格式异常"):
        await MapLocationService(repo, lambda: client).convert(
            23.109, 113.422, force_refresh=True)

    current = repo.get_coordinate("BD-09", "GCJ-02", 23.109, 113.422)
    assert current.target_latitude == old.target_latitude
    assert current.target_longitude == old.target_longitude


@pytest.mark.asyncio
@pytest.mark.parametrize(("latitude", "longitude"), [
    (float("nan"), 113.416),
    (23.103, float("-inf")),
    (-91.0, 113.416),
])
async def test_invalid_coordinate_force_refresh_keeps_old_coordinate_cache(
        session, latitude, longitude):
    repo = MapCacheRepository(session)
    old = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)
    invalid = Gcj02Coordinate.model_construct(
        latitude=latitude, longitude=longitude)
    client = FakeBaiduClient(coordinate=invalid)

    with pytest.raises(BaiduMapsError, match="坐标转换响应格式异常"):
        await MapLocationService(repo, lambda: client).convert(
            23.109, 113.422, force_refresh=True)

    current = repo.get_coordinate("BD-09", "GCJ-02", 23.109, 113.422)
    assert current.target_latitude == old.target_latitude
    assert current.target_longitude == old.target_longitude


@pytest.mark.asyncio
async def test_invalid_source_coordinate_is_rejected_before_cache_or_baidu(session):
    repo = MapCacheRepository(session)

    with pytest.raises(BaiduMapsError, match="源坐标格式异常"):
        await MapLocationService(repo, factory_that_must_not_run).convert(
            float("nan"), 113.422, force_refresh=True)
