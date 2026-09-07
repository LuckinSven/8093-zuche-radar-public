"""把 FastAPI 的 OpenAPI 定义整理成适合个人查看和交给 AI 的中文目录。"""

from collections import OrderedDict


METHODS = {"get", "post", "put", "patch", "delete"}

CATEGORY_RULES = (
    ("车型 AI 补全", ("/api/vehicle-enrichment",)),
    ("跨城专项找车", ("/api/cross-city-search",)),
    ("按车型找车", ("/api/model-search",)),
    ("广州全城车型", ("/api/citywide", "/api/model-library")),
    ("找车与扫描", ("/api/scans", "/api/discovery", "/api/data")),
    ("网点与城市", ("/api/departments", "/api/cities", "/api/probes", "/api/zuche")),
    ("地图与系统设置", ("/api/locations", "/api/settings", "/api/map")),
    ("车型资料", ("/api/models",)),
)

DESCRIPTIONS = {
    ("PUT", "/api/settings/integrations/openai-compatible"): "保存任意 OpenAI 兼容 Chat Completions 接口的地址、模型、超时、重试和启用状态；车型固定逐款请求，密钥只保存于服务端且读取时脱敏。",
    ("DELETE", "/api/settings/integrations/openai-compatible/secret"): "清除已保存的 AI 接口密钥，并同步停用车型补全。",
    ("POST", "/api/settings/integrations/openai-compatible/test"): "用固定纯电车型测试连接、JSON 模式和结构化字段；不会修改车型库。",
    ("POST", "/api/vehicle-enrichment-runs"): "手动创建车型能源补全任务；PENDING_ONLY 只处理未知、低置信或缺细分车型，ALL 重识别全部非人工车型。逐车型初判和 AI 二次识别都只有高置信有效结果才写入车型库。",
    ("GET", "/api/vehicle-enrichment-runs"): "分页读取 AI 补全历史，包括范围、状态、进度、更新、未知、失败、请求数和 Token 用量。",
    ("GET", "/api/vehicle-enrichment-runs/{run_id}"): "读取任务统计以及最多 100 条等待、保持未知或失败的车型摘要；错误内容经过安全处理。",
    ("DELETE", "/api/vehicle-enrichment-runs/{run_id}"): "删除已结束任务及其识别明细；不会删除车型库或撤销已经写入的能源信息。等待、运行或仍有请求收尾的任务不能删除。",
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/stop"): "停止运行中的补全任务；已经应用的车型结论和任务历史会保留。",
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/resume"): "继续已停止或因 Docker 重启中断的补全任务。",
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/retry-failed"): "重新处理部分完成任务中的失败车型，不重复创建已成功结果。",
    ("PUT", "/api/vehicle-enrichment-runs/{run_id}/results/{model_id}/manual-resolution"): "人工处理已结束任务中失败或仍未知的车型；结论写入永久车型库并立即重算任务统计，人工结论不会被后续 AI 覆盖。",
    ("POST", "/api/citywide-scans"): "按当前广州网点目录创建一次手动全城扫描；取还车时间必须包含时区，租期最长 30 天，同一城市同时只运行一个任务。",
    ("GET", "/api/citywide-scans"): "分页读取全城扫描任务及计划网点数、完成数、失败数、新车型数和当前状态。",
    ("GET", "/api/citywide-scans/{run_id}"): "读取一个全城扫描任务的进度、租期、结果计数和安全错误摘要。",
    ("POST", "/api/citywide-scans/{run_id}/stop"): "停止正在运行的全城扫描；已经完成的网点结果会保留。",
    ("POST", "/api/citywide-scans/{run_id}/resume"): "继续已停止或因重启中断的全城扫描。",
    ("POST", "/api/citywide-scans/{run_id}/retry-failed"): "重新执行部分完成任务中的失败网点。",
    ("GET", "/api/citywide-models"): "按指定租期任务汇总每款车型的可取网点、鱼珠距离、均价和价格区间；当前租期状态分为 AVAILABLE、NOT_FOUND、INCOMPLETE。",
    ("GET", "/api/citywide-models/{model_id}/offers"): "读取指定租期中一款车型的实际可取网点及各网点报价；报价是神州基础/列表价，不是最终结算价。",
    ("GET", "/api/model-library"): "分页读取永久车型库；无需租期，可按最近发现、首次发现或车型名称排序，并返回细节车型数、车型名称数和近 7 天新增汇总。车型只增加、不自动删除。",
    ("GET", "/api/model-library/{model_id}"): "读取永久车型档案，以及可选租期中的当前状态、均价、区间和可取网点摘要。",
    ("POST", "/api/model-search-runs"): "从永久车型库选择一个或多个车型名称，手动创建未来四个周末的广州反向找车任务；同名细节车型会全部纳入，超过 2000 次估算请求自动降为单并发，超过 5000 次拒绝启动。",
    ("GET", "/api/model-search-runs"): "分页读取按车型找车任务，包括阶段、完成/失败样本、缓存命中、上游请求、发现车型和可取网点数量。",
    ("GET", "/api/model-search-runs/{run_id}"): "读取一个按车型找车任务的实时进度、最终成功状态、自动重试次数和仍未恢复的安全错误摘要。",
    ("POST", "/api/model-search-runs/{run_id}/stop"): "停止等待或运行中的按车型找车任务；已经完成的样本和报价继续保留。",
    ("POST", "/api/model-search-runs/{run_id}/resume"): "继续已停止或因 Docker 重启中断的按车型找车任务。",
    ("GET", "/api/model-search-runs/{run_id}/results"): "按车型名称汇总任务结果；可租表示至少有一个实际网点，未找到只在任务完整结束后成立，否则一律标为扫描不完整。",
    ("GET", "/api/model-search-runs/{run_id}/periods"): "读取指定车型名称的准确取还时间、24/48 小时租期、细节车型和实际可取网点及其价格；可按可租、未找到、扫描不完整筛选租期明细。",
    ("POST", "/api/cross-city-search-runs"): "按稳定的神州车型 ID、多个取车城市、一个还车城市和一组严格 14 天租期创建跨城专项找车任务；逐个取车网点扫描，预计上游请求超过 2000 次时拒绝创建。",
    ("GET", "/api/cross-city-search-runs"): "分页读取跨城专项找车历史和实时进度，仅返回跨城类型任务。",
    ("GET", "/api/cross-city-search-runs/{run_id}"): "读取跨城任务的目标细节车型、取车城市、还车城市、还车地点提示、准确租期、铁路费用估算和执行进度。",
    ("GET", "/api/cross-city-search-runs/{run_id}/results"): "按取车城市和准确租期汇总可取网点、最低日均价、14 天估算租金、两人单程铁路费及估算总成本；状态严格区分 AVAILABLE、NOT_FOUND、INCOMPLETE。",
    ("POST", "/api/cross-city-search-runs/{run_id}/stop"): "停止跨城专项找车任务；已经完成的样本和报价继续保留。",
    ("POST", "/api/cross-city-search-runs/{run_id}/resume"): "继续已停止或因 Docker 重启中断的跨城专项找车任务。",
    ("GET", "/api/model-library/options"): "按名称搜索永久车型库，返回可供按车型找车多选的名称、细节车型数量和最近发现时间。",
    ("PUT", "/api/models/{model_id}/energy"): "在车型详情中人工确认或重新修改能源大类、细分类型和核对依据；保留独立审计记录。",
    ("POST", "/api/scans"): "按地点、坐标和取还车时间立即查询神州附近车源，并保存扫描结果。",
    ("GET", "/api/scans/{scan_id}"): "读取单次扫描的状态、租期、结果数量和变化事件。",
    ("GET", "/api/data/scans"): "列出可供数据表选择的成功扫描及其车型、网点数量。",
    ("GET", "/api/data/rows"): "按扫描、网点、价格、能源和车身等条件读取逐行报价数据。",
    ("GET", "/api/data/export.csv"): "按相同筛选条件导出 Excel 可打开的 CSV。",
    ("GET", "/api/departments"): "分页读取系统已经保存的神州网点目录。",
    ("POST", "/api/zuche/departments/sync"): "从神州匿名网点目录同步所选城市的区域、网点、地址、坐标和营业信息；不会删除历史记录。",
    ("GET", "/healthz"): "检查应用和数据库是否正常。",
}

SUMMARIES = {
    ("PUT", "/api/settings/integrations/openai-compatible"): "保存 AI 补全配置",
    ("DELETE", "/api/settings/integrations/openai-compatible/secret"): "清除 AI 补全密钥",
    ("POST", "/api/settings/integrations/openai-compatible/test"): "测试 AI 补全接口",
    ("POST", "/api/vehicle-enrichment-runs"): "创建车型能源 AI 补全任务",
    ("GET", "/api/vehicle-enrichment-runs"): "列出 AI 补全任务",
    ("GET", "/api/vehicle-enrichment-runs/{run_id}"): "读取 AI 补全任务详情",
    ("DELETE", "/api/vehicle-enrichment-runs/{run_id}"): "删除 AI 补全历史任务",
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/stop"): "停止 AI 补全任务",
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/resume"): "继续 AI 补全任务",
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/retry-failed"): "重试失败车型",
    ("PUT", "/api/vehicle-enrichment-runs/{run_id}/results/{model_id}/manual-resolution"): "人工处理失败或未知车型",
    ("POST", "/api/citywide-scans"): "创建广州全城扫描",
    ("GET", "/api/citywide-scans"): "列出全城扫描任务",
    ("GET", "/api/citywide-scans/{run_id}"): "读取全城扫描进度",
    ("POST", "/api/citywide-scans/{run_id}/stop"): "停止全城扫描",
    ("POST", "/api/citywide-scans/{run_id}/resume"): "继续全城扫描",
    ("POST", "/api/citywide-scans/{run_id}/retry-failed"): "重试失败网点",
    ("GET", "/api/citywide-models"): "查询指定租期的全城车型",
    ("GET", "/api/citywide-models/{model_id}/offers"): "读取车型的网点报价",
    ("GET", "/api/model-library"): "查询永久车型库",
    ("GET", "/api/model-library/{model_id}"): "读取永久车型档案",
    ("POST", "/api/model-search-runs"): "创建按车型找车任务",
    ("GET", "/api/model-search-runs"): "列出按车型找车任务",
    ("GET", "/api/model-search-runs/{run_id}"): "读取按车型找车进度",
    ("POST", "/api/model-search-runs/{run_id}/stop"): "停止按车型找车任务",
    ("POST", "/api/model-search-runs/{run_id}/resume"): "继续按车型找车任务",
    ("GET", "/api/model-search-runs/{run_id}/results"): "汇总按车型找车结果",
    ("GET", "/api/model-search-runs/{run_id}/periods"): "读取车型准确租期和网点",
    ("POST", "/api/cross-city-search-runs"): "创建跨城专项找车任务",
    ("GET", "/api/cross-city-search-runs"): "列出跨城专项找车任务",
    ("GET", "/api/cross-city-search-runs/{run_id}"): "读取跨城专项找车详情",
    ("GET", "/api/cross-city-search-runs/{run_id}/results"): "汇总跨城专项找车结果",
    ("POST", "/api/cross-city-search-runs/{run_id}/stop"): "停止跨城专项找车任务",
    ("POST", "/api/cross-city-search-runs/{run_id}/resume"): "继续跨城专项找车任务",
    ("GET", "/api/model-library/options"): "搜索可选车型名称",
    ("POST", "/api/scans"): "创建立即扫描",
    ("GET", "/api/scans/{scan_id}"): "读取扫描详情",
    ("GET", "/api/history"): "读取扫描历史",
    ("GET", "/api/discovery"): "汇总车型发现结果",
    ("GET", "/api/discovery/summary"): "读取扫描汇总",
    ("GET", "/api/models/{model_id}"): "读取车型档案",
    ("PUT", "/api/models/{model_id}/energy"): "人工确认车型能源",
    ("GET", "/api/cities"): "列出已保存城市",
    ("POST", "/api/cities"): "新增城市",
    ("GET", "/api/probes"): "列出扫描点",
    ("POST", "/api/probes"): "新增扫描点",
    ("PATCH", "/api/cities/{city_id}"): "更新城市",
    ("DELETE", "/api/cities/{city_id}"): "删除城市",
    ("PATCH", "/api/probes/{probe_id}"): "更新扫描点",
    ("DELETE", "/api/probes/{probe_id}"): "删除扫描点",
    ("PUT", "/api/models/{model_id}/personal-state"): "保存车型个人状态",
    ("POST", "/api/models/{model_id}/annotations"): "新增车型补充信息",
    ("GET", "/api/models/{model_id}/annotations"): "读取车型补充信息",
    ("GET", "/api/settings/integrations"): "读取集成配置",
    ("PUT", "/api/settings/integrations/baidu_maps"): "保存百度地图配置",
    ("DELETE", "/api/settings/integrations/baidu_maps/secret"): "清除百度地图密钥",
    ("POST", "/api/settings/integrations/baidu_maps/test"): "测试百度地图服务",
    ("GET", "/api/locations/search"): "搜索地图地点",
    ("GET", "/api/settings/map-cache"): "读取地图缓存",
    ("DELETE", "/api/settings/map-cache"): "清空地图缓存",
    ("POST", "/api/settings/map-cache/refresh"): "刷新地图缓存",
    ("DELETE", "/api/settings/map-cache/item"): "删除单条地图缓存",
    ("GET", "/api/zuche/cities"): "读取神州城市目录统计",
    ("POST", "/api/zuche/cities/test"): "测试神州城市目录",
    ("POST", "/api/zuche/cities/sync"): "同步神州城市目录",
    ("POST", "/api/zuche/departments/sync"): "同步神州网点目录",
    ("GET", "/api/zuche/upstream"): "读取神州上游接口台账",
    ("POST", "/api/zuche/upstream/probe/{endpoint_id}"): "安全探测指定神州上游接口",
    ("GET", "/api/departments"): "列出已保存网点",
    ("GET", "/api/departments/summary"): "读取网点统计",
    ("GET", "/api/departments/discovery-runs"): "列出网点发现任务",
    ("POST", "/api/departments/discovery-runs"): "创建网点发现任务",
    ("POST", "/api/departments/discovery-runs/stop"): "停止网点发现任务",
    ("POST", "/api/departments/discovery-runs/resume"): "继续网点发现任务",
    ("GET", "/api/data/scans"): "列出数据表扫描批次",
    ("GET", "/api/data/rows"): "读取报价数据行",
    ("GET", "/api/data/export.csv"): "导出报价 CSV",
    ("GET", "/healthz"): "检查系统健康状态",
}

EXAMPLES = {
    ("PUT", "/api/settings/integrations/openai-compatible"): '''curl -X PUT "{base_url}/api/settings/integrations/openai-compatible" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"api_key":"在本机填写实际密钥","base_url":"https://api.deepseek.com","model":"deepseek-chat","batch_size":1,"timeout_seconds":90,"max_retries":2}' ''',
    ("POST", "/api/settings/integrations/openai-compatible/test"): 'curl -X POST "{base_url}/api/settings/integrations/openai-compatible/test"',
    ("POST", "/api/vehicle-enrichment-runs"): '''curl -X POST "{base_url}/api/vehicle-enrichment-runs" \
  -H "Content-Type: application/json" \
  -d '{"scope":"PENDING_ONLY"}' ''',
    ("GET", "/api/vehicle-enrichment-runs"): 'curl "{base_url}/api/vehicle-enrichment-runs?page=1&page_size=20"',
    ("GET", "/api/vehicle-enrichment-runs/{run_id}"): 'curl "{base_url}/api/vehicle-enrichment-runs/任务编号"',
    ("DELETE", "/api/vehicle-enrichment-runs/{run_id}"): 'curl -X DELETE "{base_url}/api/vehicle-enrichment-runs/任务编号"',
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/stop"): 'curl -X POST "{base_url}/api/vehicle-enrichment-runs/任务编号/stop"',
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/resume"): 'curl -X POST "{base_url}/api/vehicle-enrichment-runs/任务编号/resume"',
    ("POST", "/api/vehicle-enrichment-runs/{run_id}/retry-failed"): 'curl -X POST "{base_url}/api/vehicle-enrichment-runs/任务编号/retry-failed"',
    ("PUT", "/api/vehicle-enrichment-runs/{run_id}/results/{model_id}/manual-resolution"): '''curl -X PUT "{base_url}/api/vehicle-enrichment-runs/任务编号/results/神州车型编号/manual-resolution" \
  -H "Content-Type: application/json" \
  -d '{"energy_type":"新能源","energy_subtype":"插电混动","note":"品牌官网已核对"}' ''',
    ("POST", "/api/citywide-scans"): '''curl -X POST "{base_url}/api/citywide-scans" \\
  -H "Content-Type: application/json" \\
  -d '{"city_id":1,"pickup_time":"2030-01-05T09:00:00+08:00","return_time":"2030-01-07T09:00:00+08:00"}' ''',
    ("GET", "/api/citywide-scans/{run_id}"): 'curl "{base_url}/api/citywide-scans/任务编号"',
    ("POST", "/api/citywide-scans/{run_id}/stop"): 'curl -X POST "{base_url}/api/citywide-scans/任务编号/stop"',
    ("POST", "/api/citywide-scans/{run_id}/resume"): 'curl -X POST "{base_url}/api/citywide-scans/任务编号/resume"',
    ("POST", "/api/citywide-scans/{run_id}/retry-failed"): 'curl -X POST "{base_url}/api/citywide-scans/任务编号/retry-failed"',
    ("GET", "/api/citywide-models"): 'curl "{base_url}/api/citywide-models?run_id=任务编号&q=比亚迪海狮05&availability=AVAILABLE&page_size=100"',
    ("GET", "/api/citywide-models/{model_id}/offers"): 'curl "{base_url}/api/citywide-models/车型编号/offers?run_id=任务编号"',
    ("GET", "/api/model-library"): 'curl "{base_url}/api/model-library?q=比亚迪海狮05&page_size=100"',
    ("GET", "/api/model-library/{model_id}"): 'curl "{base_url}/api/model-library/车型编号?run_id=任务编号"',
    ("POST", "/api/model-search-runs"): '''curl -X POST "{base_url}/api/model-search-runs" \
  -H "Content-Type: application/json" \
  -d '{"model_names":["比亚迪海狮05","小鹏MONA M03"]}' ''',
    ("GET", "/api/model-search-runs"): 'curl "{base_url}/api/model-search-runs?page=1&page_size=20"',
    ("GET", "/api/model-search-runs/{run_id}"): 'curl "{base_url}/api/model-search-runs/任务编号"',
    ("POST", "/api/model-search-runs/{run_id}/stop"): 'curl -X POST "{base_url}/api/model-search-runs/任务编号/stop"',
    ("POST", "/api/model-search-runs/{run_id}/resume"): 'curl -X POST "{base_url}/api/model-search-runs/任务编号/resume"',
    ("GET", "/api/model-search-runs/{run_id}/results"): 'curl "{base_url}/api/model-search-runs/任务编号/results?availability=AVAILABLE&page_size=20"',
    ("GET", "/api/model-search-runs/{run_id}/periods"): 'curl "{base_url}/api/model-search-runs/任务编号/periods?model_name=比亚迪海狮05&page_size=20"',
    ("POST", "/api/cross-city-search-runs"): '''curl -X POST "{base_url}/api/cross-city-search-runs" \
  -H "Content-Type: application/json" \
  -d '{"zuche_model_id":1001,"pickup_city_ids":[1,2],"return_city_id":1,"return_location_name":"计划还车网点","windows":[{"pickup_time":"2030-01-01T09:00:00+08:00","return_time":"2030-01-15T09:00:00+08:00"}],"rail_costs":{"1":0,"2":300}}' ''',
    ("GET", "/api/cross-city-search-runs"): 'curl "{base_url}/api/cross-city-search-runs?page=1&page_size=20"',
    ("GET", "/api/cross-city-search-runs/{run_id}"): 'curl "{base_url}/api/cross-city-search-runs/任务编号"',
    ("GET", "/api/cross-city-search-runs/{run_id}/results"): 'curl "{base_url}/api/cross-city-search-runs/任务编号/results"',
    ("POST", "/api/cross-city-search-runs/{run_id}/stop"): 'curl -X POST "{base_url}/api/cross-city-search-runs/任务编号/stop"',
    ("POST", "/api/cross-city-search-runs/{run_id}/resume"): 'curl -X POST "{base_url}/api/cross-city-search-runs/任务编号/resume"',
    ("GET", "/api/model-library/options"): 'curl "{base_url}/api/model-library/options?q=海狮&limit=20"',
    ("PUT", "/api/models/{model_id}/energy"): '''curl -X PUT "{base_url}/api/models/车型编号/energy" \
  -H "Content-Type: application/json" \
  -d '{"energy_type":"燃油","energy_subtype":"汽油","note":"行驶证已核对"}' ''',
    ("POST", "/api/scans"): '''curl -X POST "{base_url}/api/scans" \\
  -H "Content-Type: application/json" \\
  -d '{"city_id":"城市编号","location_name":"取车网点名称","latitude":23.00000,"longitude":113.00000,"pickup_time":"2030-01-01T09:00:00+08:00","return_time":"2030-01-02T09:00:00+08:00"}' ''',
    ("GET", "/api/data/rows"): 'curl "{base_url}/api/data/rows?scan_id=扫描编号&bookable=true&page_size=100"',
    ("GET", "/api/departments"): 'curl "{base_url}/api/departments?district=黄埔区&page_size=100"',
}


def build_api_catalog(openapi: dict, base_url: str) -> dict:
    """仅公开实际 API 和健康检查；网页路由不混入接口目录。"""
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    schemas = openapi.get("components", {}).get("schemas", {})
    for path, path_item in openapi.get("paths", {}).items():
        if not (path.startswith("/api/") or path == "/healthz"):
            continue
        for method, operation in path_item.items():
            if method not in METHODS:
                continue
            method_name = method.upper()
            category = _category(path)
            summary = SUMMARIES.get((method_name, path), f"{method_name} {path}")
            groups.setdefault(category, []).append({
                "method": method_name,
                "path": path,
                "summary": summary,
                "description": DESCRIPTIONS.get(
                    (method_name, path), f"{summary}；参数与响应状态来自当前 OpenAPI 定义。"),
                "write": method_name != "GET",
                "parameters": _parameters(path_item, operation),
                "body_fields": _body_fields(operation, schemas),
                "responses": sorted(operation.get("responses", {}).keys()),
                "example": EXAMPLES.get((method_name, path),
                                        f'curl -X {method_name} "{{base_url}}{path}"').replace(
                                            "{base_url}", base_url),
            })
    return {"groups": [{"name": name, "items": items} for name, items in groups.items()],
            "count": sum(len(items) for items in groups.values())}


def _category(path: str) -> str:
    for name, prefixes in CATEGORY_RULES:
        if path.startswith(prefixes):
            return name
    return "系统与其他"


def _parameters(path_item: dict, operation: dict) -> list[dict]:
    result = []
    for item in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
        schema = item.get("schema", {})
        result.append({"name": item.get("name"), "location": item.get("in"),
                       "required": bool(item.get("required")),
                       "type": schema.get("type") or _ref_name(schema),
                       "default": schema.get("default")})
    return result


def _body_fields(operation: dict, schemas: dict) -> list[dict]:
    schema = (operation.get("requestBody", {}).get("content", {})
              .get("application/json", {}).get("schema", {}))
    if "$ref" in schema:
        schema = schemas.get(_ref_name(schema), {})
    required = set(schema.get("required", []))
    return [{"name": name, "required": name in required,
             "type": field.get("type") or _ref_name(field)}
            for name, field in schema.get("properties", {}).items()]


def _ref_name(schema: dict) -> str:
    return str(schema.get("$ref", "")).rsplit("/", 1)[-1] or "object"
