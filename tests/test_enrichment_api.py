from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.enrichment.service import VehicleEnrichmentService
from app.main import create_app
from app.models import (
    VehicleAnnotation,
    VehicleEnrichmentResult,
    VehicleEnrichmentRun,
    VehicleModel,
)


def _configuration(enabled=True):
    return {
        "enabled": enabled,
        "api_key": "secret" if enabled else None,
        "base_url": "https://api.example.com/v1",
        "model": "model-x",
        "batch_size": 1,
        "timeout_seconds": 30,
        "max_retries": 1,
    }


def _app(engine, *, enabled=True):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory)
    app.state.vehicle_enrichment_service = VehicleEnrichmentService(
        factory,
        lambda **_: pytest.fail("创建和读取任务不应调用外部 AI"),
        lambda: _configuration(enabled),
    )
    return app, factory


@pytest.mark.asyncio
async def test_create_and_read_enrichment_run_with_stable_statistics(engine):
    app, factory = _app(engine)
    with factory() as session:
        session.add(VehicleModel(zuche_model_id=4952, name="比亚迪海狮05"))
        session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        created = await client.post(
            "/api/vehicle-enrichment-runs", json={"scope": "PENDING_ONLY"})
        detail = await client.get(
            f"/api/vehicle-enrichment-runs/{created.json()['id']}")
        history = await client.get("/api/vehicle-enrichment-runs?page=1&page_size=20")

    assert created.status_code == 201
    assert created.json()["status_label"] == "等待中"
    assert created.json()["scope_label"] == "补全待处理车型"
    assert created.json()["current_stage_label"] == "逐车型初判"
    assert created.json()["total_count"] == 1
    assert detail.json()["id"] == created.json()["id"]
    assert detail.json()["results"][0]["model_name"] == "比亚迪海狮05"
    assert history.json()["pagination"]["total"] == 1


@pytest.mark.asyncio
async def test_create_run_rejects_unconfigured_ai_and_extra_fields(engine):
    app, _ = _app(engine, enabled=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        unconfigured = await client.post(
            "/api/vehicle-enrichment-runs", json={"scope": "PENDING_ONLY"})
        unsafe = await client.post(
            "/api/vehicle-enrichment-runs", json={"scope": "ALL", "secret": "x"})

    assert unconfigured.status_code == 422
    assert "未启用" in unconfigured.json()["detail"]
    assert unsafe.status_code == 422


@pytest.mark.asyncio
async def test_missing_and_illegal_run_controls_use_404_and_409(engine):
    app, factory = _app(engine)
    with factory() as session:
        session.add(VehicleModel(zuche_model_id=1, name="测试车型"))
        session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        created = await client.post(
            "/api/vehicle-enrichment-runs", json={"scope": "PENDING_ONLY"})
        illegal = await client.post(
            f"/api/vehicle-enrichment-runs/{created.json()['id']}/stop")
        missing = await client.get(f"/api/vehicle-enrichment-runs/{uuid4()}")

    assert illegal.status_code == 409
    assert missing.status_code == 404
    assert "不存在" in missing.json()["detail"]


@pytest.mark.asyncio
async def test_delete_stopped_history_removes_run_but_keeps_model_library(engine):
    app, factory = _app(engine)
    with factory() as session:
        model = VehicleModel(zuche_model_id=7, name="保留车型")
        session.add(model)
        session.flush()
        run = VehicleEnrichmentRun(
            scope="PENDING_ONLY", status="STOPPED", total_count=1)
        session.add(run)
        session.flush()
        session.add(VehicleEnrichmentResult(run_id=run.id, vehicle_model_id=model.id))
        session.commit()
        run_id, model_id = run.id, model.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        deleted = await client.delete(f"/api/vehicle-enrichment-runs/{run_id}")
        missing = await client.get(f"/api/vehicle-enrichment-runs/{run_id}")

    assert deleted.status_code == 204
    assert missing.status_code == 404
    with factory() as session:
        assert session.get(VehicleModel, model_id).name == "保留车型"


@pytest.mark.asyncio
async def test_delete_active_enrichment_run_returns_conflict(engine):
    app, factory = _app(engine)
    with factory() as session:
        session.add(VehicleModel(zuche_model_id=8, name="等待车型"))
        session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        created = await client.post(
            "/api/vehicle-enrichment-runs", json={"scope": "PENDING_ONLY"})
        deleted = await client.delete(
            f"/api/vehicle-enrichment-runs/{created.json()['id']}")

    assert deleted.status_code == 409


@pytest.mark.asyncio
async def test_focused_stage_is_presented_as_ai_second_identification(engine):
    """“单车复核”容易被理解为需要用户到另一个页面人工操作。"""
    app, factory = _app(engine)
    with factory() as session:
        session.add(VehicleModel(zuche_model_id=9, name="待二次识别车型"))
        session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        created = await client.post(
            "/api/vehicle-enrichment-runs", json={"scope": "PENDING_ONLY"})
        with factory() as session:
            result = session.query(VehicleEnrichmentResult).filter_by(
                run_id=created.json()["id"]).one()
            result.stage = "FOCUSED"
            session.commit()
        detail = await client.get(
            f"/api/vehicle-enrichment-runs/{created.json()['id']}")

    assert detail.json()["results"][0]["stage_label"] == "AI 二次识别"


@pytest.mark.asyncio
async def test_manual_resolution_updates_model_result_and_run_statistics(engine):
    """如果人工处理后失败数没减少、车型库没更新，该测试应失败。"""
    app, factory = _app(engine)
    with factory() as session:
        model = VehicleModel(zuche_model_id=4952, name="比亚迪海狮05")
        session.add(model)
        session.flush()
        run = VehicleEnrichmentRun(
            scope="PENDING_ONLY",
            status="PARTIAL",
            total_count=1,
            processed_count=1,
            failed_count=1,
            last_error_summary="AI 接口返回格式异常",
        )
        session.add(run)
        session.flush()
        session.add(VehicleEnrichmentResult(
            run_id=run.id,
            vehicle_model_id=model.id,
            status="FAILED",
            stage="FOCUSED",
            error_summary="AI 接口返回格式异常",
        ))
        session.commit()
        run_id = run.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.put(
            f"/api/vehicle-enrichment-runs/{run_id}/results/4952/manual-resolution",
            json={
                "energy_type": "新能源",
                "energy_subtype": "插电混动",
                "note": "品牌官网已核对",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["updated_count"] == 1
    assert response.json()["failed_count"] == 0
    with factory() as session:
        model = session.query(VehicleModel).filter_by(zuche_model_id=4952).one()
        result = session.query(VehicleEnrichmentResult).filter_by(run_id=run_id).one()
        annotation = session.query(VehicleAnnotation).filter_by(
            vehicle_model_id=model.id).one()
        assert (model.energy_type, model.energy_subtype, model.energy_source) == (
            "新能源", "插电混动", "MANUAL")
        assert (result.status, result.applied, result.error_summary) == (
            "APPLIED", True, None)
        assert annotation.source == "品牌官网已核对"


@pytest.mark.asyncio
async def test_manual_resolution_can_explicitly_keep_model_unknown(engine):
    """如果人工选择保持未知却被计为失败，该测试应失败。"""
    app, factory = _app(engine)
    with factory() as session:
        model = VehicleModel(zuche_model_id=88, name="未知车型")
        session.add(model)
        session.flush()
        run = VehicleEnrichmentRun(
            scope="PENDING_ONLY", status="PARTIAL", total_count=1,
            processed_count=1, failed_count=1)
        session.add(run)
        session.flush()
        session.add(VehicleEnrichmentResult(
            run_id=run.id, vehicle_model_id=model.id, status="FAILED"))
        session.commit()
        run_id = run.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.put(
            f"/api/vehicle-enrichment-runs/{run_id}/results/88/manual-resolution",
            json={"energy_type": "未知", "energy_subtype": "未知", "note": "暂无可靠依据"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["unknown_count"] == 1
    assert response.json()["failed_count"] == 0
    with factory() as session:
        model = session.query(VehicleModel).filter_by(zuche_model_id=88).one()
        result = session.query(VehicleEnrichmentResult).filter_by(run_id=run_id).one()
        assert (model.energy_type, model.energy_subtype, model.energy_source) == (
            "未知", "未知", "MANUAL")
        assert (result.status, result.applied) == ("KEPT_UNKNOWN", False)


@pytest.mark.asyncio
async def test_manual_resolution_rejects_active_or_already_applied_results(engine):
    """如果运行中结果能被人工抢先改写，该测试应失败。"""
    app, factory = _app(engine)
    with factory() as session:
        model = VehicleModel(zuche_model_id=99, name="测试车型")
        session.add(model)
        session.flush()
        run = VehicleEnrichmentRun(scope="PENDING_ONLY", status="RUNNING", total_count=1)
        session.add(run)
        session.flush()
        session.add(VehicleEnrichmentResult(
            run_id=run.id, vehicle_model_id=model.id, status="RUNNING"))
        session.commit()
        run_id = run.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.put(
            f"/api/vehicle-enrichment-runs/{run_id}/results/99/manual-resolution",
            json={"energy_type": "燃油", "energy_subtype": "汽油", "note": "人工确认"},
        )

    assert response.status_code == 409
