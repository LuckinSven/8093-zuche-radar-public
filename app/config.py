"""兼容旧导入；新代码统一使用 app.settings。"""

from app.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
