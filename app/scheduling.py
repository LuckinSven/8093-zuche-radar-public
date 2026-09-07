import asyncio
import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import ScanQuery
from app.citywide.service import CitywideScanService
from app.enrichment.client import OpenAICompatibleClient
from app.enrichment.service import VehicleEnrichmentService
from app.integrations.repository import IntegrationSettingsRepository
from app.departments.service import DepartmentDiscoveryService
from app.maintenance import (
    CitywideDetailMaintenance,
    ModelSearchMaintenance,
    RawPayloadMaintenance,
)
from app.model_search.service import ModelSearchService
from app.models import City, Probe
from app.repositories.departments import DepartmentDiscoveryRepository
from app.repositories.scans import ScanRepository
from app.scanning.service import ScanService, ScanTrigger
from app.zuche.client import ZucheClient

logger = logging.getLogger(__name__)


class ProbeScheduler:
    """将数据库中已启用的扫描点同步到调度器。"""

    def __init__(self, session_source, runner: Callable[[int], object], scheduler=None) -> None:
        self.session_source = session_source
        self.runner = runner
        self.scheduler = scheduler or BackgroundScheduler(timezone="Asia/Shanghai")

    def reconcile(self) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith("probe-"):
                self.scheduler.remove_job(job.id)
        context = nullcontext(self.session_source) if isinstance(self.session_source, Session) else self.session_source()
        with context as session:
            probes = session.scalars(select(Probe).where(Probe.enabled.is_(True))).all()
            for probe in probes:
                if not probe.schedule:
                    continue
                self.scheduler.add_job(self.runner, _trigger(probe.schedule), args=[probe.id],
                                       id=f"probe-{probe.id}", replace_existing=True)

    def job_ids(self) -> list[str]:
        return sorted(job.id for job in self.scheduler.get_jobs())

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)


def _trigger(schedule: str):
    if schedule.startswith("interval:"):
        minutes = int(schedule.removeprefix("interval:"))
        if minutes <= 0:
            raise ValueError("扫描间隔必须为正数")
        return IntervalTrigger(minutes=minutes)
    return CronTrigger.from_crontab(schedule, timezone="Asia/Shanghai")


def validate_schedule(schedule: str) -> None:
    _trigger(schedule)


def build_probe_query(city: City, probe: Probe, now: datetime | None = None) -> ScanQuery:
    local_now = (now or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
    pickup = local_now.replace(hour=9, minute=0, second=0, microsecond=0)
    if pickup <= local_now:
        pickup += timedelta(days=1)
    return ScanQuery(city_id=city.zuche_city_id, location_name=probe.name,
                     latitude=float(probe.latitude), longitude=float(probe.longitude),
                     pickup_time=pickup, return_time=pickup + timedelta(days=1))


class RadarRuntime:
    """生产运行时：恢复扫描点任务并执行每日原始数据清理。"""

    def __init__(self, session_factory, retention_days: int = 60,
                 gateway_factory: Callable[[], ZucheClient] | None = None,
                 enrichment_client_factory: Callable[..., OpenAICompatibleClient] | None = None) -> None:
        self.session_factory = session_factory
        self.retention_days = retention_days
        self.gateway_factory = gateway_factory or ZucheClient
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self.probe_scheduler = ProbeScheduler(session_factory, self._run_probe, self.scheduler)
        self.department_discovery_service = DepartmentDiscoveryService(
            session_factory, self.gateway_factory)
        self.citywide_scan_service = CitywideScanService(
            session_factory, self.gateway_factory)
        self.model_search_service = ModelSearchService(
            session_factory, self.gateway_factory)
        self.vehicle_enrichment_service = VehicleEnrichmentService(
            session_factory,
            enrichment_client_factory or OpenAICompatibleClient,
            self._load_enrichment_configuration,
        )

    def start(self) -> None:
        with self.session_factory() as session:
            interrupted = DepartmentDiscoveryRepository(session).interrupt_stale_runs()
            session.commit()
        if interrupted:
            logger.info("启动时已中断 %s 个遗留网点发现任务，等待用户手动继续", interrupted)
        citywide_interrupted = self.citywide_scan_service.interrupt_stale_runs()
        if citywide_interrupted:
            logger.info(
                "启动时已中断 %s 个遗留全城扫描任务，等待用户手动继续",
                citywide_interrupted,
            )
        enrichment_interrupted = self.vehicle_enrichment_service.interrupt_stale_runs()
        if enrichment_interrupted:
            logger.info(
                "启动时已中断 %s 个遗留 AI 补全任务，等待用户手动继续",
                enrichment_interrupted,
            )
        model_search_interrupted = self.model_search_service.interrupt_stale_runs()
        if model_search_interrupted:
            logger.info(
                "启动时已中断 %s 个遗留按车型找车任务，等待用户手动继续",
                model_search_interrupted,
            )
        self.scheduler.add_job(self._purge_raw_payloads, CronTrigger(hour=3, minute=15, timezone="Asia/Shanghai"),
                               id="maintenance-raw-payloads", replace_existing=True)
        self.scheduler.add_job(
            self._run_department_discovery,
            IntervalTrigger(seconds=2, jitter=1),
            id="department-discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._run_citywide_scan,
            IntervalTrigger(seconds=2, jitter=1),
            id="citywide-scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._run_vehicle_enrichment,
            IntervalTrigger(seconds=2, jitter=1),
            id="vehicle-enrichment",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._run_model_search,
            IntervalTrigger(seconds=2, jitter=1),
            id="model-search",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.probe_scheduler.reconcile()
        self.probe_scheduler.start()

    def shutdown(self) -> None:
        self.probe_scheduler.shutdown()

    def _run_probe(self, probe_id: int) -> None:
        with self.session_factory() as session:
            probe = session.get(Probe, probe_id)
            if probe is None or not probe.enabled:
                return
            city = session.get(City, probe.city_id)
            if city is None or not city.enabled:
                return
            query = build_probe_query(city, probe)
        asyncio.run(self._scan(query))

    async def _scan(self, query: ScanQuery) -> None:
        with self.session_factory() as session:
            async with self.gateway_factory() as gateway:
                await ScanService(gateway, ScanRepository(session)).run(query, ScanTrigger.SCHEDULED)

    def _run_department_discovery(self) -> None:
        asyncio.run(self.department_discovery_service.process_active_run())

    def _run_citywide_scan(self) -> None:
        asyncio.run(self.citywide_scan_service.process_active_run())

    def _run_vehicle_enrichment(self) -> None:
        try:
            asyncio.run(self.vehicle_enrichment_service.process_active_run())
        except ValueError as error:
            logger.warning("AI 补全任务等待有效配置：%s", error)

    def _run_model_search(self) -> None:
        asyncio.run(self.model_search_service.process_active_run())

    def _load_enrichment_configuration(self) -> dict:
        with self.session_factory() as session:
            item = IntegrationSettingsRepository(session).get(
                "openai_compatible_enrichment")
            config = dict(item.config_json or {}) if item else {}
            return {
                "enabled": bool(item and item.enabled),
                "api_key": item.secret_value if item else None,
                "base_url": str(config.get("base_url") or "https://api.deepseek.com"),
                "model": str(config.get("model") or "deepseek-chat"),
                "batch_size": 1,
                "timeout_seconds": int(config.get("timeout_seconds") or 30),
                "max_retries": int(config.get("max_retries") or 1),
            }

    def _purge_raw_payloads(self) -> None:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            deleted = RawPayloadMaintenance(session, self.retention_days).purge(now)
            session.commit()
            citywide = CitywideDetailMaintenance(session).purge(
                now - timedelta(days=self.retention_days))
            model_search = ModelSearchMaintenance(session).purge(
                now - timedelta(days=self.retention_days))
        logger.info(
            "原始响应维护完成，旧扫描原始响应 %s 条、全城报价 %s 条、"
            "全城原始响应 %s 条、找车任务 %s 个",
            deleted,
            citywide["offers"],
            citywide["raw_payloads"],
            model_search["runs"],
        )
