from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import StringConstraints

from app.config import Settings, get_settings
from app.integrations.amap import AmapClient, AmapError, AmapTimeoutError, REQUEST_TIMEOUT
from app.schemas.place import Place
from app.schemas.destination import LocatedPlace
from app.destinations import DestinationId, destination_for
from app.usage_runtime import MeteredAsyncClient


router = APIRouter(prefix="/places", tags=["places"])
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with MeteredAsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
        yield client


def get_amap_client(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> AmapClient:
    key = settings.amap_web_key.get_secret_value().strip()
    if not key:
        raise HTTPException(status_code=503, detail="地点搜索服务未配置 AMAP_WEB_KEY。")
    return AmapClient(client, key)


@router.get("/search", response_model=list[LocatedPlace] | list[Place])
async def search_places(
    keyword: Annotated[Keyword, Query()],
    amap: Annotated[AmapClient, Depends(get_amap_client)],
    city: Annotated[City | None, Query()] = None,
    destination_id: Annotated[DestinationId | None, Query()] = None,
) -> list[Place]:
    if (city is None) == (destination_id is None):
        raise HTTPException(status_code=422, detail="请明确选择目的地。")
    try:
        if destination_id is not None:
            return await amap.search(keyword, destination_for(destination_id).name, destination_id=destination_id)
        # Original explicit city interface retained for /demo compatibility only.
        return await amap.search(keyword, city)
    except AmapTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from None
    except AmapError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
