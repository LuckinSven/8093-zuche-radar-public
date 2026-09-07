import httpx
from pydantic import BaseModel, Field


class BaiduMapsError(RuntimeError):
    """百度地图接口错误；消息必须可安全展示且不包含 AK。"""


class BaiduPlace(BaseModel):
    uid: str | None = None
    name: str
    address: str
    province: str | None = None
    city: str | None = None
    district: str | None = None
    adcode: str | None = None
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Gcj02Coordinate(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class BaiduMapsClient:
    def __init__(self, ak: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.ak = ak
        self.client = httpx.AsyncClient(base_url="https://api.map.baidu.com",
            timeout=10, transport=transport)

    async def __aenter__(self) -> "BaiduMapsClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def suggest(self, region: str, keyword: str) -> list[BaiduPlace]:
        data = await self._get("/place/v3/suggestion", {
            "query": keyword, "region": region, "region_limit": "true"})
        results = data.get("results")
        if not isinstance(results, list):
            raise BaiduMapsError("百度地点提示响应格式异常")
        places = []
        invalid_result = False
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("location"), dict):
                invalid_result = True
                continue
            location = item["location"]
            try:
                places.append(BaiduPlace(
                    uid=_optional_text(item.get("uid")),
                    name=str(item["name"]),
                    address=str(item.get("address") or ""),
                    province=_optional_text(item.get("province")),
                    city=_optional_text(item.get("city")),
                    district=_optional_text(item.get("district")),
                    adcode=_optional_text(item.get("adcode")),
                    latitude=float(location["lat"]),
                    longitude=float(location["lng"]),
                ))
            except (KeyError, TypeError, ValueError):
                invalid_result = True
                continue
        if not places:
            if invalid_result:
                raise BaiduMapsError("百度地点提示响应格式异常")
            raise BaiduMapsError("百度地图未找到匹配地点")
        return places

    async def convert_bd09_to_gcj02(self, latitude: float, longitude: float) -> Gcj02Coordinate:
        data = await self._get("/geoconv/v2/", {
            "coords": f"{longitude},{latitude}", "model": 5})
        results = data.get("result")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise BaiduMapsError("百度坐标转换响应格式异常")
        try:
            return Gcj02Coordinate(latitude=float(results[0]["y"]), longitude=float(results[0]["x"]))
        except (KeyError, TypeError, ValueError) as error:
            raise BaiduMapsError("百度坐标转换响应格式异常") from error

    async def _get(self, path: str, params: dict) -> dict:
        try:
            response = await self.client.get(path, params=params | {"ak": self.ak})
            response.raise_for_status()
            data = response.json()
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError,
                ValueError) as error:
            raise BaiduMapsError("百度地图网络请求失败") from error
        if not isinstance(data, dict) or data.get("status") not in (0, "0"):
            raise BaiduMapsError("百度地图验证失败")
        return data


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
