import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.api_catalog import build_api_catalog
from app.integrations.baidu_maps import BaiduMapsError, BaiduPlace, Gcj02Coordinate
from app.integrations.service import mask_secret
from app.main import create_app


CHOOSE_CAR_FIXTURE = Path(__file__).parent / "fixtures" / "choose_car_guangzhou_redacted.json"


def app_for(engine):
    return create_app(session_factory=sessionmaker(bind=engine, expire_on_commit=False))


async def save_baidu(client, *, enabled=True, ak="abcdef123456"):
    return await client.put("/api/settings/integrations/baidu_maps", json={
        "enabled": enabled, "ak": ak, "default_region": "广州",
        "test_keyword": "三溪地铁站"})


@pytest.mark.parametrize("secret", ["a", "ab", "abc", "abcd", "abcde", "abcdef", "abcdefg", "abcdefgh"])
def test_short_secret_is_fully_masked(secret):
    assert mask_secret(secret) == "****"


@pytest.mark.asyncio
async def test_settings_api_returns_safe_default_when_not_configured(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        response = await client.get("/api/settings/integrations")

    assert response.status_code == 200
    assert response.json() == {"items": [
        {"provider": "baidu_maps", "display_name": "百度地图", "enabled": False,
         "configured": False, "masked_secret": None, "default_region": "广州",
         "test_keyword": "三溪地铁站", "updated_at": None},
        {"provider": "openai_compatible_enrichment", "display_name": "AI 车型补全",
         "enabled": False, "configured": False, "masked_secret": None,
         "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
         "batch_size": 1, "timeout_seconds": 30, "max_retries": 1,
         "updated_at": None},
    ]}


@pytest.mark.asyncio
async def test_manual_scan_uses_injectable_anonymous_client_factory_without_arguments(engine):
    class AnonymousChooseCar:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def choose_car(self, _):
            return json.loads(CHOOSE_CAR_FIXTURE.read_text(encoding="utf-8"))

    app = app_for(engine)
    factory_calls = []
    app.state.zuche_client_factory = lambda: factory_calls.append("anonymous") or AnonymousChooseCar()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/scans", json={
            "city_id": "14", "location_name": "广州中心", "latitude": 23.1291, "longitude": 113.2644,
            "pickup_time": datetime(2026, 9, 5, 9).isoformat(),
            "return_time": datetime(2026, 9, 6, 9).isoformat(),
        })

    assert response.status_code == 201
    assert response.json()["status"] == "SUCCESS"

    assert factory_calls == ["anonymous"]


def test_openapi_publishes_baidu_and_ai_integration_endpoints(engine):
    paths = app_for(engine).openapi()["paths"]

    assert {path for path in paths if path.startswith("/api/settings/integrations")} == {
        "/api/settings/integrations",
        "/api/settings/integrations/baidu_maps",
        "/api/settings/integrations/baidu_maps/secret",
        "/api/settings/integrations/baidu_maps/test",
        "/api/settings/integrations/openai-compatible",
        "/api/settings/integrations/openai-compatible/secret",
        "/api/settings/integrations/openai-compatible/test",
    }


@pytest.mark.asyncio
async def test_ai_setting_round_trip_masks_api_key_and_preserves_blank_update(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        saved = await client.put("/api/settings/integrations/openai-compatible", json={
            "enabled": True,
            "api_key": "sk-1234567890",
            "base_url": "https://api.deepseek.com/v1/",
            "model": "custom-model",
            "batch_size": 1,
            "timeout_seconds": 75,
            "max_retries": 3,
        })
        updated = await client.put("/api/settings/integrations/openai-compatible", json={
            "enabled": False,
            "api_key": "",
            "base_url": "https://gateway.example.com/",
            "model": "second-model",
            "batch_size": 1,
            "timeout_seconds": 60,
            "max_retries": 1,
        })

    assert saved.status_code == 200
    assert saved.json()["masked_secret"] == "sk-1****7890"
    assert "sk-1234567890" not in saved.text
    assert saved.json()["base_url"] == "https://api.deepseek.com/v1"
    assert updated.json()["configured"] is True
    assert updated.json()["masked_secret"] == "sk-1****7890"
    assert updated.json()["base_url"] == "https://gateway.example.com"


@pytest.mark.asyncio
async def test_ai_setting_rejects_enabled_configuration_without_secret(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        response = await client.put("/api/settings/integrations/openai-compatible", json={
            "enabled": True,
            "api_key": "",
            "base_url": "https://api.example.com",
            "model": "model-x",
        })

    assert response.status_code == 422
    assert response.json()["detail"] == "启用 AI 补全前必须配置 API Key"


@pytest.mark.asyncio
async def test_ai_setting_rejects_multi_model_batches(engine):
    """兼容接口若重新接受批量值，历史任务会再次整批格式失败。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        response = await client.put("/api/settings/integrations/openai-compatible", json={
            "enabled": False,
            "base_url": "https://api.example.com",
            "model": "model-x",
            "batch_size": 2,
        })

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ai_connection_test_uses_saved_configuration_without_exposing_secret(engine):
    factory_calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def test_connection(self):
            return {
                "ok": True,
                "elapsed_ms": 18,
                "model": "custom-model",
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }

    app = app_for(engine)
    app.state.enrichment_client_factory = lambda **kwargs: (
        factory_calls.append(kwargs) or FakeClient())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        await client.put("/api/settings/integrations/openai-compatible", json={
            "enabled": True,
            "api_key": "must-not-leak",
            "base_url": "https://api.example.com/v1",
            "model": "custom-model",
            "batch_size": 1,
            "timeout_seconds": 55,
            "max_retries": 2,
        })
        response = await client.post("/api/settings/integrations/openai-compatible/test")

    assert response.status_code == 200
    assert response.json()["usage"]["total_tokens"] == 30
    assert "must-not-leak" not in response.text
    assert factory_calls == [{
        "api_key": "must-not-leak",
        "base_url": "https://api.example.com/v1",
        "model": "custom-model",
        "timeout_seconds": 55,
        "max_retries": 2,
    }]


@pytest.mark.asyncio
async def test_clearing_ai_secret_also_disables_integration(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        await client.put("/api/settings/integrations/openai-compatible", json={
            "enabled": True,
            "api_key": "secret-value",
            "base_url": "https://api.example.com",
            "model": "model-x",
        })
        cleared = await client.delete(
            "/api/settings/integrations/openai-compatible/secret")
        settings = await client.get("/api/settings/integrations")

    ai = next(item for item in settings.json()["items"]
              if item["provider"] == "openai_compatible_enrichment")
    assert cleared.status_code == 204
    assert ai["enabled"] is False
    assert ai["configured"] is False


def test_api_catalog_documents_citywide_find_car_flow(engine):
    catalog = build_api_catalog(app_for(engine).openapi(), "http://server.local:8093")
    items = {
        (item["method"], item["path"]): item
        for group in catalog["groups"]
        for item in group["items"]
    }

    assert ("POST", "/api/citywide-scans") in items
    assert ("GET", "/api/citywide-models") in items
    assert ("GET", "/api/model-library") in items
    assert items[("POST", "/api/citywide-scans")]["summary"] == "创建广州全城扫描"
    assert "取还车时间必须包含时区" in items[("POST", "/api/citywide-scans")]["description"]
    assert "比亚迪海狮05" in items[("GET", "/api/citywide-models")]["example"]
    assert "AVAILABLE、NOT_FOUND、INCOMPLETE" in items[("GET", "/api/citywide-models")]["description"]
    assert "只增加、不自动删除" in items[("GET", "/api/model-library")]["description"]


def test_api_catalog_documents_model_search_flow(engine):
    catalog = build_api_catalog(app_for(engine).openapi(), "http://server.local:8093")
    items = {
        (item["method"], item["path"]): item
        for group in catalog["groups"]
        for item in group["items"]
    }

    expected = {
        ("POST", "/api/model-search-runs"),
        ("GET", "/api/model-search-runs"),
        ("GET", "/api/model-search-runs/{run_id}"),
        ("POST", "/api/model-search-runs/{run_id}/stop"),
        ("POST", "/api/model-search-runs/{run_id}/resume"),
        ("GET", "/api/model-search-runs/{run_id}/results"),
        ("GET", "/api/model-search-runs/{run_id}/periods"),
        ("GET", "/api/model-library/options"),
    }
    assert expected <= set(items)
    assert "未来四个周末" in items[("POST", "/api/model-search-runs")]["description"]
    assert "比亚迪海狮05" in items[("POST", "/api/model-search-runs")]["example"]
    assert "未找到" in items[("GET", "/api/model-search-runs/{run_id}/results")]["description"]
    assert "准确取还时间" in items[("GET", "/api/model-search-runs/{run_id}/periods")]["description"]


def test_api_catalog_documents_cross_city_search_flow(engine):
    catalog = build_api_catalog(app_for(engine).openapi(), "http://server.local:8093")
    items = {
        (item["method"], item["path"]): item
        for group in catalog["groups"]
        for item in group["items"]
    }

    expected = {
        ("POST", "/api/cross-city-search-runs"),
        ("GET", "/api/cross-city-search-runs"),
        ("GET", "/api/cross-city-search-runs/{run_id}"),
        ("GET", "/api/cross-city-search-runs/{run_id}/results"),
        ("POST", "/api/cross-city-search-runs/{run_id}/stop"),
        ("POST", "/api/cross-city-search-runs/{run_id}/resume"),
    }
    assert expected <= set(items)
    create = items[("POST", "/api/cross-city-search-runs")]
    assert "神州车型 ID" in create["description"]
    assert "严格 14 天" in create["description"]
    assert "2000" in create["description"]
    assert '"zuche_model_id":1001' in create["example"]
    assert "2026-09-24" not in create["example"]
    results = items[("GET", "/api/cross-city-search-runs/{run_id}/results")]
    assert "AVAILABLE、NOT_FOUND、INCOMPLETE" in results["description"]


def test_api_catalog_describes_ai_enrichment_endpoints_without_secrets(engine):
    catalog = build_api_catalog(app_for(engine).openapi(), "http://server.local:8093")
    items = {
        (item["method"], item["path"]): item
        for group in catalog["groups"]
        for item in group["items"]
    }

    assert items[("POST", "/api/vehicle-enrichment-runs")]["summary"] == "创建车型能源 AI 补全任务"
    assert "只有高置信" in items[("POST", "/api/vehicle-enrichment-runs")]["description"]
    assert ("POST", "/api/vehicle-enrichment-runs/{run_id}/retry-failed") in items
    assert "PENDING_ONLY" in items[("POST", "/api/vehicle-enrichment-runs")]["example"]
    assert all(
        "API Key" not in item["example"] and "secret_value" not in item["example"]
        for item in items.values()
    )


@pytest.mark.asyncio
async def test_settings_api_masks_secret_and_blank_update_preserves_it(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        saved = await save_baidu(client)
        updated = await client.put("/api/settings/integrations/baidu_maps", json={
            "enabled": False, "ak": "", "default_region": "深圳", "test_keyword": "福田站"})

    assert saved.status_code == 200
    assert saved.json()["masked_secret"] == "abcd****3456"
    assert "abcdef123456" not in saved.text
    assert updated.json()["configured"] is True
    assert updated.json()["masked_secret"] == "abcd****3456"
    assert updated.json()["default_region"] == "深圳"


@pytest.mark.asyncio
async def test_settings_api_rejects_enabling_without_secret(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        response = await save_baidu(client, ak="")

    assert response.status_code == 422
    assert response.json()["detail"] == "启用百度地图前必须配置 AK"


class FakeBaidu:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    async def suggest(self, region, keyword):
        assert (region, keyword) == ("广州", "三溪地铁站")
        return [BaiduPlace(name="三溪地铁站", address="广东省广州市天河区",
                           latitude=23.109, longitude=113.422)]
    async def convert_bd09_to_gcj02(self, latitude, longitude):
        assert (latitude, longitude) == (23.109, 113.422)
        return Gcj02Coordinate(latitude=23.103, longitude=113.416)


@pytest.mark.asyncio
async def test_settings_test_returns_two_safe_steps(engine):
    app = app_for(engine)
    received_keys = []
    app.state.baidu_client_factory = lambda ak: received_keys.append(ak) or FakeBaidu()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await save_baidu(client, ak="secret-ak-value")
        response = await client.post("/api/settings/integrations/baidu_maps/test")

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["place_suggestion"]["name"] == "三溪地铁站"
    assert body["coordinate_conversion"]["gcj02_longitude"] == 113.416
    assert body["elapsed_ms"] >= 0
    assert "secret-ak-value" not in response.text
    assert received_keys == ["secret-ak-value"]


@pytest.mark.asyncio
async def test_settings_test_rejects_disabled_integration(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        await save_baidu(client, enabled=False)
        response = await client.post("/api/settings/integrations/baidu_maps/test")

    assert response.status_code == 422
    assert response.json()["detail"] == "百度地图配置尚未启用"


@pytest.mark.asyncio
async def test_settings_test_maps_external_error_to_502_without_secret(engine):
    class BrokenBaidu(FakeBaidu):
        async def suggest(self, region, keyword):
            raise BaiduMapsError("百度地图验证失败")

    app = app_for(engine)
    app.state.baidu_client_factory = lambda _: BrokenBaidu()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await save_baidu(client, ak="secret-ak-value")
        response = await client.post("/api/settings/integrations/baidu_maps/test")

    assert response.status_code == 502
    assert response.json()["detail"] == "百度地图验证失败"
    assert "secret-ak-value" not in response.text


@pytest.mark.asyncio
async def test_clear_secret_disables_integration(engine):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(engine)),
                                 base_url="http://test") as client:
        await save_baidu(client)
        cleared = await client.delete("/api/settings/integrations/baidu_maps/secret")
        settings = await client.get("/api/settings/integrations")

    assert cleared.status_code == 204
    assert settings.json()["items"][0]["configured"] is False
    assert settings.json()["items"][0]["enabled"] is False
