"""Request-local, paced, bounded directed edges; not an all-pairs matrix."""

import asyncio
import math
from datetime import datetime, timezone

from app.integrations.amap import AmapError, AmapTimeoutError
from app.integrations.amap_walking import AmapWalkingClient, INVALID_DATA
from app.schemas.place import ConfirmedPlace
from app.schemas.route import RouteEndpoint, WalkingRouteRequest
from app.schemas.schedule import ScheduleDay, ScheduleEdge, ScheduleRequest


ROUTE_BATCH_TIMEOUT_SECONDS = 20.0
MAX_CONCURRENT_ROUTES = 3
# Live 5B-1 batches hit AMap CUQPS (10021); retain batch-local launch pacing.
MIN_ROUTE_START_INTERVAL_SECONDS = 0.4
MAX_LOGICAL_EDGES = 29  # at most 17 required + 12 fixed-tail optional edges
EdgeKey = tuple[str, str, str, str]
EndpointPair = tuple[RouteEndpoint, RouteEndpoint]


def normalized_endpoint(place: ConfirmedPlace) -> RouteEndpoint:
    def coordinate(value: float) -> float:
        rounded = float(f"{value:.6f}")
        return 0.0 if rounded == 0 else rounded

    return RouteEndpoint(
        place_id=place.id, longitude=coordinate(place.longitude), latitude=coordinate(place.latitude),
    )


def edge_key(origin: RouteEndpoint, destination: RouteEndpoint) -> EdgeKey:
    return (origin.place_id, f"{origin.longitude:.6f},{origin.latitude:.6f}",
            destination.place_id, f"{destination.longitude:.6f},{destination.latitude:.6f}")


def unique_pairs(pairs: list[EndpointPair]) -> list[EndpointPair]:
    unique: dict[EdgeKey, EndpointPair] = {}
    for origin, destination in pairs:
        unique.setdefault(edge_key(origin, destination), (origin, destination))
    return list(unique.values())


def required_edges(request: ScheduleRequest) -> list[EndpointPair]:
    stay = normalized_endpoint(request.accommodation_place)
    places = [normalized_endpoint(place) for place in request.must_visit_places]
    pairs = [(endpoint, other) for place in places for endpoint, other in [(stay, place), (place, stay)]]
    pairs.extend(zip(places, places[1:]))
    return unique_pairs(pairs)


def optional_edges(request: ScheduleRequest, baseline_days: list[ScheduleDay]) -> list[EndpointPair]:
    """Only immutable day-tail→optional and optional→stay, never optional→optional."""
    stay = normalized_endpoint(request.accommodation_place)
    required = {place.id: normalized_endpoint(place) for place in request.must_visit_places}
    optionals = [normalized_endpoint(place) for place in request.optional_places]
    pairs: list[EndpointPair] = []
    for day in baseline_days:
        visits = [item for item in day.items if item.kind == "visit"]
        tail = required[visits[-1].place_id] if visits else stay
        pairs.extend((tail, optional) for optional in optionals)
    pairs.extend((optional, stay) for optional in optionals)
    return unique_pairs(pairs)


class ScheduleRouteCollector:
    """One request shares its deadline, pacing, semaphore and directed memo across phases."""

    def __init__(self, amap: AmapWalkingClient) -> None:
        self.amap = amap
        self.deadline = asyncio.get_running_loop().time() + ROUTE_BATCH_TIMEOUT_SECONDS
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_ROUTES)
        self.start_lock = asyncio.Lock()
        self.next_start = 0.0
        self.pairs: list[EndpointPair] = []
        self.indices: dict[EdgeKey, int] = {}
        self.resolved: dict[int, ScheduleEdge] = {}
        self.started: set[int] = set()

    @property
    def results(self) -> list[ScheduleEdge]:
        return [self.resolved[index] for index in range(len(self.pairs))]

    def failed(self, index: int, status: str, message: str) -> ScheduleEdge:
        origin, destination = self.pairs[index]
        return ScheduleEdge(id=f"e{index}", origin=origin, destination=destination, status=status,
                            queried_at=datetime.now(timezone.utc), message=message)

    def budget_exhausted(self, index: int) -> ScheduleEdge:
        return self.failed(index, "budget_exhausted", "本次查询截止前未启动此路段，尚未完成核实。")

    async def wait_to_start(self) -> bool:
        async with self.start_lock:
            loop = asyncio.get_running_loop()
            delay = max(0.0, self.next_start - loop.time())
            if loop.time() + delay >= self.deadline:
                return False
            if delay > 0:
                await asyncio.sleep(delay)
            if loop.time() >= self.deadline:
                return False
            self.next_start = loop.time() + MIN_ROUTE_START_INTERVAL_SECONDS
            return True

    async def query(self, index: int) -> ScheduleEdge:
        origin, destination = self.pairs[index]
        async with self.semaphore:
            try:
                if not await self.wait_to_start():
                    return self.budget_exhausted(index)
                self.started.add(index)
                result = await self.amap.walking(WalkingRouteRequest(origin=origin, destination=destination))
                if result.status == "no_route":
                    return self.failed(index, "no_route", "未找到所需步行路线。")
                if result.status != "ok" or result.route is None:
                    return self.failed(index, "data_error", "步行路线数据无法用于排程。")
                route = result.route
                return ScheduleEdge(id=f"e{index}", origin=origin, destination=destination, status="ok",
                                    duration_seconds=route.duration_seconds,
                                    duration_minutes=math.ceil(route.duration_seconds / 60),
                                    distance_meters=route.distance_meters, queried_at=result.queried_at)
            except AmapTimeoutError:
                return self.failed(index, "timeout", "所需步行路线查询超时。")
            except AmapError as error:
                if str(error) == INVALID_DATA:
                    return self.failed(index, "data_error", "步行路线数据无法用于排程。")
                return self.failed(index, "failed", "暂时无法取得所需步行路线。")
            except Exception:
                return self.failed(index, "failed", "暂时无法取得所需步行路线。")

    async def collect(self, pairs: list[EndpointPair]) -> list[ScheduleEdge]:
        new_pairs = [pair for pair in unique_pairs(pairs) if edge_key(*pair) not in self.indices]
        if len(self.pairs) + len(new_pairs) > MAX_LOGICAL_EDGES:
            raise ValueError("Directed route budget exceeded")
        tasks: dict[int, asyncio.Task[ScheduleEdge]] = {}
        for origin, destination in new_pairs:
            index = len(self.pairs)
            self.indices[edge_key(origin, destination)] = index
            self.pairs.append((origin, destination))
            if origin.place_id == destination.place_id or (
                origin.longitude == destination.longitude and origin.latitude == destination.latitude
            ):
                self.resolved[index] = ScheduleEdge(
                    id=f"e{index}", origin=origin, destination=destination, status="same_place",
                    duration_seconds=0, duration_minutes=0, distance_meters=0, source="same_place",
                    queried_at=datetime.now(timezone.utc),
                )
            elif asyncio.get_running_loop().time() >= self.deadline:
                self.resolved[index] = self.budget_exhausted(index)
            else:
                tasks[index] = asyncio.create_task(self.query(index))
        if not tasks:
            return self.results
        try:
            remaining = max(0.0, self.deadline - asyncio.get_running_loop().time())
            done, pending = await asyncio.wait(tasks.values(), timeout=remaining)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for index, task in tasks.items():
                self.resolved[index] = task.result() if task in done else (
                    self.failed(index, "timeout", "本次路线查询超时。")
                    if index in self.started else self.budget_exhausted(index)
                )
            return self.results
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)


async def collect_schedule_edges(request: ScheduleRequest, amap: AmapWalkingClient) -> list[ScheduleEdge]:
    # Backward-compatible must-only helper; orchestration shares one collector
    # instance explicitly when it adds the optional phase.
    return await ScheduleRouteCollector(amap).collect(required_edges(request))
