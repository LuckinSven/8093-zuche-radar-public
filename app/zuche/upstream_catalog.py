"""神州上游 URI 的证据台账与低风险探测。"""

import inspect
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain import ScanQuery


STATUS_LABELS = {
    "verified": "已实际验证",
    "implemented": "代码确认但未复测",
    "observed": "公开前端资源观察",
    "lead": "仅发现线索",
}
AUTH_LABELS = {
    "anonymous": "匿名可用",
    "likely_authenticated": "可能需要登录",
    "unknown": "尚未确认",
}
SCOPE = "当前项目、参考项目和神州公开 H5 首页资源中可观察到的 URI"
OBSERVED_AT = "2026-09-01"
FRONTEND_SOURCE = "https://h.zuchecdn.com/??lib/polyfill.min.js,lib/react.js,scripts/commons.js,scripts/runtime~home.js,scripts/home.js?v=2026032313"
REFERENCE_SOURCE = "https://github.com/zeratulers/shenzhou-zuche-scanner"
OPTIMIZER_SOURCE = "https://github.com/sunnyhot/car-rental-optimizer/blob/main/Sources/CarRentalOptimizer/LiveRentalSearchService.swift"


def _entry(uri: str, *, item_id: str, purpose: str, category: str, status: str,
           source: str, source_url: str, auth: str = "unknown", system_usage: str = "未使用",
           evidence: str, safe_probe: bool = False, request_fields: tuple[str, ...] = (),
           response_fields: tuple[str, ...] = (), persisted_fields: tuple[str, ...] = (),
           not_persisted_fields: tuple[str, ...] = (), last_verified_at: str | None = None) -> dict:
    return {
        "id": item_id,
        "uri": uri,
        "version": uri.rsplit("/", 1)[-1],
        "gateway": "POST https://m.zuche.com/api/gw.do?uri={URI}",
        "purpose": purpose,
        "category": category,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "source": source,
        "source_url": source_url,
        "observed_at": OBSERVED_AT,
        "last_verified_at": last_verified_at,
        "auth": auth,
        "auth_label": AUTH_LABELS[auth],
        "system_usage": system_usage,
        "evidence": evidence,
        "safe_probe": safe_probe,
        "request_fields": list(request_fields),
        "response_fields": list(response_fields),
        "persisted_fields": list(persisted_fields),
        "not_persisted_fields": list(not_persisted_fields),
    }


CORE_ENTRIES = [
    _entry(
        "/resource/carrctapi/order/chooseCar/v3", item_id="choose-car-v3",
        purpose="按城市、坐标和取还时间返回附近网点、车型、价格与可租状态",
        category="租车找车", status="verified", source="当前系统代码与脱敏实测响应",
        source_url="https://github.com/LuckinSven/zuche-radar-8093",
        auth="anonymous", system_usage="立即扫描、网点发现和车型数据表正在使用",
        evidence="当前适配器持续调用；已有广州脱敏响应样本和自动化解析测试。",
        safe_probe=True, last_verified_at="2026-09-01 22:36 +08:00",
        request_fields=("pickupCityId", "pickupTime", "returnCityId", "returnTime", "entrance",
                        "userChooseLat", "userChooseLon", "holidaysWaitingFlag"),
        response_fields=("content.deptHangModels[]", "content.modelGroups[]", "content.deptTotalNum",
                         "content.deptDataType", "content.notSuitDeptTips"),
        persisted_fields=("deptId", "deptName", "deptAddress", "lat", "lon", "deptDistance",
                          "workTime", "wholeDayFlag", "modelId", "modelName", "modelDesc",
                          "dailyPrice", "packagePrice", "bookFlag", "inventoryType", "selfServiceFlag"),
        not_persisted_fields=("deptTotalNum", "deptDataType", "notSuitDeptTips", "chainFlag",
                              "cityId", "modelCountDesc", "pickupAppropriate", "pickupWebsite",
                              "modelImgUrl", "lowPrice", "havePriceFlag", "lightSpotFlag",
                              "modelGroupId"),
    ),
    _entry(
        "/action/carrctapi/order/cityList/v1", item_id="city-list-v1",
        purpose="读取神州当前开放城市目录", category="城市与定位", status="verified",
        source="当前系统代码与参考项目运行流程", source_url=REFERENCE_SOURCE,
        auth="anonymous", system_usage="神州开放城市同步正在使用",
        evidence="当前系统可匿名调用并解析 allCities、cityList 等已观察结构。",
        safe_probe=True, last_verified_at="2026-09-01 22:36 +08:00", request_fields=(),
        response_fields=("content.allCities[]", "cityId", "cityName", "cityLat", "cityLon",
                         "cityCode", "enName"),
        persisted_fields=("cityId", "cityName", "cityLat/lat", "cityLon/lon", "cityCode", "enName"),
    ),
    _entry(
        "/action/carrctapi/order/cityLocation/v1", item_id="city-location-v1",
        purpose="根据经纬度识别神州城市 ID 和城市名称", category="城市与定位",
        status="verified", source="当前系统适配器代码与运行态探测",
        source_url="https://github.com/LuckinSven/zuche-radar-8093",
        auth="anonymous", system_usage="已实现调用方法，当前找车流程没有使用",
        evidence="代码中存在完整请求与解析逻辑；鱼珠坐标的匿名运行态探测成功识别为广州。",
        safe_probe=True, last_verified_at="2026-09-01 22:36 +08:00", request_fields=("lat", "lon"),
        response_fields=("content.cityId", "content.cityName"),
    ),
    _entry(
        "/action/carrctapi/order/deptList/v1", item_id="dept-list-v1",
        purpose="按城市一次返回行政区和网点目录，不依赖车型库存窗口",
        category="网点目录", status="verified", source="参考项目源码与广州匿名实测响应",
        source_url=OPTIMIZER_SOURCE,
        auth="anonymous", system_usage="广州网点目录手动同步与找车网点下拉正在使用",
        evidence="2026-09-01 匿名实测广州返回 17 个区域、91 个网点；鱼珠服务点 ID 79340 在结果中。",
        safe_probe=True, last_verified_at="2026-09-01 23:13 +08:00",
        request_fields=("cityId", "entrance", "pickupFlag"),
        response_fields=("content.districtList[]", "content.districtList[].districtId",
                         "content.districtList[].districtName",
                         "content.districtList[].deptCount",
                         "content.districtList[].deptList[]", "deptId", "deptName",
                         "deptAddress", "deptLat", "deptLon", "workTime", "wholeDayFlag",
                         "selfServiceFlag", "inventoryAbleFlag"),
        persisted_fields=("deptId", "deptName", "deptAddress", "deptLat", "deptLon",
                          "districtName", "workTime", "wholeDayFlag", "selfServiceFlag"),
        not_persisted_fields=("districtId", "deptCount", "deptPhone", "servicePhone",
                              "inventoryAbleFlag", "pickupAppropriate", "pickupWebsite",
                              "inWorkFlag", "panoramaFlag", "specialWorkTime"),
    ),
]


FRONTEND_PURPOSES = {
    "/resource/carrctapi/order/chooseCar/v1": ("旧版/另一入口的选车接口", "租车找车"),
    "/resource/carrctapi/testDrive/homePage/v1": ("试驾首页数据", "试驾"),
    "/resource/carrctapi/testDrive/intentionModelList/v1": ("试驾意向车型列表", "试驾"),
    "/resource/carrctapi/testDrive/modelDetail/v1": ("试驾车型详情", "试驾"),
    "/resource/carrctapi/testDrive/modelGuide/v1": ("试驾车型指南", "试驾"),
    "/resource/carrctapi/testDrive/reservationDetail/v1": ("试驾预约详情", "试驾"),
    "/resource/carrctapi/testDrive/reservationList/v1": ("试驾预约列表", "试驾"),
    "/resource/carrctapi/testDrive/reservePage/v1": ("试驾预约页面数据", "试驾"),
    "/resource/carrctapi/testDrive/reserveSuccessPage/v1": ("试驾预约成功页面数据", "试驾"),
    "/resource/carrctapi/testDrive/reserveTimeList/v1": ("试驾可预约时间列表", "试驾"),
    "/resource/carrctapi/testDrive/reserveTypeList/v1": ("试驾预约类型列表", "试驾"),
    "/resource/carrctapi/testDrive/savePayResult/v1": ("保存试驾相关支付结果", "试驾"),
    "/action/carrctapi/testDrive/checkInSuccessList/v1": ("试驾签到成功列表", "试驾"),
    "/action/carrctapi/testDrive/goCheckIn/v1": ("执行试驾签到", "试驾"),
    "/action/carrctapi/testDrive/goPay/v1": ("发起试驾相关支付", "试驾"),
    "/action/carrctapi/testDrive/notCheckInlist/v1": ("未签到试驾列表", "试驾"),
    "/action/carrctapi/testDrive/reserve/v1": ("提交试驾预约", "试驾"),
    "/action/carrctapi/testDrive/reserveOrder/cancel/v1": ("取消试驾预约", "试驾"),
    "/action/carrctapi/testDrive/reserveOrder/cancelReasonList/v1": ("试驾取消原因列表", "试驾"),
    "/action/carrctapi/testDrive/reserveTimeCheck/v1": ("校验试驾预约时间", "试驾"),
    "/action/carrctapi/testDrive/scanLandingPage/v1": ("扫描试驾落地页信息", "试驾"),
    "/action/carrctapi/testDrive/share/v1": ("记录或生成试驾分享", "试驾"),
    "/action/carrctapi/user/sendLoginCode/v1": ("发送登录验证码", "用户"),
    "/resource/carrctapi/translate/zh2En/v1": ("中文内容转英文", "通用工具"),
}


def _frontend_entries() -> list[dict]:
    result = []
    for index, (uri, (purpose, category)) in enumerate(FRONTEND_PURPOSES.items(), 1):
        auth = "likely_authenticated" if "/action/" in uri or "reservation" in uri else "unknown"
        result.append(_entry(
            uri, item_id=f"frontend-{index}", purpose=purpose, category=category,
            status="observed", source="神州当前 H5 首页公开前端资源", source_url=FRONTEND_SOURCE,
            auth=auth, evidence="URI 字符串直接出现在当前公开前端构建资源中；尚未发送探测请求。",
        ))
    return result


REFERENCE_LEADS = {
    "/resource/carrctapi/base/cityList/v1": "候选基础城市列表",
    "/resource/carrctapi/chooseCar/cityList/v1": "候选选车城市列表",
    "/resource/carrctapi/common/cityList/v1": "候选通用城市列表",
    "/resource/carrctapi/dept/cityList/v1": "候选网点城市列表",
    "/resource/carrctapi/dept/openCityList/v1": "候选网点开放城市列表",
    "/resource/carrctapi/index/cityList/v1": "候选首页城市列表",
    "/resource/carrctapi/order/cityList/v1": "候选订单城市列表",
    "/resource/carrctapi/order/getCityList/v1": "候选订单城市目录",
    "/resource/carrctapi/order/openCity/v1": "候选订单开放城市",
    "/resource/carrctapi/rent/cityList/v1": "候选租车城市列表",
}


def _lead_entries() -> list[dict]:
    return [_entry(
        uri, item_id=f"lead-{index}", purpose=purpose, category="城市与定位",
        status="lead", source="参考项目探测脚本", source_url=REFERENCE_SOURCE,
        evidence="该 URI 来自枚举候选名称的调试脚本，没有足够证据证明接口存在或可用。",
    ) for index, (uri, purpose) in enumerate(REFERENCE_LEADS.items(), 1)]


def upstream_catalog() -> dict:
    items = [*CORE_ENTRIES, *_frontend_entries(), *_lead_entries()]
    items.sort(key=lambda item: (item["category"], item["status"], item["uri"]))
    return {
        "scope": SCOPE,
        "count": len(items),
        "observed_at": OBSERVED_AT,
        "categories": sorted({item["category"] for item in items}),
        "status_counts": dict(Counter(item["status"] for item in items)),
        "items": items,
    }


class UpstreamProbeError(RuntimeError):
    pass


class UpstreamProbeService:
    SAFE_IDS = {"city-list-v1", "city-location-v1", "dept-list-v1", "choose-car-v3"}

    def __init__(self, gateway_factory) -> None:
        self.gateway_factory = gateway_factory

    async def probe(self, endpoint_id: str) -> dict:
        if endpoint_id not in self.SAFE_IDS:
            raise KeyError(endpoint_id)
        try:
            async with _gateway(self.gateway_factory()) as gateway:
                if endpoint_id == "city-list-v1":
                    summary = f"返回 {len(await gateway.list_cities())} 个城市"
                elif endpoint_id == "city-location-v1":
                    city = await gateway.resolve_city(23.10161, 113.432649)
                    summary = f"识别为{city.city_name}（城市 ID {city.city_id}）"
                elif endpoint_id == "dept-list-v1":
                    departments = await gateway.list_departments("14")
                    summary = f"广州目录返回 {len(departments)} 个网点"
                else:
                    payload = await gateway.choose_car(_fish_weekend_query())
                    departments = (payload.get("content") or {}).get("deptHangModels") or []
                    offers = sum(len(item.get("models") or []) for item in departments
                                 if isinstance(item, dict))
                    summary = f"返回 {len(departments)} 个网点、{offers} 条车型报价"
        except Exception as error:
            raise UpstreamProbeError("神州接口探测失败") from error
        return {"ok": True, "endpoint_id": endpoint_id,
                "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "summary": summary}


def _fish_weekend_query() -> ScanQuery:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    days_until_saturday = (5 - now.weekday()) % 7
    if days_until_saturday == 0 and now.hour >= 9:
        days_until_saturday = 7
    pickup = (now + timedelta(days=days_until_saturday)).replace(hour=9, minute=0, second=0,
                                                                  microsecond=0)
    return ScanQuery(city_id="14", location_name="鱼珠地铁站服务点", latitude=23.10161,
                     longitude=113.432649, pickup_time=pickup, return_time=pickup + timedelta(days=1))


@asynccontextmanager
async def _gateway(client):
    if hasattr(client, "__aenter__"):
        async with client as entered:
            yield entered
        return
    try:
        yield client
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result
