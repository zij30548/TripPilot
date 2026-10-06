from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.places import router as places_router
from app.api.amap_proxy import router as amap_proxy_router
from app.api.trips import router as trips_router
from app.api.routes import router as routes_router
from app.api.candidates import router as candidates_router

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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
