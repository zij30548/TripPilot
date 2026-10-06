from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.api.places import get_amap_client
from app.integrations.amap import AmapClient
from app.schemas.candidates import CandidateRequest, CandidateResponse
from app.services.candidates import prepare_candidates


class SafeCandidateValidation(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(
                    status_code=422,
                    detail="候选地点需求无效，请返回确认住宿参考点、必去地点和兴趣。",
                ) from None

        return safe_handler


router = APIRouter(prefix="/places", tags=["places"], route_class=SafeCandidateValidation)


@router.post("/candidates", response_model=CandidateResponse)
async def get_candidates(
    request: CandidateRequest,
    amap: Annotated[AmapClient, Depends(get_amap_client)],
) -> CandidateResponse:
    return await prepare_candidates(request, amap)
