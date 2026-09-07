import asyncio
import copy
from datetime import UTC, datetime
import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.citywide.service import CitywideScanService
from app.models import (
    City,
    CitywideOffer,
    CitywidePointStatus,
    CitywideRawPayload,
    CitywideScanPoint,
    CitywideScanRun,
    CitywideScanStatus,
    Department,
    VehicleModel,
)


PICKUP = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
RETURN = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
OLD_SEEN = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


class SessionTracker:
    def __init__(self):
        self.open_count = 0


class TrackingSession(Session):
    def __init__(self, *args, tracker: SessionTracker, **kwargs):
        super().__init__(*args, **kwargs)
        self._tracker = tracker
        self._tracked_open = True
        tracker.open_count += 1

    def close(self):
        if self._tracked_open:
            self._tracker.open_count -= 1
            self._tracked_open = False
        super().close()


class ConcurrentGateway:
    def __init__(self, state, tracker, departments, payload_factory):
        self.state = state
        self.tracker = tracker
        self.departments = departments
        self.payload_factory = payload_factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def choose_car(self, query):
        self.state["active"] += 1
        self.state["maximum"] = max(self.state["maximum"], self.state["active"])
        self.state["sessions"].append(self.tracker.open_count)
        if self.state["active"] == len(self.departments):
            self.state["all_started"].set()
        await asyncio.wait_for(self.state["all_started"].wait(), timeout=1)
        await asyncio.sleep(0)
        department = min(
            self.departments,
            key=lambda item: abs(float(item.latitude) - query.latitude),
        )
        self.state["active"] -= 1
        return self.payload_factory(department)


class ErrorGateway:
    def __init__(self, error):
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def choose_car(self, _):
        raise self.error


def _session_factory(engine, tracker):
    return sessionmaker(
        bind=engine,
        class_=TrackingSession,
        expire_on_commit=False,
        tracker=tracker,
    )


def _seed_city(engine, count=3):
    with Session(engine) as session:
        city = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644)
        session.add(city)
        session.flush()
        departments = [
            Department(
                city_id=city.id,
                zuche_dept_id=79_340 + index,
                name=f"广州网点{index}",
                latitude=23.101610 + index / 10_000,
                longitude=113.432649 + index / 10_000,
            )
            for index in range(count)
        ]
        session.add_all(departments)
        session.commit()
        city_id = city.id
        for department in departments:
            session.refresh(department)
        snapshots = [
            (item.id, item.zuche_dept_id, item.name, float(item.latitude), float(item.longitude))
            for item in departments
        ]
    return city_id, snapshots


def _payload(department, *, model_id=4952, model_name="比亚迪海狮05"):
    department_id = department.zuche_dept_id if hasattr(department, "zuche_dept_id") else department[1]
    department_name = department.name if hasattr(department, "name") else department[2]
    latitude = float(department.latitude) if hasattr(department, "latitude") else department[3]
    longitude = float(department.longitude) if hasattr(department, "longitude") else department[4]
    return {
        "code": 1,
        "uid": "REDACTED",
        "content": {
            "modelGroups": [{
                "groupId": 31,
                "name": "新能源车",
                "lowPriceDesc": "¥115起",
                "sortNum": 8,
                "modelItems": [{
                    "modelId": model_id,
                    "modelName": model_name,
                    "lowPriceDesc": "¥115起",
                    "modelImgUrl": "https://dfs.zuchecdn.com/redacted/4952.png",
                }],
            }],
            "deptHangModels": [{
                "pickupWebsite": 6,
                "deptId": department_id,
                "deptName": department_name,
                "deptAddress": "广州市测试地址",
                "lat": f"{latitude:.6f}",
                "lon": f"{longitude:.6f}",
                "deptDistance": "0m",
                "deptDistanceDouble": 0,
                "workTime": "00:00-24:00",
                "wholeDayFlag": True,
                "models": [{
                    "modelId": model_id,
                    "modelName": model_name,
                    "modelDesc": "纯电58kWh | SUV5座",
                    "modelImgUrl": "https://dfs.zuchecdn.com/redacted/4952.png",
                    "modelGroupId": 31,
                    "dailyPrice": "115",
                    "packagePrice": "115",
                    "lowPrice": 115,
                    "bookFlag": True,
                    "inventoryType": 1,
                    "selfServiceFlag": True,
                }],
            }],
        },
    }


@pytest.mark.asyncio
async def test_process_active_run_closes_sessions_during_three_concurrent_requests(engine):
    """若外呼期间仍持有数据库会话或串行请求，连接池和扫描时间都会恶化。"""
    city_id, rows = _seed_city(engine, count=3)
    tracker = SessionTracker()
    factory = _session_factory(engine, tracker)
    departments = [type("Anchor", (), {
        "id": row[0], "zuche_dept_id": row[1], "name": row[2],
        "latitude": row[3], "longitude": row[4],
    }) for row in rows]
    state = {
        "active": 0,
        "maximum": 0,
        "sessions": [],
        "all_started": asyncio.Event(),
    }
    gateway_factory = lambda: ConcurrentGateway(
        state, tracker, departments, _payload)
    service = CitywideScanService(factory, gateway_factory)
    service.create_run(city_id, PICKUP, RETURN)

    result = await service.process_active_run()

    assert result.processed == 3
    assert result.succeeded == 3
    assert state["maximum"] == 3
    assert state["sessions"] == [0, 0, 0]
    assert tracker.open_count == 0
    with Session(engine) as session:
        run = session.scalar(select(CitywideScanRun).where(
            CitywideScanRun.city_id == city_id))
        assert run.status == CitywideScanStatus.COMPLETED
        assert session.scalar(select(func.count()).select_from(CitywideRawPayload)) == 3
        assert session.scalar(select(func.count()).select_from(CitywideOffer)) == 3


@pytest.mark.asyncio
async def test_permanent_model_update_preserves_manual_energy_and_existing_nonempty_fields(engine):
    """若扫描覆盖人工能源或用空值覆盖旧字段，永久车型库会丢失可信信息。"""
    city_id, rows = _seed_city(engine, count=1)
    with Session(engine) as session:
        session.add(VehicleModel(
            zuche_model_id=4952,
            name="旧车型名",
            first_seen_at=OLD_SEEN,
            last_seen_at=OLD_SEEN,
            latest_description="人工确认描述",
            image_url="https://image.example/manual.png",
            energy_type="燃油",
            energy_source="MANUAL",
        ))
        session.commit()
    tracker = SessionTracker()
    factory = _session_factory(engine, tracker)
    anchor = type("Anchor", (), {
        "zuche_dept_id": rows[0][1], "name": rows[0][2],
        "latitude": rows[0][3], "longitude": rows[0][4],
    })
    response = _payload(anchor, model_name="比亚迪海狮05")
    response["content"]["modelGroups"][0]["modelItems"][0]["modelImgUrl"] = None
    response["content"]["deptHangModels"][0]["models"][0]["modelImgUrl"] = None
    response["content"]["deptHangModels"][0]["models"][0]["modelDesc"] = None
    service = CitywideScanService(factory, lambda: _StaticGateway(response))
    service.create_run(city_id, PICKUP, RETURN)

    await service.process_active_run()

    with Session(engine) as session:
        model = session.scalar(select(VehicleModel).where(VehicleModel.zuche_model_id == 4952))
        assert model.name == "比亚迪海狮05"
        assert model.first_seen_at == OLD_SEEN
        assert model.last_seen_at > OLD_SEEN
        assert model.latest_description == "人工确认描述"
        assert model.image_url == "https://image.example/manual.png"
        assert model.energy_type == "燃油"
        assert model.energy_source == "MANUAL"


@pytest.mark.parametrize("protected_source", ["AI_BATCH", "AI_FOCUSED"])
@pytest.mark.asyncio
async def test_permanent_model_update_preserves_ai_energy(engine, protected_source):
    """若后续扫描覆盖 AI 高置信结论，两个车型页面会重新退回上游粗分类。"""
    city_id, rows = _seed_city(engine, count=1)
    with Session(engine) as session:
        session.add(VehicleModel(
            zuche_model_id=4952,
            name="比亚迪海狮05",
            energy_type="燃油",
            energy_source=protected_source,
        ))
        session.commit()
    tracker = SessionTracker()
    factory = _session_factory(engine, tracker)
    anchor = type("Anchor", (), {
        "zuche_dept_id": rows[0][1], "name": rows[0][2],
        "latitude": rows[0][3], "longitude": rows[0][4],
    })
    service = CitywideScanService(factory, lambda: _StaticGateway(_payload(anchor)))
    service.create_run(city_id, PICKUP, RETURN)

    await service.process_active_run()

    with Session(engine) as session:
        model = session.scalar(select(VehicleModel).where(VehicleModel.zuche_model_id == 4952))
        assert model.energy_type == "燃油"
        assert model.energy_source == protected_source


@pytest.mark.asyncio
async def test_retryable_network_failure_stops_after_three_scheduler_passes(engine):
    """若网络错误无限重排或过早终止，扫描任务无法给出可信完成状态。"""
    city_id, _ = _seed_city(engine, count=1)
    tracker = SessionTracker()
    factory = _session_factory(engine, tracker)
    error = httpx.ConnectError("private upstream url")
    service = CitywideScanService(factory, lambda: ErrorGateway(error))
    run = service.create_run(city_id, PICKUP, RETURN)

    first = await service.process_active_run()
    second = await service.process_active_run()
    third = await service.process_active_run()

    assert (first.failed, second.failed, third.failed) == (1, 1, 1)
    with Session(engine) as session:
        stored_run = session.get(type(run), run.id)
        point = session.scalar(select(CitywideScanPoint))
        assert stored_run.status == CitywideScanStatus.PARTIAL
        assert stored_run.last_error_summary == "神州匿名接口网络请求失败"
        assert point.status == CitywidePointStatus.FAILED
        assert point.attempt_count == 3


@pytest.mark.asyncio
async def test_malformed_upstream_content_keeps_raw_payload_for_diagnosis(engine):
    """若解析失败时丢弃已收到的响应，后续无法核对神州字段变化。"""
    city_id, _ = _seed_city(engine, count=1)
    tracker = SessionTracker()
    factory = _session_factory(engine, tracker)
    service = CitywideScanService(
        factory,
        lambda: _StaticGateway({"code": 1, "content": None, "uid": "REDACTED"}),
    )
    run = service.create_run(city_id, PICKUP, RETURN)

    result = await service.process_active_run()

    assert result.failed == 1
    with Session(engine) as session:
        stored = session.get(type(run), run.id)
        assert stored.status == CitywideScanStatus.PARTIAL
        assert stored.last_error_summary == "神州匿名接口响应格式错误"
        assert session.scalar(select(func.count()).select_from(CitywideRawPayload)) == 1


class _StaticGateway:
    def __init__(self, response):
        self.response = copy.deepcopy(response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def choose_car(self, _):
        return copy.deepcopy(self.response)
