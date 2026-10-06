"""A small bounded set of directed walking edges, not a route matrix."""

import asyncio
import math
from datetime import datetime, timezone

from app.integrations.amap import AmapError, AmapTimeoutError
from app.integrations.amap_walking import AmapWalkingClient, INVALID_DATA
from app.schemas.place import ConfirmedPlace
from app.schemas.route import RouteEndpoint, WalkingRouteRequest
from app.schemas.schedule import ScheduleEdge, ScheduleRequest


ROUTE_BATCH_TIMEOUT_SECONDS = 20.0
MAX_CONCURRENT_ROUTES = 3
# A live five-edge batch hit AMap CUQPS (10021) despite low concurrency:
# fast responses let all five starts occur within one second. Pace logical
# route starts within this batch; keep the integration's retry policy intact.
MIN_ROUTE_START_INTERVAL_SECONDS = 0.4
EdgeKey = tuple[str, str, str, str]


def normalized_endpoint(place: ConfirmedPlace) -> RouteEndpoint:
    def coordinate(value: float) -> float:
        rounded = float(f"{value:.6f}")
        return 0.0 if rounded == 0 else rounded

    return RouteEndpoint(
        place_id=place.id, longitude=coordinate(place.longitude), latitude=coordinate(place.latitude),
    )


def edge_key(origin: RouteEndpoint, destination: RouteEndpoint) -> EdgeKey:
    return (
        origin.place_id, f"{origin.longitude:.6f},{origin.latitude:.6f}",
        destination.place_id, f"{destination.longitude:.6f},{destination.latitude:.6f}",
    )


def required_edges(request: ScheduleRequest) -> list[tuple[RouteEndpoint, RouteEndpoint]]:
    stay = normalized_endpoint(request.accommodation_place)
    places = [normalized_endpoint(place) for place in request.must_visit_places]
    pairs = [(endpoint, other) for place in places for endpoint, other in [(stay, place), (place, stay)]]
    pairs.extend(zip(places, places[1:]))
    unique: dict[EdgeKey, tuple[RouteEndpoint, RouteEndpoint]] = {}
    for origin, destination in pairs:
        unique.setdefault(edge_key(origin, destination), (origin, destination))
    return list(unique.values())


async def collect_schedule_edges(request: ScheduleRequest, amap: AmapWalkingClient) -> list[ScheduleEdge]:
    pairs = required_edges(request)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ROUTES)
    start_lock = asyncio.Lock()
    next_start = 0.0

    async def wait_to_start() -> None:
        nonlocal next_start
        async with start_lock:
            loop = asyncio.get_running_loop()
            delay = next_start - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            next_start = loop.time() + MIN_ROUTE_START_INTERVAL_SECONDS

    def failed(index: int, status: str, message: str) -> ScheduleEdge:
        origin, destination = pairs[index]
        return ScheduleEdge(
            id=f"e{index}", origin=origin, destination=destination, status=status,
            queried_at=datetime.now(timezone.utc), message=message,
        )

    async def query(index: int) -> ScheduleEdge:
        origin, destination = pairs[index]
        if origin.place_id == destination.place_id or (
            origin.longitude == destination.longitude and origin.latitude == destination.latitude
        ):
            return ScheduleEdge(
                id=f"e{index}", origin=origin, destination=destination, status="same_place",
                duration_seconds=0, duration_minutes=0, distance_meters=0,
                source="same_place", queried_at=datetime.now(timezone.utc),
            )
        async with semaphore:
            try:
                # Lock/sleep and semaphore waiting are both inside the existing
                # whole-batch deadline and cancelled/drained with the tasks.
                await wait_to_start()
                result = await amap.walking(WalkingRouteRequest(origin=origin, destination=destination))
                if result.status == "no_route":
                    return failed(index, "no_route", "未找到所需步行路线。")
                if result.status != "ok" or result.route is None:
                    return failed(index, "data_error", "步行路线数据无法用于排程。")
                route = result.route
                return ScheduleEdge(
                    id=f"e{index}", origin=origin, destination=destination, status="ok",
                    duration_seconds=route.duration_seconds,
                    duration_minutes=math.ceil(route.duration_seconds / 60),
                    distance_meters=route.distance_meters, queried_at=result.queried_at,
                )
            except AmapTimeoutError:
                return failed(index, "timeout", "所需步行路线查询超时。")
            except AmapError as error:
                # Compare only the integration's fixed safe sentinel, never
                # forward exception text, upstream payloads or secret URLs.
                if str(error) == INVALID_DATA:
                    return failed(index, "data_error", "步行路线数据无法用于排程。")
                return failed(index, "failed", "暂时无法取得所需步行路线。")
            except Exception:
                return failed(index, "failed", "暂时无法取得所需步行路线。")

    tasks = [asyncio.create_task(query(index)) for index in range(len(pairs))]
    try:
        done, pending = await asyncio.wait(tasks, timeout=ROUTE_BATCH_TIMEOUT_SECONDS)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return [task.result() if task in done else failed(index, "timeout", "本次路线查询超时。")
                for index, task in enumerate(tasks)]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
