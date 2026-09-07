import json

import httpx

from app.domain import CityResolution, ScanQuery
from app.settings import get_settings


class ZucheGatewayError(RuntimeError):
    pass


class ZucheClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
                   "Origin": "https://m.zuche.com", "Referer": "https://m.zuche.com/",
                   "X-Requested-With": "XMLHttpRequest"}
        self._client = httpx.AsyncClient(base_url=get_settings().zuche_base_url, timeout=20, transport=transport,
            headers=headers)

    async def __aenter__(self) -> "ZucheClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def choose_car(self, query: ScanQuery) -> dict:
        return await self._post("/resource/carrctapi/order/chooseCar/v3", {
            "pickupCityId": query.city_id,
            "pickupTime": query.pickup_time.strftime("%Y-%m-%d %H:%M"),
            "returnCityId": query.effective_return_city_id,
            "returnTime": query.return_time.strftime("%Y-%m-%d %H:%M"),
            "entrance": 1,
            "userChooseLat": query.latitude,
            "userChooseLon": query.longitude,
            "holidaysWaitingFlag": 0,
        })

    async def resolve_city(self, latitude: float, longitude: float) -> CityResolution:
        response = await self._post("/action/carrctapi/order/cityLocation/v1",
                                    {"lat": f"{latitude:.6f}", "lon": f"{longitude:.6f}"})
        content = response.get("content")
        if not isinstance(content, dict) or "cityId" not in content:
            raise ZucheGatewayError("城市定位响应缺少城市 ID")
        return CityResolution(city_id=str(content["cityId"]), city_name=str(content.get("cityName") or ""))

    async def list_cities(self) -> list[dict]:
        response = await self._post("/action/carrctapi/order/cityList/v1", {})
        content = response.get("content")
        cities = content if isinstance(content, list) else _city_list(content)
        if not isinstance(cities, list) or not all(isinstance(city, dict) for city in cities):
            raise ZucheGatewayError("城市目录响应格式错误")
        return cities

    async def list_departments(self, city_id: str) -> list[dict]:
        response = await self._post("/action/carrctapi/order/deptList/v1", {
            "cityId": city_id,
            "entrance": 1,
            "pickupFlag": 1,
        })
        content = response.get("content")
        districts = content.get("districtList") if isinstance(content, dict) else None
        if not isinstance(districts, list):
            raise ZucheGatewayError("网点目录响应格式错误")
        departments: list[dict] = []
        for district in districts:
            if not isinstance(district, dict) or not isinstance(district.get("deptList"), list):
                raise ZucheGatewayError("网点目录响应格式错误")
            for department in district["deptList"]:
                if not isinstance(department, dict):
                    raise ZucheGatewayError("网点目录响应格式错误")
                departments.append({
                    **department,
                    "districtId": district.get("districtId"),
                    "districtName": district.get("districtName"),
                })
        return departments

    async def _post(self, uri: str, payload: dict) -> dict:
        response = await self._client.post("/api/gw.do", params={"uri": uri},
            data={"data": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))})
        response.raise_for_status()
        try:
            result = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise ZucheGatewayError("神州接口响应格式错误") from error
        if not isinstance(result, dict) or result.get("code") != 1:
            raise ZucheGatewayError("神州接口业务处理失败" if isinstance(result, dict) else "神州接口响应格式错误")
        return result


def _city_list(content: object) -> list[dict] | None:
    if not isinstance(content, dict):
        return None
    for key in ("allCities", "cityList", "openCityList", "cities", "list"):
        candidate = content.get(key)
        if isinstance(candidate, list):
            direct = _as_city_list(candidate)
            if direct is not None:
                return direct
            nested = _nested_city_list(candidate)
            if nested is not None:
                return nested
    return None


def _as_city_list(candidate: list[object]) -> list[dict] | None:
    if all(isinstance(city, dict) and any(key in city for key in ("cityId", "city_id"))
           for city in candidate):
        return candidate
    return None


def _nested_city_list(candidate: list[object]) -> list[dict] | None:
    cities: list[dict] = []
    for group in candidate:
        if not isinstance(group, dict):
            return None
        for key in ("cityList", "cities", "citys", "list"):
            nested = group.get(key)
            if isinstance(nested, list):
                parsed = _as_city_list(nested)
                if parsed is None:
                    return None
                cities.extend(parsed)
                break
        else:
            return None
    return cities
