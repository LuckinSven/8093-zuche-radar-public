import time
from collections.abc import Callable
from urllib.parse import urlsplit

from app.enrichment.client import OpenAICompatibleClient
from app.integrations.baidu_maps import BaiduMapsClient
from app.integrations.repository import IntegrationSettingsRepository
from app.models import IntegrationSetting


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    return "****" if len(value) <= 8 else f"{value[:4]}****{value[-4:]}"


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


def serialize_ai_enrichment_setting(item: IntegrationSetting | None) -> dict:
    config = item.config_json if item else {}
    return {
        "provider": "openai_compatible_enrichment",
        "display_name": "AI 车型补全",
        "enabled": bool(item and item.enabled),
        "configured": bool(item and item.secret_value),
        "masked_secret": mask_secret(item.secret_value if item else None),
        "base_url": config.get("base_url", "https://api.deepseek.com"),
        "model": config.get("model", "deepseek-chat"),
        "batch_size": 1,
        "timeout_seconds": config.get("timeout_seconds", 30),
        "max_retries": config.get("max_retries", 1),
        "updated_at": item.updated_at if item else None,
    }


class IntegrationSettingsService:
    def __init__(self, repository: IntegrationSettingsRepository) -> None:
        self.repository = repository

    def list_integrations(self) -> list[dict]:
        return [
            serialize_baidu_setting(self.repository.get("baidu_maps")),
            serialize_ai_enrichment_setting(
                self.repository.get("openai_compatible_enrichment")),
        ]

    def save_baidu(self, *, enabled: bool, ak: str | None,
                   default_region: str, test_keyword: str) -> dict:
        existing = self.repository.get("baidu_maps")
        secret = ak.strip() if ak and ak.strip() else None
        available_secret = secret or (existing.secret_value if existing else None)
        if enabled and not available_secret:
            raise ValueError("启用百度地图前必须配置 AK")
        region, keyword = default_region.strip(), test_keyword.strip()
        if not region or not keyword:
            raise ValueError("默认城市和测试关键词不能为空")
        item = self.repository.save("baidu_maps", enabled,
            {"default_region": region, "test_keyword": keyword}, secret)
        return serialize_baidu_setting(item)

    def clear_baidu_secret(self) -> None:
        self.repository.clear_secret("baidu_maps")

    async def test_baidu(self, client_factory: Callable[[str], BaiduMapsClient]) -> dict:
        item = self.repository.get("baidu_maps")
        if item is None or not item.secret_value:
            raise ValueError("百度地图 AK 尚未配置")
        if not item.enabled:
            raise ValueError("百度地图配置尚未启用")
        config = item.config_json or {}
        region = str(config.get("default_region") or "广州")
        keyword = str(config.get("test_keyword") or "三溪地铁站")
        started = time.perf_counter()
        async with client_factory(item.secret_value) as client:
            places = await client.suggest(region, keyword)
            place = places[0]
            coordinate = await client.convert_bd09_to_gcj02(place.latitude, place.longitude)
        return {
            "ok": True,
            "elapsed_ms": max(0, round((time.perf_counter() - started) * 1000)),
            "place_suggestion": {
                "ok": True, "name": place.name, "address": place.address,
                "bd09_latitude": place.latitude, "bd09_longitude": place.longitude,
            },
            "coordinate_conversion": {
                "ok": True, "gcj02_latitude": coordinate.latitude,
                "gcj02_longitude": coordinate.longitude,
            },
        }

    def save_ai_enrichment(
        self,
        *,
        enabled: bool,
        api_key: str | None,
        base_url: str,
        model: str,
        batch_size: int,
        timeout_seconds: int,
        max_retries: int,
    ) -> dict:
        existing = self.repository.get("openai_compatible_enrichment")
        secret = api_key.strip() if api_key and api_key.strip() else None
        available_secret = secret or (existing.secret_value if existing else None)
        if enabled and not available_secret:
            raise ValueError("启用 AI 补全前必须配置 API Key")
        normalized_url = base_url.strip().rstrip("/")
        parsed = urlsplit(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 必须是有效的 HTTP 或 HTTPS 地址")
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("模型名称不能为空")
        item = self.repository.save(
            "openai_compatible_enrichment",
            enabled,
            {
                "base_url": normalized_url,
                "model": normalized_model,
                "batch_size": 1,
                "timeout_seconds": timeout_seconds,
                "max_retries": max_retries,
            },
            secret,
        )
        return serialize_ai_enrichment_setting(item)

    def clear_ai_enrichment_secret(self) -> None:
        self.repository.clear_secret("openai_compatible_enrichment")

    async def test_ai_enrichment(
        self,
        client_factory: Callable[..., OpenAICompatibleClient],
    ) -> dict:
        item = self.repository.get("openai_compatible_enrichment")
        if item is None or not item.secret_value:
            raise ValueError("AI 补全 API Key 尚未配置")
        if not item.enabled:
            raise ValueError("AI 补全配置尚未启用")
        config = item.config_json or {}
        async with client_factory(
            api_key=item.secret_value,
            base_url=str(config.get("base_url") or "https://api.deepseek.com"),
            model=str(config.get("model") or "deepseek-chat"),
            timeout_seconds=int(config.get("timeout_seconds") or 30),
            max_retries=int(config.get("max_retries") or 1),
        ) as client:
            result = await client.test_connection()
        return result.model_dump() if hasattr(result, "model_dump") else dict(result)
