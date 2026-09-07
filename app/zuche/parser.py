import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.domain import ParsedDepartment, ParsedGroup, ParsedOffer, ParsedScan, ScanQuery
from app.energy import classify_energy


def parse_choose_car(payload: Mapping[str, object], query: ScanQuery) -> ParsedScan:
    content = payload.get("content")
    if not isinstance(content, Mapping):
        raise ValueError("神州响应缺少 content")
    groups: list[ParsedGroup] = []
    group_keys: set[tuple[int, int]] = set()
    for group in _items(content.get("modelGroups")):
        group_id = _integer(group.get("groupId"))
        if group_id is None:
            continue
        for model in _items(group.get("modelItems")):
            model_id = _integer(model.get("modelId"))
            if model_id is None:
                continue
            groups.append(ParsedGroup(
                model_id=model_id,
                group_id=group_id,
                group_name=str(group.get("name") or "未命名分组"),
                group_low_price_desc=_text(group.get("lowPriceDesc")),
                sort_num=_integer(group.get("sortNum")),
                model_name=_text(model.get("modelName")),
                model_low_price_desc=_text(model.get("lowPriceDesc")),
                model_image_url=_text(model.get("modelImgUrl")),
            ))
            group_keys.add((model_id, group_id))
    group_names: dict[int, list[str]] = {}
    for group in groups:
        group_names.setdefault(group.model_id, []).append(group.group_name)
    departments, offers = [], []
    for department in _items(content.get("deptHangModels")):
        department_id = _integer(department.get("deptId"))
        if department_id is None:
            continue
        models = _items(department.get("models"))
        self_service = _department_self_service(models)
        distance = _department_distance_km(department)
        departments.append(ParsedDepartment(department_id=department_id, name=str(department.get("deptName") or "未命名网点"),
            address=_text(department.get("deptAddress")), latitude=_number(department.get("lat")),
            longitude=_number(department.get("lon")), distance_km=distance,
            business_hours=_text(department.get("workTime")),
            is_open_24h=_optional_bool(department.get("wholeDayFlag")),
            self_service_pickup=self_service, self_service_return=self_service))
        for model in models:
            model_id = _integer(model.get("modelId"))
            if model_id is None:
                continue
            desc = _text(model.get("modelDesc"))
            model_name = str(model.get("modelName") or "未命名车型")
            model_group_id = _integer(model.get("modelGroupId"))
            if model_group_id is not None and (model_id, model_group_id) not in group_keys:
                groups.append(ParsedGroup(
                    model_id=model_id,
                    group_id=model_group_id,
                    group_name="未命名分组",
                    model_name=model_name,
                    model_low_price_desc=_text(model.get("lowPriceDesc")),
                    model_image_url=_text(model.get("modelImgUrl")),
                ))
                group_keys.add((model_id, model_group_id))
            body = re.search(r"(SUV|MPV|两厢|三厢|跑车|皮卡)(?=\d座|\s|$)", desc or "", re.I)
            seats = re.search(r"(\d)座", desc or "")
            offers.append(ParsedOffer(department_id=department_id, model_id=model_id,
                model_name=model_name, daily_price=_decimal(model.get("dailyPrice")),
                package_price=_decimal(model.get("packagePrice")), distance_km=distance,
                distance_text=_text(department.get("deptDistance")), model_desc=desc,
                image_url=_text(model.get("modelImgUrl")),
                body_style=body.group(1).upper() if body else None, seat_count=int(seats.group(1)) if seats else None,
                energy_type=classify_energy(desc, model_name, group_names.get(model_id, [])),
                bookable=bool(model.get("bookFlag")), inventory_type=_integer(model.get("inventoryType"))))
    return ParsedScan(query=query, departments=departments, offers=offers, groups=groups)


def _items(value: object) -> list[Mapping[str, object]]:
    return [x for x in value if isinstance(x, Mapping)] if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _integer(value: object) -> int | None:
    try: return int(str(value)) if value is not None else None
    except (TypeError, ValueError): return None


def _number(value: object) -> float | None:
    try: return float(str(value)) if value is not None else None
    except (TypeError, ValueError): return None


def _decimal(value: object) -> Decimal | None:
    try: return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError): return None


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


def _department_distance_km(department: Mapping[str, object]) -> float | None:
    """神州文本字段使用公里，数值字段 deptDistanceDouble 使用米。"""
    text = str(department.get("deptDistance") or "").strip().lower()
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match:
        value = float(match.group())
        return value / 1000 if "m" in text and "km" not in text else value
    meters = _number(department.get("deptDistanceDouble"))
    return meters / 1000 if meters is not None else None


def _department_self_service(models: list[Mapping[str, object]]) -> bool | None:
    values = [_optional_bool(model.get("selfServiceFlag")) for model in models
              if "selfServiceFlag" in model]
    known_values = [value for value in values if value is not None]
    return any(known_values) if known_values else None
