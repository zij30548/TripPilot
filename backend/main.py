from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.trips import router as trips_router

app = FastAPI(
    title="TripPilot API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
app.include_router(trips_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
