import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import distinct, func, select

from app.database import SessionFactory
from app.domain import ScanQuery
from app.models import AvailabilitySnapshot
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger
from app.zuche.client import ZucheClient


def format_summary(scan_id: str, status: str, departments: int, offers: int, models: int) -> str:
    return f"扫描ID: {scan_id}\n状态: {status}\n网点数: {departments}\n报价数: {offers}\n车型数: {models}"


async def run(args) -> int:
    query = ScanQuery(city_id=args.city_id, location_name=args.location, latitude=args.lat, longitude=args.lon,
                      pickup_time=datetime.fromisoformat(args.pickup), return_time=datetime.fromisoformat(args.return_time))
    session = SessionFactory()
    try:
        async with ZucheClient() as gateway:
            result = await ScanService(gateway, ScanRepository(session)).run(query, ScanTrigger.MANUAL)
        models = int(session.scalar(select(func.count(distinct(AvailabilitySnapshot.vehicle_model_id))).where(
            AvailabilitySnapshot.scan_run_id == result.scan_id)) or 0)
        print(format_summary(str(result.scan_id), result.status, result.department_count, result.offer_count, models))
        if result.error_message:
            print(f"错误: {result.error_message}")
        return 0 if result.status == "SUCCESS" else 1
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="执行一次神州车型雷达真实扫描")
    parser.add_argument("--city-id", default="14")
    parser.add_argument("--location", default="广州中心")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--pickup", required=True)
    parser.add_argument("--return", dest="return_time", required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
