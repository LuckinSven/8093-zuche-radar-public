from fastapi import APIRouter, HTTPException, Request

from app.zuche.upstream_catalog import UpstreamProbeError, UpstreamProbeService, upstream_catalog


router = APIRouter()


@router.get("/zuche/upstream")
def get_upstream_catalog():
    return upstream_catalog()


@router.post("/zuche/upstream/probe/{endpoint_id}")
async def probe_upstream_endpoint(endpoint_id: str, request: Request):
    try:
        return await UpstreamProbeService(request.app.state.zuche_client_factory).probe(endpoint_id)
    except KeyError as error:
        raise HTTPException(404, "该接口未列入安全探测范围") from error
    except UpstreamProbeError as error:
        raise HTTPException(502, str(error)) from error
