"""兼容旧导入；新代码统一使用 app.scheduling。"""

from app.scheduling import ProbeScheduler

__all__ = ["ProbeScheduler"]
