import json
from collections.abc import Mapping

import httpx

from app.schemas import CityResolution, ScanRequest


GATEWAY_URL = "https://m.zuche.com/api/gw.do"
CHOOSE_CAR_URI = "/resource/carrctapi/order/chooseCar/v3"
CITY_LOCATION_URI = "/action/carrctapi/order/cityLocation/v1"


class ZucheApiError(RuntimeError):
    """神州接口未返回成功结果。"""


class ZucheClient:
    """神州租车公开移动端网关的轻量客户端。"""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
                "Origin": "https://m.zuche.com",
                "Referer": "https://m.zuche.com/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self._owns_client = client is None

    async def __aenter__(self) -> "ZucheClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def resolve_city(self, latitude: float, longitude: float) -> CityResolution:
        payload = {"lat": f"{latitude:.6f}", "lon": f"{longitude:.6f}"}
        response = await self._post(CITY_LOCATION_URI, payload)
        content = _content(response)
        return CityResolution(
            city_id=str(content["cityId"]),
            city_name=str(content.get("cityName") or content.get("name") or ""),
        )

    async def choose_car(self, request: ScanRequest) -> dict:
        payload = {
            "cityId": request.city_id,
            "startTime": request.pickup_time.strftime("%Y-%m-%d %H:%M"),
            "endTime": request.return_time.strftime("%Y-%m-%d %H:%M"),
            "lat": f"{request.latitude:.6f}",
            "lon": f"{request.longitude:.6f}",
        }
        return await self._post(CHOOSE_CAR_URI, payload)

    async def _post(self, uri: str, payload: Mapping[str, object]) -> dict:
        response = await self._client.post(
            GATEWAY_URL,
            params={"uri": uri},
            data={"data": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        )
        response.raise_for_status()
        decoded = response.json()
        if not isinstance(decoded, dict) or decoded.get("code") != 1:
            message = decoded.get("msg", "未知错误") if isinstance(decoded, dict) else "响应格式错误"
            raise ZucheApiError(str(message))
        return decoded


def _content(response: dict) -> dict:
    content = response.get("content")
    if not isinstance(content, dict):
        raise ZucheApiError("响应缺少内容")
    return content
