from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import StringConstraints

from app.config import Settings, get_settings
from app.integrations.amap import AmapClient, AmapError, AmapTimeoutError, REQUEST_TIMEOUT
from app.schemas.place import Place


router = APIRouter(prefix="/places", tags=["places"])
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        yield client


def get_amap_client(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> AmapClient:
    key = settings.amap_web_key.get_secret_value().strip()
    if not key:
        raise HTTPException(status_code=503, detail="地点搜索服务未配置 AMAP_WEB_KEY。")
    return AmapClient(client, key)


@router.get("/search", response_model=list[Place])
async def search_places(
    keyword: Annotated[Keyword, Query()],
    city: Annotated[City, Query()],
    amap: Annotated[AmapClient, Depends(get_amap_client)],
) -> list[Place]:
    try:
        return await amap.search(keyword, city)
    except AmapTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from None
    except AmapError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
