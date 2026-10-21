"""Official Q&A entry is deferred; research implementation remains unregistered."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/answer", status_code=503)
async def answer_disabled() -> None:
    # No request/config/client dependencies: payloads cannot opt into generation.
    raise HTTPException(status_code=503, detail={
        "code": "knowledge_answer_disabled",
        "message": "AI问答暂未开放，请使用资料检索并查阅官方原文。",
    })
