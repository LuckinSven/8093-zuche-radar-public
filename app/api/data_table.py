import csv
import io
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.api.dependencies import request_session
from app.data_table import DataTableFilters, DataTableService


router = APIRouter(prefix="/data")


@router.get("/scans")
def scans(request: Request):
    with request_session(request) as session:
        return {"items": DataTableService(session).list_scans()}


@router.get("/rows")
def rows(request: Request, filters: Annotated[DataTableFilters, Query()]):
    with request_session(request) as session:
        return DataTableService(session).search(filters)


@router.get("/export.csv")
def export_csv(request: Request, filters: Annotated[DataTableFilters, Query()]):
    with request_session(request) as session:
        data = DataTableService(session).search(filters, paginate=False)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["扫描时间", "扫描地点", "车型ID", "车型名称", "车型组", "能源", "车身",
                     "座位", "网点ID", "网点名称", "网点地址", "神州距离(km)", "坐标复算距离(km)",
                     "日均价", "套餐价", "可租状态", "价格口径"])
    for row in data["items"]:
        writer.writerow([row["started_at"], row["scan_location"], row["model_id"], row["model_name"],
                         " / ".join(row["native_groups"]), row["energy_type"] or "", row["body_style"] or "",
                         row["seat_count"] or "", row["department_id"], row["department_name"],
                         row["department_address"] or "", row["shenzhou_distance_km"] or "",
                         row["coordinate_distance_km"] or "", row["daily_price"] or "",
                         row["package_price"] or "", "可租" if row["bookable"] else "暂不可租", "非最终结算价"])
    filename = f"zuche-data-{data['scan_id'] or 'empty'}.csv"
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
