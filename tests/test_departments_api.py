from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.departments.service import DepartmentDiscoveryService
from app.main import create_app
from app.models import (
    City,
    Department,
    DepartmentActivityState,
    DepartmentDiscoveryPoint,
    DepartmentDiscoveryPointStatus,
    DepartmentDiscoveryRun,
    DepartmentDiscoveryStatus,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _app_for(engine, service=None):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory)
    app.state.department_discovery_service = service or DepartmentDiscoveryService(
        factory,
        lambda: (_ for _ in ()).throw(
            AssertionError("创建和控制任务不应等待神州网络请求")),
    )
    return app, factory


def _city(factory, *, enabled=True, catalog_active=True, name="广州"):
    with factory() as session:
        item = City(
            zuche_city_id="14",
            name=name,
            latitude=23.1291,
            longitude=113.2644,
            enabled=enabled,
            catalog_active=catalog_active,
        )
        session.add(item)
        session.commit()
        return item.id


class RecordingDiscoveryService:
    def __init__(self):
        self.created = []

    def create_run(self, city, **values):
        self.created.append((city, values))
        return DepartmentDiscoveryRun(
            id=uuid4(),
            city_id=city.id,
            preset=values["preset"],
            radius_km=35,
            spacing_km=10,
            max_requests=80,
            pickup_time=values["pickup_time"],
            return_time=values["return_time"],
            status=DepartmentDiscoveryStatus.RUNNING,
            planned_point_count=37,
            completed_point_count=0,
            request_count=0,
            new_department_count=0,
            updated_department_count=0,
            created_at=datetime.now(UTC),
        )


class DirectoryGateway:
    def __init__(self, departments):
        self.departments = departments
        self.city_ids = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def list_departments(self, city_id):
        self.city_ids.append(city_id)
        return self.departments


@pytest.mark.asyncio
async def test_department_directory_sync_saves_all_returned_guangzhou_departments_without_deleting_history(engine):
    app, factory = _app_for(engine, RecordingDiscoveryService())
    city_id = _city(factory)
    with factory() as session:
        session.add(Department(
            zuche_dept_id=999,
            city_id=city_id,
            name="历史网点",
            discovery_source="CHOOSE_CAR",
        ))
        session.commit()
    gateway = DirectoryGateway([
        {
            "deptId": 79340,
            "deptName": "鱼珠地铁站服务点",
            "deptAddress": "广东省广州市黄埔区鱼珠地铁站D出口",
            "deptLat": "23.10161",
            "deptLon": "113.432649",
            "districtName": "黄埔区",
            "workTime": "00:00-24:00",
            "wholeDayFlag": True,
            "selfServiceFlag": True,
            "inventoryAbleFlag": True,
        },
        {
            "deptId": 2033,
            "deptName": "东圃服务点",
            "deptAddress": "广东省广州市天河区中山大道中236号",
            "deptLat": "23.123456",
            "deptLon": "113.401234",
            "districtName": "天河区",
            "workTime": "08:00-21:00",
            "wholeDayFlag": False,
            "selfServiceFlag": False,
            "inventoryAbleFlag": True,
        },
    ])
    app.state.zuche_client_factory = lambda: gateway

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/zuche/departments/sync", json={"city_id": city_id})

    assert response.status_code == 200
    assert response.json() == {
        "city_id": city_id,
        "city_name": "广州",
        "department_count": 2,
        "new_count": 2,
        "updated_count": 0,
    }
    assert gateway.city_ids == ["14"]
    with factory() as session:
        rows = session.query(Department).order_by(Department.zuche_dept_id).all()
        assert [item.zuche_dept_id for item in rows] == [999, 2033, 79340]
        fish = next(item for item in rows if item.zuche_dept_id == 79340)
        assert fish.district == "黄埔区"
        assert fish.business_hours == "00:00-24:00"
        assert fish.is_open_24h is True
        assert fish.self_service_pickup is True
        assert fish.self_service_return is True
        assert fish.discovery_source == "DIRECTORY"


def test_discovery_write_apis_publish_strong_request_schemas():
    openapi = create_app().openapi()
    paths = openapi["paths"]

    create_schema = paths["/api/departments/discovery-runs"]["post"]["requestBody"]
    stop_schema = paths["/api/departments/discovery-runs/stop"]["post"]["requestBody"]
    resume_schema = paths["/api/departments/discovery-runs/resume"]["post"]["requestBody"]

    assert create_schema["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CreateDiscoveryRunInput")
    assert stop_schema["content"]["application/json"]["schema"]["$ref"].endswith(
        "/DiscoveryRunControlInput")
    assert resume_schema["content"]["application/json"]["schema"]["$ref"].endswith(
        "/DiscoveryRunControlInput")
    create_model = openapi["components"]["schemas"]["CreateDiscoveryRunInput"]
    assert "confirmation" in create_model["properties"]


@pytest.mark.asyncio
async def test_deep_run_requires_exact_explicit_confirmation_before_service_call(engine):
    service = RecordingDiscoveryService()
    app, factory = _app_for(engine, service)
    city_id = _city(factory)

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id, "preset": "deep"})
        wrong = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id, "preset": "deep", "confirmation": "确认"})
        accepted = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id,
            "preset": "deep",
            "confirmation": "确认启动深度发现",
        })

    for response in (missing, wrong):
        assert response.status_code == 422
        assert response.json()["detail"] == "深度发现必须提交“确认启动深度发现”"
    assert len(service.created) == 1
    assert service.created[0][1]["preset"] == "deep"
    assert accepted.status_code == 201


@pytest.mark.asyncio
async def test_create_loads_city_and_calls_injected_service_with_shanghai_nine_oclock(engine):
    service = RecordingDiscoveryService()
    app, factory = _app_for(engine, service)
    city_id = _city(factory)
    pickup_date = datetime.now(SHANGHAI).date() + timedelta(days=2)
    return_date = pickup_date + timedelta(days=3)

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id,
            "preset": "standard",
            "pickup_date": pickup_date.isoformat(),
            "return_date": return_date.isoformat(),
        })

    assert response.status_code == 201
    city, values = service.created[0]
    assert (city.id, city.name) == (city_id, "广州")
    assert values["preset"] == "standard"
    assert values["pickup_time"] == datetime.combine(
        pickup_date, datetime.min.time().replace(hour=9), SHANGHAI)
    assert values["return_time"] == datetime.combine(
        return_date, datetime.min.time().replace(hour=9), SHANGHAI)
    assert response.json()["status"] == "RUNNING"
    assert response.json()["status_label"] == "运行中"


@pytest.mark.asyncio
async def test_create_defaults_to_tomorrow_and_one_day_period_at_nine(engine):
    service = RecordingDiscoveryService()
    app, factory = _app_for(engine, service)
    city_id = _city(factory)
    today = datetime.now(SHANGHAI).date()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id,
        })

    assert response.status_code == 201
    _, values = service.created[0]
    assert values["preset"] == "quick"
    assert values["pickup_time"] == datetime.combine(
        today + timedelta(days=1), datetime.min.time().replace(hour=9), SHANGHAI)
    assert values["return_time"] - values["pickup_time"] == timedelta(days=1)


@pytest.mark.asyncio
async def test_only_open_and_enabled_city_can_create_a_run(engine):
    cases = [
        (False, True, "城市尚未启用"),
        (True, False, "城市当前不在开放目录"),
    ]
    for enabled, catalog_active, expected in cases:
        app, factory = _app_for(engine, RecordingDiscoveryService())
        city_id = _city(factory, enabled=enabled, catalog_active=catalog_active)
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/departments/discovery-runs", json={
                "city_id": city_id,
            })
        assert response.status_code == 422
        assert response.json()["detail"] == expected
        with factory() as session:
            session.query(City).delete()
            session.commit()


@pytest.mark.asyncio
async def test_city_without_coordinates_cannot_create_department_discovery(engine):
    service = RecordingDiscoveryService()
    app, factory = _app_for(engine, service)
    with factory() as session:
        city = City(zuche_city_id="998", name="待补坐标城市", latitude=None, longitude=None,
                    enabled=True, catalog_active=True)
        session.add(city)
        session.commit()
        city_id = city.id

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/api/cities")
        response = await client.post("/api/departments/discovery-runs", json={"city_id": city_id})

    item = next(item for item in listing.json()["items"] if item["id"] == city_id)
    assert item["latitude"] is None
    assert item["longitude"] is None
    assert response.status_code == 422
    assert response.json()["detail"] == "城市缺少中心坐标，请先补充"
    assert service.created == []


@pytest.mark.asyncio
async def test_city_api_exposes_catalog_state_for_creation_and_historical_filters(engine):
    app, factory = _app_for(engine, RecordingDiscoveryService())
    open_city_id = _city(factory, enabled=True, catalog_active=True)
    with factory() as session:
        closed = City(
            zuche_city_id="20", name="深圳", latitude=22.5431, longitude=114.0579,
            enabled=True, catalog_active=False,
        )
        session.add(closed)
        session.commit()
        closed_city_id = closed.id

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/cities")

    cities = {item["id"]: item for item in response.json()["items"]}
    assert response.status_code == 200
    assert cities[open_city_id]["catalog_active"] is True
    assert cities[closed_city_id]["catalog_active"] is False


@pytest.mark.asyncio
async def test_only_one_active_city_discovery_can_be_created_without_network_wait(engine):
    app, factory = _app_for(engine)
    city_id = _city(factory)

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id, "preset": "quick"})
        second = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id, "preset": "quick"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "该城市已有活动发现任务，请先停止或等待其结束"


@pytest.mark.asyncio
async def test_create_rejects_unreasonable_period_and_unknown_preset_in_chinese(engine):
    app, factory = _app_for(engine, RecordingDiscoveryService())
    city_id = _city(factory)
    today = datetime.now(SHANGHAI).date()
    future = today + timedelta(days=2)
    cases = [
        ({"city_id": city_id, "preset": "wide"}, "发现预设只能是快速、标准或深度"),
        ({"city_id": city_id, "pickup_date": future.isoformat()}, "取车和还车日期必须同时提供"),
        ({"city_id": city_id, "pickup_date": future.isoformat(),
          "return_date": future.isoformat()},
         "还车日期必须晚于取车日期"),
        ({"city_id": city_id, "pickup_date": future.isoformat(),
          "return_date": (future + timedelta(days=31)).isoformat()},
         "租期最多为 30 天"),
        ({"city_id": city_id, "pickup_date": (today - timedelta(days=2)).isoformat(),
          "return_date": (today - timedelta(days=1)).isoformat()},
         "取车日期不能早于今天"),
    ]

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for payload, expected in cases:
            response = await client.post("/api/departments/discovery-runs", json=payload)
            assert response.status_code == 422
            assert response.json()["detail"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "payload", "expected"), [
    ("GET", "/api/departments?page=0", None, "页码必须是大于 0 的整数"),
    ("GET", "/api/departments?page_size=101", None, "每页数量必须是 1 到 100 的整数"),
    ("GET", "/api/departments?status=UNKNOWN", None, "网点状态不正确"),
    ("GET", "/api/departments/discovery-runs?run_id=broken", None,
     "发现任务编号必须是有效 UUID"),
    ("POST", "/api/departments/discovery-runs", {"city_id": "broken"},
     "城市编号必须是大于 0 的整数"),
    ("POST", "/api/departments/discovery-runs", {"city_id": True},
     "城市编号必须是大于 0 的整数"),
    ("POST", "/api/departments/discovery-runs", {"city_id": 1, "unexpected": True},
     "请求内容包含不支持的字段"),
    ("POST", "/api/departments/discovery-runs/stop", {"run_id": "broken"},
     "发现任务编号必须是有效 UUID"),
    ("POST", "/api/departments/discovery-runs/resume", {},
     "发现任务编号必须是有效 UUID"),
])
async def test_department_validation_errors_are_stable_chinese(
        engine, method, path, payload, expected):
    app, _ = _app_for(engine, RecordingDiscoveryService())
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == expected


@pytest.mark.asyncio
async def test_department_list_filters_pages_in_sql_and_serializes_decimal_and_enums(engine):
    app, factory = _app_for(engine, RecordingDiscoveryService())
    city_id = _city(factory)
    seen_at = datetime(2026, 8, 31, 10, 30, tzinfo=UTC)
    with factory() as session:
        session.add_all([
            Department(
                zuche_dept_id=101, city_id=city_id, name="天河一号店", address="体育西路",
                district="天河区", latitude=Decimal("23.123456"),
                longitude=Decimal("113.123456"), discovery_source="CHOOSE_CAR",
                active_state=DepartmentActivityState.RECENTLY_SEEN,
                first_seen_at=seen_at, last_seen_at=seen_at, last_synced_at=seen_at),
            Department(
                zuche_dept_id=102, city_id=city_id, name="天河二号店", address="珠江新城",
                district="天河区", discovery_source="MANUAL",
                active_state=DepartmentActivityState.RECENTLY_SEEN,
                first_seen_at=seen_at, last_seen_at=seen_at + timedelta(hours=1),
                last_synced_at=seen_at),
            Department(
                zuche_dept_id=103, city_id=city_id, name="越秀门店", district="越秀区",
                discovery_source="CHOOSE_CAR",
                active_state=DepartmentActivityState.LONG_UNSEEN,
                first_seen_at=seen_at, last_seen_at=seen_at + timedelta(hours=2),
                last_synced_at=seen_at),
        ])
        session.commit()

    statements = []

    def record_select(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.upper())

    event.listen(engine, "before_cursor_execute", record_select)
    try:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/departments", params={
                "city_id": city_id, "district": "天河区", "keyword": "店",
                "status": "RECENTLY_SEEN", "page": 2, "page_size": 1,
            })
    finally:
        event.remove(engine, "before_cursor_execute", record_select)

    body = response.json()
    assert response.status_code == 200
    assert body["pagination"] == {"page": 2, "page_size": 1, "total": 2, "pages": 2}
    assert body["items"][0]["name"] == "天河一号店"
    assert body["items"][0]["latitude"] == 23.123456
    assert body["items"][0]["active_state"] == "RECENTLY_SEEN"
    assert body["items"][0]["active_state_label"] == "近期出现"
    assert body["items"][0]["discovery_source_label"] == "选车接口"
    assert body["items"][0]["last_seen_at"].startswith("2026-08-31T10:30:00")
    assert any("DEPARTMENTS" in statement and " LIMIT " in statement
               for statement in statements)


@pytest.mark.asyncio
async def test_department_summary_uses_database_aggregates_and_district_groups(engine):
    app, factory = _app_for(engine, RecordingDiscoveryService())
    city_id = _city(factory)
    seen_at = datetime(2026, 8, 31, 10, 30, tzinfo=UTC)
    with factory() as session:
        session.add_all([
            Department(zuche_dept_id=201, city_id=city_id, name="A", district="天河区",
                       latitude=23.1, longitude=113.1, first_seen_at=seen_at,
                       last_seen_at=seen_at, last_synced_at=seen_at),
            Department(zuche_dept_id=202, city_id=city_id, name="B", district="天河区",
                       first_seen_at=seen_at, last_seen_at=seen_at + timedelta(hours=1),
                       last_synced_at=seen_at),
            Department(zuche_dept_id=203, city_id=city_id, name="C", district="越秀区",
                       latitude=23.2, longitude=113.2, first_seen_at=seen_at,
                       last_seen_at=seen_at + timedelta(hours=2), last_synced_at=seen_at),
        ])
        session.commit()

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/departments/summary", params={"city_id": city_id})

    assert response.status_code == 200
    assert response.json() == {
        "discovered_count": 3,
        "with_coordinates_count": 2,
        "districts": [
            {"district": "天河区", "count": 2},
            {"district": "越秀区", "count": 1},
        ],
        "latest_seen_at": "2026-08-31T12:30:00+00:00",
    }


@pytest.mark.asyncio
async def test_run_list_and_detail_include_sql_grouped_progress_and_safe_status(engine):
    app, factory = _app_for(engine, RecordingDiscoveryService())
    city_id = _city(factory, enabled=False, catalog_active=False)
    created_at = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    with factory() as session:
        run = DepartmentDiscoveryRun(
            city_id=city_id, preset="deep", radius_km=90, spacing_km=7.5,
            max_requests=500,
            pickup_time=datetime(2026, 9, 5, 9, 0, tzinfo=SHANGHAI),
            return_time=datetime(2026, 9, 6, 9, 0, tzinfo=SHANGHAI),
            status=DepartmentDiscoveryStatus.STOPPED,
            planned_point_count=2, completed_point_count=1, request_count=2,
            new_department_count=3, updated_department_count=4,
            created_at=created_at, started_at=created_at,
            last_error_summary="匿名接口安全错误",
        )
        session.add(run)
        session.flush()
        session.add_all([
            DepartmentDiscoveryPoint(
                run_id=run.id, latitude=23.1, longitude=113.1, source="GRID",
                round_number=1, status=DepartmentDiscoveryPointStatus.COMPLETED,
                new_department_count=3),
            DepartmentDiscoveryPoint(
                run_id=run.id, latitude=23.2, longitude=113.2, source="GRID",
                round_number=1, status=DepartmentDiscoveryPointStatus.PENDING,
                new_department_count=0),
        ])
        session.commit()
        run_id = str(run.id)

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = await client.get("/api/departments/discovery-runs", params={
            "city_id": city_id, "page": 1, "page_size": 10})
        detail = await client.get("/api/departments/discovery-runs", params={
            "run_id": run_id})

    assert listing.status_code == 200
    assert listing.json()["pagination"]["total"] == 1
    item = detail.json()["item"]
    assert item["id"] == run_id
    assert item["city_name"] == "广州"
    assert item["preset"] == "deep"
    assert item["preset_label"] == "深度"
    assert item["status"] == "STOPPED"
    assert item["status_label"] == "已停止"
    assert item["request_count"] == 2
    assert item["max_requests"] == 500
    assert item["last_error_summary"] == "匿名接口安全错误"
    assert item["rounds"] == [{
        "round_number": 1, "planned_point_count": 2,
        "completed_point_count": 1, "new_department_count": 3,
    }]


@pytest.mark.asyncio
async def test_stop_and_resume_require_existing_run_and_legal_state(engine):
    app, factory = _app_for(engine)
    city_id = _city(factory)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id})
        run_id = created.json()["id"]
        stopped = await client.post("/api/departments/discovery-runs/stop", json={
            "run_id": run_id})
        stopped_again = await client.post("/api/departments/discovery-runs/stop", json={
            "run_id": run_id})
        resumed = await client.post("/api/departments/discovery-runs/resume", json={
            "run_id": run_id})
        resumed_again = await client.post("/api/departments/discovery-runs/resume", json={
            "run_id": run_id})
        missing = await client.post("/api/departments/discovery-runs/stop", json={
            "run_id": str(uuid4())})

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "STOPPED"
    assert stopped_again.status_code == 409
    assert stopped_again.json()["detail"] == "只有运行中的发现任务可以停止"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "RUNNING"
    assert resumed_again.status_code == 409
    assert resumed_again.json()["detail"] == "只有已停止或已中断的发现任务可以继续"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "发现任务不存在"


@pytest.mark.asyncio
async def test_department_api_hides_internal_service_errors(engine):
    class BrokenService:
        def create_run(self, *_args, **_kwargs):
            raise RuntimeError("postgresql://secret-host/internal")

    app, factory = _app_for(engine, BrokenService())
    city_id = _city(factory)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test") as client:
        response = await client.post("/api/departments/discovery-runs", json={
            "city_id": city_id})

    assert response.status_code == 500
    assert response.json()["detail"] == "发现任务创建失败，请稍后重试"
    assert "secret-host" not in response.text


def test_department_browser_controls_guard_polling_and_escape_external_fields():
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert "function initDepartments" in script
    assert "visibilitychange" in script
    assert "pagehide" in script
    assert "refreshPromise" in script
    assert "3000" in script
    assert "new AbortController()" in script
    assert "controller.abort()" in script
    assert "8000" in script
    assert "signal:controller.signal" in script
    assert "clearTimeout(refreshTimeout)" in script
    assert "网点数据刷新超时，请重试" in script
    assert "throw reportRefreshError(error,'网点数据刷新失败，请重试')" in script
    assert "batch.abortReason='timeout'" in script
    assert "batch.abortReason='cascade';controller.abort()" in script
    assert "refreshBatch.abortReason='operation'" in script
    assert "refreshBatch.controller.abort()" in script
    assert "const requests=[" in script
    assert "Promise.all(requests)" in script
    assert "await Promise.allSettled(requests)" in script
    assert "departmentRefreshReported" in script
    assert "message?new Error(message)" in script
    cascade = script.index("batch.abortReason='cascade';controller.abort()")
    settled = script.index("await Promise.allSettled(requests)", cascade)
    batch_cleanup = script.index("refreshBatch=null", settled)
    promise_cleanup = script.index("refreshPromise=null", settled)
    assert cascade < settled < batch_cleanup < promise_cleanup
    assert "深度预设最多会发起 500 次请求" in script
    assert "body.confirmation='确认启动深度发现'" in script
    assert "creationItems=data.items.filter(item=>item.enabled&&item.catalog_active)" in script
    assert "filterOptions=data.items.map" in script
    for expression in (
        "esc(item.name)", "esc(item.address", "esc(item.district",
        "esc(item.latitude", "esc(item.longitude", "esc(item.discovery_source_label",
        "esc(item.active_state_label", "esc(item.last_error_summary",
    ):
        assert expression in script
