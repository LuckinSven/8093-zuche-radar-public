import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.model_search.service import ModelSearchService
from app.models import (
    City,
    Department,
    ModelSearchOffer,
    ModelSearchRun,
    ModelSearchSample,
    ModelSearchSampleStatus,
    VehicleModel,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=SHANGHAI)


class TrackingSession(Session):
    open_count = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tracked = True
        type(self).open_count += 1

    def close(self):
        if self._tracked:
            type(self).open_count -= 1
            self._tracked = False
        super().close()


class RecordingGateway:
    def __init__(self, state, payload):
        self.state = state
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def choose_car(self, query):
        self.state["active"] += 1
        self.state["maximum"] = max(self.state["maximum"], self.state["active"])
        self.state["session_counts"].append(TrackingSession.open_count)
        await asyncio.sleep(0.02)
        self.state["queries"].append(query)
        self.state["active"] -= 1
        return self.payload


class ErrorGateway:
    def __init__(self, error):
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def choose_car(self, _query):
        raise self.error


def _payload():
    return {
        "code": 1,
        "content": {
            "modelGroups": [{
                "groupId": 31,
                "name": "新能源车",
                "modelItems": [
                    {"modelId": 4952, "modelName": "比亚迪海狮05"},
                    {"modelId": 9999, "modelName": "扫描中新车型"},
                ],
            }],
            "deptHangModels": [{
                "deptId": 79340,
                "deptName": "鱼珠地铁站服务点",
                "deptAddress": "广州市黄埔区鱼珠地铁站D出口",
                "lat": "23.101610",
                "lon": "113.432649",
                "deptDistance": "0m",
                "models": [
                    {
                        "modelId": 4952,
                        "modelName": "比亚迪海狮05",
                        "modelDesc": "纯电58kWh | SUV5座",
                        "packagePrice": "145",
                        "dailyPrice": "155",
                        "bookFlag": True,
                        "inventoryType": 1,
                    },
                    {
                        "modelId": 9999,
                        "modelName": "扫描中新车型",
                        "modelDesc": "1.5T自动 | 三厢5座",
                        "packagePrice": "99",
                        "bookFlag": True,
                    },
                ],
            }],
        },
    }


def _setup(engine, department_count=2):
    TrackingSession.open_count = 0
    factory = sessionmaker(
        bind=engine, class_=TrackingSession, expire_on_commit=False)
    with factory() as session:
        city = City(zuche_city_id="14", name="广州")
        target = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
        session.add_all([city, target])
        session.flush()
        session.add_all([
            Department(
                city_id=city.id,
                zuche_dept_id=79340 + index,
                name=f"广州网点{index}",
                latitude=23.101610 + index / 10_000,
                longitude=113.432649 + index / 10_000,
            )
            for index in range(department_count)
        ])
        session.commit()
        return factory, city.id


@pytest.mark.asyncio
async def test_service_closes_database_sessions_and_only_saves_target_offers(engine):
    """网络等待持有连接或保存非目标明细都会让长任务拖垮数据库。"""
    factory, city_id = _setup(engine)
    state = {"active": 0, "maximum": 0, "session_counts": [], "queries": []}
    service = ModelSearchService(
        factory, lambda: RecordingGateway(state, _payload()))
    service.create_run(city_id, ["比亚迪海狮05"], now=NOW)

    result = await service.process_active_run(now=NOW)

    assert result.processed == 2
    assert result.succeeded == 2
    assert state["maximum"] == 2
    assert state["session_counts"] == [0, 0]
    assert TrackingSession.open_count == 0
    with factory() as session:
        saved_ids = set(session.scalars(select(VehicleModel.zuche_model_id).join(
            ModelSearchOffer,
            ModelSearchOffer.vehicle_model_id == VehicleModel.id,
        )))
        assert saved_ids == {4952}
        assert session.scalar(select(func.count()).select_from(VehicleModel).where(
            VehicleModel.zuche_model_id == 9999)) == 1


@pytest.mark.asyncio
async def test_service_respects_single_concurrency_for_large_task(engine):
    """大任务若仍并发请求，会违背超过2000次自动降速的约束。"""
    factory, city_id = _setup(engine)
    state = {"active": 0, "maximum": 0, "session_counts": [], "queries": []}
    service = ModelSearchService(
        factory, lambda: RecordingGateway(state, _payload()))
    run = service.create_run(city_id, ["比亚迪海狮05"], now=NOW)
    with factory() as session:
        stored = session.get(ModelSearchRun, run.id)
        stored.execution_concurrency = 1
        stored.estimated_request_count = 2001
        session.commit()

    result = await service.process_active_run(now=NOW)

    assert result.processed == 1
    assert state["maximum"] == 1


@pytest.mark.asyncio
async def test_timeout_returns_sample_to_pending_with_backoff(engine):
    """暂时网络超时不应立刻形成永久失败或忙循环重试。"""
    factory, city_id = _setup(engine, department_count=1)
    service = ModelSearchService(
        factory, lambda: ErrorGateway(httpx.ReadTimeout("timeout")))
    run = service.create_run(city_id, ["比亚迪海狮05"], now=NOW)

    result = await service.process_active_run(now=NOW)

    assert result.failed == 2
    with factory() as session:
        samples = session.scalars(select(ModelSearchSample).where(
            ModelSearchSample.run_id == run.id,
            ModelSearchSample.attempt_count == 1,
        )).all()
        assert len(samples) == 2
        assert {sample.status for sample in samples} == {
            ModelSearchSampleStatus.PENDING}
        assert all(sample.next_attempt_at > NOW.astimezone(UTC) for sample in samples)
        assert {sample.error_summary for sample in samples} == {
            "神州匿名接口网络请求失败"}
