import re
from collections.abc import Iterable


_TEXT_MARKERS = ("新能源", "纯电", "插电混", "插混", "增程")
_GROUP_MARKERS = ("新能源", "纯电", "插电混", "插混", "增程")
_KWH = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*kwh(?![A-Za-z])", re.IGNORECASE)


def classify_energy(desc: str | None, model_name: str | None,
                    native_group_names: Iterable[str] = ()) -> str | None:
    """只在神州返回了明确新能源证据时标记车型。"""

    text = " ".join(value for value in (desc, model_name) if value)
    if any(marker in text for marker in _TEXT_MARKERS) or _KWH.search(text):
        return "新能源"
    if any(marker in group_name for group_name in native_group_names for marker in _GROUP_MARKERS):
        return "新能源"
    return None
