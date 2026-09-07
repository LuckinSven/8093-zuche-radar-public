import re
from collections.abc import Mapping

from app.schemas import ParsedAvailability, ParsedDepartment, ParsedGroupMembership, ParsedScan, ScanRequest
from app.energy import classify_energy


def parse_choose_car(payload: Mapping[str, object], request: ScanRequest) -> ParsedScan:
    """将神州原始车型响应转换为稳定的内部扫描结构。"""

    content = payload.get("content")
    if not isinstance(content, Mapping):
        raise ValueError("神州响应缺少 content")

    groups = [
        ParsedGroupMembership(
            zuche_model_id=model_id,
            group_id=group_id,
            group_name=str(group.get("name") or "未命名分组"),
        )
        for group in _mapping_list(content.get("modelGroups"))
        if (group_id := _int(group.get("groupId"))) is not None
        for model in _mapping_list(group.get("modelItems"))
        if (model_id := _int(model.get("modelId"))) is not None
    ]
    group_names: dict[int, list[str]] = {}
    for group in groups:
        group_names.setdefault(group.zuche_model_id, []).append(group.group_name)
    departments: list[ParsedDepartment] = []
    availabilities: list[ParsedAvailability] = []
    for item in _mapping_list(content.get("deptHangModels")):
        dept_id = _int(item.get("deptId"))
        if dept_id is None:
            continue
        models = _mapping_list(item.get("models"))
        self_service = _department_self_service(models)
        departments.append(
            ParsedDepartment(
                zuche_dept_id=dept_id,
                name=str(item.get("deptName") or "未命名服务点"),
                address=_optional_string(item.get("deptAddress")),
                latitude=_float(item.get("lat")),
                longitude=_float(item.get("lon")),
                distance_km=_distance_km(item.get("deptDistanceDouble"), item.get("deptDistance")),
                business_hours=_optional_string(item.get("workTime")),
                is_open_24h=_optional_bool(item.get("wholeDayFlag")),
                self_service_pickup=self_service,
                self_service_return=self_service,
            )
        )
        for model in models:
            model_id = _int(model.get("modelId"))
            if model_id is None:
                continue
            model_desc = _optional_string(model.get("modelDesc"))
            model_name = str(model.get("modelName") or "未命名车型")
            body_style, seat_count = _parse_model_desc(model_desc)
            availabilities.append(
                ParsedAvailability(
                    zuche_dept_id=dept_id,
                    zuche_model_id=model_id,
                    model_name=model_name,
                    daily_price=_float(model.get("dailyPrice")),
                    package_price=_float(model.get("packagePrice")),
                    model_desc=model_desc,
                    body_style=body_style,
                    seat_count=seat_count,
                    energy_type=classify_energy(model_desc, model_name, group_names.get(model_id, [])),
                    book_flag=bool(model.get("bookFlag")),
                    inventory_type=_int(model.get("inventoryType")),
                    department_distance=_optional_string(item.get("deptDistance")),
                )
            )

    return ParsedScan(request=request, departments=departments, availabilities=availabilities, groups=groups)


def _parse_model_desc(model_desc: str | None) -> tuple[str | None, int | None]:
    if not model_desc:
        return None, None
    body_match = re.search(r"(SUV|MPV|两厢|三厢|跑车|皮卡)(?=\d座|\s|$)", model_desc, re.IGNORECASE)
    seat_match = re.search(r"(\d)座", model_desc)
    return (
        body_match.group(1).upper() if body_match else None,
        int(seat_match.group(1)) if seat_match else None,
    )


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _int(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        return float(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_string(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _distance_km(numeric_value: object, display_value: object) -> float | None:
    numeric = _float(numeric_value)
    if numeric is not None:
        return numeric
    match = re.search(r"([\d.]+)\s*km", str(display_value), re.IGNORECASE)
    return float(match.group(1)) if match else None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "是"}:
            return True
        if normalized in {"0", "false", "no", "否"}:
            return False
        return None
    return bool(value)


def _department_self_service(models: list[Mapping[str, object]]) -> bool | None:
    values = [_optional_bool(model.get("selfServiceFlag")) for model in models
              if "selfServiceFlag" in model]
    known_values = [value for value in values if value is not None]
    return any(known_values) if known_values else None
