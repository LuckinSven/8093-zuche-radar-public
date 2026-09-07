import asyncio
from enum import StrEnum
from uuid import UUID

import httpx
from pydantic import BaseModel

from app.domain import ScanQuery
from app.repositories.scans import ScanRepository
from app.zuche.client import ZucheGatewayError
from app.zuche.parser import parse_choose_car


class ScanTrigger(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"


class ScanEvent(BaseModel):
    event_type: str
    vehicle_model_id: int
    department_id: int | None


class ScanResult(BaseModel):
    scan_id: UUID
    status: str
    department_count: int = 0
    offer_count: int = 0
    events: list[ScanEvent] = []
    error_message: str | None = None

    @property
    def scan_run_id(self) -> UUID: return self.scan_id
    @property
    def availability_count(self) -> int: return self.offer_count


class ScanService:
    def __init__(self, gateway, repository: ScanRepository) -> None:
        self.gateway, self.repository = gateway, repository

    async def run(self, query: ScanQuery, trigger: ScanTrigger) -> ScanResult:
        if not isinstance(query, ScanQuery):
            query = ScanQuery.model_validate(query.model_dump())
        run = self.repository.create_run(query, str(trigger)); run_id = run.id
        self.repository.session.commit()
        try:
            raw = await self._fetch(query)
            parsed = parse_choose_car(raw, query)
            events = self.repository.persist_success(run, raw, parsed)
            self.repository.session.commit()
            return ScanResult(scan_id=run_id, status="SUCCESS", department_count=len(parsed.departments),
                offer_count=len(parsed.offers), events=[ScanEvent(event_type=x.event_type,
                vehicle_model_id=x.vehicle_model_id, department_id=x.department_id) for x in events])
        except Exception as error:
            self.repository.session.rollback()
            failed = self.repository.session.get(type(run), run_id)
            if failed is None: raise
            error_code, error_message = safe_scan_error(error)
            self.repository.mark_failed(failed, error_code, error_message)
            self.repository.session.commit()
            return ScanResult(scan_id=run_id, status="FAILED", error_message=error_message)

    async def _fetch(self, query: ScanQuery) -> dict:
        for attempt, delay in enumerate((0, 1, 2)):
            if delay: await asyncio.sleep(delay)
            try: return await self.gateway.choose_car(query)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 2: raise
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 or attempt == 2:
                    raise
        raise RuntimeError("扫描重试逻辑异常")


def safe_scan_error(error: Exception) -> tuple[str, str]:
    """将上游异常转换为可持久化、不会泄漏请求细节的稳定中文错误。"""
    if isinstance(error, httpx.TimeoutException):
        return "UPSTREAM_TIMEOUT", "神州接口请求超时，请稍后重试"
    if isinstance(error, httpx.TransportError):
        return "UPSTREAM_CONNECTION_ERROR", "暂时无法连接神州接口，请稍后重试"
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code >= 500:
            return "UPSTREAM_UNAVAILABLE", "神州接口暂时不可用，请稍后重试"
        return "UPSTREAM_REJECTED", "神州接口拒绝了本次请求，请稍后重试"
    if isinstance(error, ZucheGatewayError):
        return "UPSTREAM_RESPONSE_ERROR", "神州接口返回异常，请稍后重试"
    if isinstance(error, (ValueError, TypeError)):
        return "UPSTREAM_FORMAT_ERROR", "神州接口返回的数据格式不正确"
    return "SCAN_FAILED", "扫描失败，请稍后重试"
