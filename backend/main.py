from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.saved_trips import ArchiveError
from app.api.saved_trips import router as saved_trips_router

from app.api.places import router as places_router
from app.api.amap_proxy import router as amap_proxy_router
from app.api.trips import router as trips_router
from app.api.routes import router as routes_router
from app.api.candidates import router as candidates_router
from app.api.schedule import router as schedule_router
from app.api.weather import router as weather_router
from app.api.explanation import router as explanation_router
from app.api.adjustment_intent import router as adjustment_intent_router
from app.api.knowledge import router as knowledge_router
from app.api.knowledge_answer_disabled import router as knowledge_answer_router
from app.api.itinerary_query import router as itinerary_query_router
from app.api.photo import router as photo_router

app = FastAPI(
    title="TripPilot API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(trips_router)
app.include_router(places_router)
app.include_router(amap_proxy_router)
app.include_router(routes_router)
app.include_router(candidates_router)
app.include_router(schedule_router)
app.include_router(weather_router)
app.include_router(explanation_router)
app.include_router(adjustment_intent_router)
app.include_router(saved_trips_router)
app.include_router(knowledge_router)
app.include_router(knowledge_answer_router)
app.include_router(itinerary_query_router)
app.include_router(photo_router)


@app.exception_handler(ArchiveError)
async def archive_error(_request: Request, error: ArchiveError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content={"detail": {"code": error.code, "message": "行程存档操作未完成，请稍后主动重试。"}})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
