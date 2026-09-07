import math
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.models import City


class FakeGateway:
    def __init__(self, cities):
        self.cities = cities

    async def list_cities(self):
        return self.cities


@pytest.mark.asyncio
async def test_catalog_sync_preserves_manual_enabled_and_marks_absent_city_inactive(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogService

    session.add_all([
        City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644,
             enabled=True, catalog_active=True),
        City(zuche_city_id="20", name="深圳", latitude=22.5431, longitude=114.0579,
             enabled=True, catalog_active=True),
    ])
    session.commit()

    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "14", "cityName": "广州市", "latitude": "23.1291", "longitude": "113.2644",
         "cityCode": "GZ", "enName": "Guangzhou"},
        {"cityId": "999", "cityName": "新城市", "lat": 30.1, "lon": 120.2},
    ]))

    result = await service.sync()

    repository = CityRepository(session)
    guangzhou = repository.get_by_zuche_id("14")
    new_city = repository.get_by_zuche_id("999")
    shenzhen = repository.get_by_zuche_id("20")
    assert result == {"city_count": 2, "created_count": 1, "updated_count": 1, "inactive_count": 1}
    assert guangzhou.enabled is True
    assert guangzhou.code == "GZ"
    assert guangzhou.en_name == "Guangzhou"
    assert new_city.enabled is False
    assert new_city.catalog_active is True
    assert new_city.first_seen_at is not None
    assert new_city.last_seen_at is not None
    assert new_city.catalog_synced_at is not None
    assert shenzhen.enabled is True
    assert shenzhen.catalog_active is False


@pytest.mark.asyncio
async def test_catalog_rejects_an_empty_or_invalid_snapshot_without_changing_existing_cities(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogError, CityCatalogService

    session.add(City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644,
                     enabled=True, catalog_active=True))
    session.commit()
    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "", "cityName": "空 ID", "lat": 23.1, "lon": 113.2},
        {"cityId": "88", "cityName": "无效坐标", "lat": math.inf, "lon": 113.2},
    ]))

    with pytest.raises(CityCatalogError, match="响应格式异常"):
        await service.sync()

    city = CityRepository(session).get_by_zuche_id("14")
    assert city.catalog_active is True


@pytest.mark.asyncio
async def test_catalog_keeps_the_first_duplicate_id_from_one_snapshot(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogService

    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "30", "cityName": "第一条", "lat": 30.1, "lon": 120.2},
        {"cityId": "30", "cityName": "第一条", "lat": 30.1, "lon": 120.2},
    ]))

    await service.sync()

    city = CityRepository(session).get_by_zuche_id("30")
    assert (city.name, float(city.latitude), float(city.longitude)) == ("第一条", 30.1, 120.2)


@pytest.mark.asyncio
async def test_catalog_rejects_the_entire_snapshot_when_any_entry_is_invalid(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogError, CityCatalogService

    session.add(City(zuche_city_id="14", name="旧广州", latitude=23.1, longitude=113.2,
                     enabled=True, catalog_active=True))
    session.commit()
    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "14", "cityName": "新广州", "lat": 23.1291, "lon": 113.2644},
        {"cityId": "broken", "cityName": "坏坐标", "lat": "not-a-number", "lon": 113.2},
    ]))

    with pytest.raises(CityCatalogError, match="响应格式异常"):
        await service.sync()

    city = CityRepository(session).get_by_zuche_id("14")
    assert (city.name, city.catalog_active) == ("旧广州", True)


@pytest.mark.asyncio
async def test_catalog_rejects_conflicting_duplicate_id_without_changing_database(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogError, CityCatalogService

    session.add(City(zuche_city_id="14", name="旧广州", latitude=23.1, longitude=113.2,
                     enabled=True, catalog_active=True))
    session.commit()
    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "14", "cityName": "广州", "lat": 23.1291, "lon": 113.2644},
        {"cityId": "14", "cityName": "广州市", "lat": 23.1291, "lon": 113.2644},
    ]))

    with pytest.raises(CityCatalogError, match="响应格式异常"):
        await service.sync()

    city = CityRepository(session).get_by_zuche_id("14")
    assert (city.name, city.catalog_active) == ("旧广州", True)


@pytest.mark.asyncio
async def test_catalog_rejects_conflicting_known_alias_fields(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogError, CityCatalogService

    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "14", "cityName": "广州", "lat": 23.1291, "lon": 113.2644,
         "cityCode": "GZ", "code": "CONFLICT"},
    ]))

    with pytest.raises(CityCatalogError, match="响应格式异常"):
        await service.sync()


@pytest.mark.asyncio
async def test_catalog_only_enables_guangzhou_when_zuche_id_is_14(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogService

    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "14", "cityName": "广州市", "lat": 23.1291, "lon": 113.2644},
        {"cityId": "999", "cityName": "广州", "lat": 30.1, "lon": 120.2},
    ]))

    await service.sync()

    repository = CityRepository(session)
    assert repository.get_by_zuche_id("14").enabled is True
    assert repository.get_by_zuche_id("999").enabled is False


@pytest.mark.asyncio
async def test_catalog_accepts_the_known_underscore_and_city_coordinate_field_combination(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogService

    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"city_id": "45", "name": "字段城市", "cityLatitude": "30.123", "cityLongitude": "120.456",
         "city_code": "FIELD", "en_name": "Field City"},
    ]))

    await service.sync()

    city = CityRepository(session).get_by_zuche_id("45")
    assert (city.name, city.code, city.en_name, float(city.latitude), float(city.longitude)) == (
        "字段城市", "FIELD", "Field City", 30.123, 120.456)


@pytest.mark.asyncio
async def test_catalog_preserves_cities_whose_coordinates_are_both_missing(session):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogService

    service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
        {"cityId": "14", "cityName": "广州", "cityLat": "23.1291", "cityLon": "113.2644"},
        {"cityId": "998", "cityName": "待补坐标城市", "cityLat": "", "cityLon": ""},
    ]))

    result = await service.sync()

    city = CityRepository(session).get_by_zuche_id("998")
    assert result["city_count"] == 2
    assert city.latitude is None
    assert city.longitude is None
    assert city.enabled is False


@pytest.mark.asyncio
async def test_catalog_does_not_create_a_database_session_while_waiting_for_network():
    from app.zuche.catalog import CityCatalogService

    created_sessions = []

    class Gateway:
        async def list_cities(self):
            assert created_sessions == []
            return [{"cityId": "14", "cityName": "广州", "lat": 23.1291, "lon": 113.2644}]

    def repository_factory():
        created_sessions.append(True)
        raise AssertionError("连接测试不应创建数据库会话")

    service = CityCatalogService(repository_factory, lambda: Gateway())
    assert await service.test_connection() == {"city_count": 1}
    assert created_sessions == []


@pytest.mark.asyncio
async def test_catalog_transaction_rolls_back_every_city_when_writing_snapshot_fails(session, engine):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogService

    session.add(City(zuche_city_id="14", name="旧广州", latitude=23.1, longitude=113.2,
                     enabled=True, catalog_active=True))
    session.commit()

    def fail_inactive_update(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("UPDATE CITIES"):
            raise RuntimeError("test transaction failure")

    event.listen(engine, "before_cursor_execute", fail_inactive_update)
    try:
        service = CityCatalogService(lambda: CityRepository(session), lambda: FakeGateway([
            {"cityId": "14", "cityName": "新广州", "lat": 23.1291, "lon": 113.2644},
            {"cityId": "99", "cityName": "新城市", "lat": 30.1, "lon": 120.2},
        ]))
        with pytest.raises(RuntimeError, match="test transaction failure"):
            await service.sync()
    finally:
        event.remove(engine, "before_cursor_execute", fail_inactive_update)

    repository = CityRepository(session)
    city = repository.get_by_zuche_id("14")
    assert (city.name, city.catalog_active) == ("旧广州", True)
    assert repository.get_by_zuche_id("99") is None


def test_postgresql_concurrent_catalog_sync_uses_advisory_lock_and_keeps_a_complete_snapshot(engine):
    from app.repositories.cities import CityRepository
    from app.zuche.catalog import CityCatalogService

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    gateway_barrier = Barrier(2)
    statements: list[str] = []

    class Gateway:
        def __init__(self, cities):
            self.cities = cities

        async def list_cities(self):
            gateway_barrier.wait(timeout=5)
            return self.cities

    def observe_statements(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    def sync(cities):
        import asyncio

        service = CityCatalogService(lambda: CityRepository(factory()), lambda: Gateway(cities))
        return asyncio.run(service.sync())

    snapshot_a = [
        {"cityId": "14", "cityName": "广州A", "lat": 23.1, "lon": 113.2},
        {"cityId": "20", "cityName": "城市A", "lat": 22.1, "lon": 114.2},
    ]
    snapshot_b = [
        {"cityId": "14", "cityName": "广州B", "lat": 23.2, "lon": 113.3},
        {"cityId": "30", "cityName": "城市B", "lat": 31.1, "lon": 121.2},
    ]
    event.listen(engine, "before_cursor_execute", observe_statements)
    errors = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(sync, snapshot) for snapshot in (snapshot_a, snapshot_b)]
            for future in futures:
                try:
                    future.result(timeout=10)
                except Exception as error:
                    errors.append(error)
    finally:
        event.remove(engine, "before_cursor_execute", observe_statements)

    assert errors == []
    assert any("pg_advisory_xact_lock" in statement for statement in statements)
    with factory() as session:
        active = {city.zuche_city_id: city.name for city in session.query(City).filter_by(catalog_active=True)}
    assert active in ({"14": "广州A", "20": "城市A"}, {"14": "广州B", "30": "城市B"})


@pytest.mark.asyncio
async def test_catalog_api_reports_stats_and_maps_anonymous_failure_to_safe_chinese_error(engine):
    import httpx
    from sqlalchemy.orm import sessionmaker

    from app.main import create_app

    class BrokenGateway:
        async def list_cities(self):
            raise RuntimeError("upstream request detail")

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory)
    app.state.zuche_client_factory = lambda: BrokenGateway()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        stats = await client.get("/api/zuche/cities")
        tested = await client.post("/api/zuche/cities/test")

    assert stats.status_code == 200
    assert stats.json() == {"city_count": 0, "active_count": 0, "last_synced_at": None}
    assert tested.status_code == 502
    assert tested.json()["detail"] == "神州匿名开放接口暂时不可用"


@pytest.mark.asyncio
async def test_catalog_api_syncs_only_after_the_anonymous_snapshot_has_been_read(engine):
    import httpx
    from sqlalchemy.orm import sessionmaker

    from app.main import create_app

    class Gateway:
        async def list_cities(self):
            return [{"cityId": "14", "cityName": "广州", "lat": 23.1291, "lon": 113.2644}]

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory)
    app.state.zuche_client_factory = lambda: Gateway()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/zuche/cities/sync")
        stats = await client.get("/api/zuche/cities")

    assert response.status_code == 200
    assert response.json()["city_count"] == 1
    assert stats.json()["active_count"] == 1
