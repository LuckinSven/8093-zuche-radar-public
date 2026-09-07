from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.enrichment.client import EnrichmentClientError
from app.api.dependencies import request_session
from app.integrations.baidu_maps import BaiduMapsError
from app.integrations.repository import IntegrationSettingsRepository
from app.integrations.service import IntegrationSettingsService

router = APIRouter()


class BaiduSettingsInput(BaseModel):
    enabled: bool = False
    ak: str | None = Field(default=None, max_length=128)
    default_region: str = Field(default="广州", min_length=1, max_length=64)
    test_keyword: str = Field(default="三溪地铁站", min_length=1, max_length=64)


class AIEnrichmentSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    api_key: str | None = Field(default=None, max_length=512)
    base_url: AnyHttpUrl = "https://api.deepseek.com"
    model: str = Field(default="deepseek-chat", min_length=1, max_length=128)
    batch_size: int = Field(default=1, ge=1, le=1)
    timeout_seconds: int = Field(default=30, ge=10, le=300)
    max_retries: int = Field(default=1, ge=0, le=5)


def _service(session) -> IntegrationSettingsService:
    return IntegrationSettingsService(IntegrationSettingsRepository(session))


@router.get("/settings/integrations")
def list_integrations(request: Request):
    with request_session(request) as session:
        return {"items": _service(session).list_integrations()}


@router.put("/settings/integrations/baidu_maps")
def save_baidu(payload: BaiduSettingsInput, request: Request):
    with request_session(request) as session:
        try:
            return _service(session).save_baidu(**payload.model_dump())
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


@router.delete("/settings/integrations/baidu_maps/secret", status_code=204)
def clear_baidu_secret(request: Request):
    with request_session(request) as session:
        _service(session).clear_baidu_secret()
        return Response(status_code=204)


@router.post("/settings/integrations/baidu_maps/test")
async def test_baidu(request: Request):
    with request_session(request) as session:
        try:
            return await _service(session).test_baidu(request.app.state.baidu_client_factory)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except BaiduMapsError as error:
            raise HTTPException(502, str(error)) from error


@router.put("/settings/integrations/openai-compatible")
def save_ai_enrichment(payload: AIEnrichmentSettingsInput, request: Request):
    values = payload.model_dump()
    values["base_url"] = str(payload.base_url)
    with request_session(request) as session:
        try:
            return _service(session).save_ai_enrichment(**values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error


@router.delete("/settings/integrations/openai-compatible/secret", status_code=204)
def clear_ai_enrichment_secret(request: Request):
    with request_session(request) as session:
        _service(session).clear_ai_enrichment_secret()
        return Response(status_code=204)


@router.post("/settings/integrations/openai-compatible/test")
async def test_ai_enrichment(request: Request):
    with request_session(request) as session:
        try:
            return await _service(session).test_ai_enrichment(
                request.app.state.enrichment_client_factory)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except EnrichmentClientError as error:
            raise HTTPException(502, error.public_message) from error
