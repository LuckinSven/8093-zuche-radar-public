"""兼容旧导入；新代码统一使用 app.scanning.service。"""

from app.scanning.service import ScanEvent as ScanEventResult
from app.scanning.service import ScanResult as ScanRunResult
from app.scanning.service import ScanService, ScanTrigger

__all__ = ["ScanEventResult", "ScanRunResult", "ScanService", "ScanTrigger"]
