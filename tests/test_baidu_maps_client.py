import httpx
import pytest

from app.integrations.baidu_maps import BaiduMapsClient, BaiduMapsError


def success_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/place/v3/suggestion":
        assert request.url.params["query"] == "三溪地铁站"
        assert request.url.params["region"] == "广州"
        assert request.url.params["region_limit"] == "true"
        assert request.url.params["ak"] == "test-ak"
        return httpx.Response(200, json={"status": 0, "message": "ok", "results": [{
            "uid": "baidu-uid", "name": "三溪地铁站", "address": "广东省广州市天河区",
            "province": "广东省", "city": "广州市", "district": "天河区",
            "adcode": "440106",
            "location": {"lat": 23.109, "lng": 113.422}}]})
    assert request.url.path == "/geoconv/v2/"
    assert request.url.params["coords"] == "113.422,23.109"
    assert request.url.params["model"] == "5"
    return httpx.Response(200, json={"status": 0, "result": [{"x": 113.416, "y": 23.103}]})


@pytest.mark.asyncio
async def test_baidu_client_returns_place_and_gcj02_coordinate():
    transport = httpx.MockTransport(success_handler)
    async with BaiduMapsClient("test-ak", transport=transport) as client:
        places = await client.suggest("广州", "三溪地铁站")
        coordinate = await client.convert_bd09_to_gcj02(
            places[0].latitude, places[0].longitude)

    assert places[0].name == "三溪地铁站"
    assert places[0].address == "广东省广州市天河区"
    assert places[0].uid == "baidu-uid"
    assert places[0].province == "广东省"
    assert places[0].city == "广州市"
    assert places[0].district == "天河区"
    assert places[0].adcode == "440106"
    assert coordinate.latitude == 23.103
    assert coordinate.longitude == 113.416


@pytest.mark.asyncio
async def test_baidu_client_keeps_missing_optional_place_fields_as_none():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": 0, "message": "ok", "results": [{
            "name": "三溪地铁站", "location": {"lat": 23.109, "lng": 113.422},
        }],
    }))
    async with BaiduMapsClient("test-ak", transport=transport) as client:
        place = (await client.suggest("广州", "三溪地铁站"))[0]

    assert place.address == ""
    assert place.uid is None
    assert place.province is None
    assert place.city is None
    assert place.district is None
    assert place.adcode is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("latitude", "longitude"), [
    (float("nan"), 113.422),
    (23.109, float("inf")),
    (91.0, 113.422),
    (23.109, 181.0),
])
async def test_baidu_client_rejects_non_finite_or_out_of_range_place_coordinates(
        latitude, longitude):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": 0, "message": "ok", "results": [{
            "name": "非法地点", "location": {"lat": latitude, "lng": longitude},
        }],
    }))
    async with BaiduMapsClient("test-ak", transport=transport) as client:
        with pytest.raises(BaiduMapsError, match="百度"):
            await client.suggest("广州", "非法地点")


@pytest.mark.asyncio
async def test_baidu_client_rejects_empty_suggestion_results():
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, json={"status": 0, "message": "ok", "results": []}))
    async with BaiduMapsClient("test-ak", transport=transport) as client:
        with pytest.raises(BaiduMapsError, match="未找到匹配地点"):
            await client.suggest("广州", "不存在的地点")


@pytest.mark.asyncio
async def test_baidu_client_turns_business_error_into_safe_chinese_error():
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, json={"status": 101, "message": "AK参数不存在"}))
    async with BaiduMapsClient("must-not-leak", transport=transport) as client:
        with pytest.raises(BaiduMapsError, match="百度地图验证失败") as captured:
            await client.suggest("广州", "三溪地铁站")
    assert "must-not-leak" not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [
    lambda _: httpx.Response(503, json={"status": 1}),
    lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)),
    lambda _: httpx.Response(200, content=b"not-json"),
])
async def test_baidu_client_maps_http_timeout_and_invalid_json_to_safe_error(handler):
    async with BaiduMapsClient("must-not-leak", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BaiduMapsError, match="百度地图网络请求失败") as captured:
            await client.suggest("广州", "三溪地铁站")
    assert "must-not-leak" not in str(captured.value)


@pytest.mark.asyncio
async def test_baidu_client_rejects_malformed_coordinate_response():
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, json={"status": 0, "result": []}))
    async with BaiduMapsClient("test-ak", transport=transport) as client:
        with pytest.raises(BaiduMapsError, match="坐标转换响应格式异常"):
            await client.convert_bd09_to_gcj02(23.1, 113.4)


@pytest.mark.asyncio
@pytest.mark.parametrize(("latitude", "longitude"), [
    (float("nan"), 113.416),
    (23.103, float("-inf")),
    (-91.0, 113.416),
    (23.103, -181.0),
])
async def test_baidu_client_rejects_non_finite_or_out_of_range_converted_coordinates(
        latitude, longitude):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "status": 0, "result": [{"x": longitude, "y": latitude}],
    }))
    async with BaiduMapsClient("test-ak", transport=transport) as client:
        with pytest.raises(BaiduMapsError, match="百度"):
            await client.convert_bd09_to_gcj02(23.109, 113.422)
