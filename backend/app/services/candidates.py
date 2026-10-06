import asyncio
from datetime import datetime, timezone

from app.integrations.amap import AmapClient, AmapTimeoutError
from app.schemas.candidates import (
    CandidateQuery, CandidateRequest, CandidateResponse, Interest,
    PlaceCandidate, RetrievalSource,
)
from app.schemas.place import Place


# Order is a product rule, independent of request order and asynchronous timing.
INTEREST_KEYWORDS: tuple[tuple[Interest, str], ...] = (
    ("摄影", "公园"), ("Citywalk", "步行街"), ("美食", "餐厅"),
    ("建筑", "历史建筑"), ("博物馆", "博物馆"), ("购物", "商场"),
)
MAX_CONCURRENT_SEARCHES = 3
BATCH_TIMEOUT_SECONDS = 12.0
PAGE_LIMIT = 20
OPTIONAL_LIMIT = 18
FAILURE_MESSAGE = "本次地点搜索未成功，请稍后主动重试。"
TIMEOUT_MESSAGE = "本次地点搜索超时，请稍后主动重试。"


def retrieval_sources(interests: list[Interest]) -> list[RetrievalSource]:
    selected = set(interests)
    seen_keywords: set[str] = set()
    sources: list[RetrievalSource] = []
    for interest, keyword in INTEREST_KEYWORDS:
        if interest in selected and keyword not in seen_keywords:
            sources.append(RetrievalSource(interest=interest, keyword=keyword))
            seen_keywords.add(keyword)
    return sources or [RetrievalSource(interest=None, keyword="旅游景点")]


def merge_candidates(
    request: CandidateRequest,
    sources: list[RetrievalSource],
    results: list[list[Place]],
) -> list[PlaceCandidate]:
    # Confirmed snapshots win even when a newer search has different fields.
    candidates = {
        place.id: PlaceCandidate(place=place, role="must_visit", retrieval_sources=[])
        for place in request.must_visit_places
    }
    must_ids = set(candidates)
    for source, places in zip(sources, results, strict=True):
        for place in places:
            if place.id == request.accommodation_place.id and place.id not in must_ids:
                continue
            candidate = candidates.setdefault(
                place.id, PlaceCandidate(place=place, role="optional", retrieval_sources=[]),
            )
            if source not in candidate.retrieval_sources:
                candidate.retrieval_sources.append(source)

    # Each query gets its next unselected eligible result, then yields its turn.
    # Read all sources first so a result selected early still has complete provenance.
    selected_set = set(must_ids)
    optional_ids: list[str] = []
    cursors = [0] * len(results)
    while len(optional_ids) < OPTIONAL_LIMIT:
        added = False
        for index, places in enumerate(results):
            while cursors[index] < len(places):
                place_id = places[cursors[index]].id
                cursors[index] += 1
                if place_id in candidates and place_id not in selected_set:
                    optional_ids.append(place_id)
                    selected_set.add(place_id)
                    added = True
                    break
            if len(optional_ids) == OPTIONAL_LIMIT:
                break
        if not added:
            break
    # Preserve the user's must-visit input order, not set iteration order.
    selected = [place.id for place in request.must_visit_places] + optional_ids
    return [candidates[place_id] for place_id in selected]


async def prepare_candidates(request: CandidateRequest, amap: AmapClient) -> CandidateResponse:
    sources = retrieval_sources(request.interests)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)

    async def search(source: RetrievalSource) -> tuple[CandidateQuery, list[Place]]:
        async with semaphore:
            try:
                # AmapClient owns page 1 / 20 and its existing connection-only
                # retry (at most two HTTP attempts). Never retry at batch level.
                places = (await amap.search(source.keyword, "上海"))[:PAGE_LIMIT]
                return CandidateQuery(
                    **source.model_dump(), status="success", result_count=len(places),
                ), places
            except AmapTimeoutError:
                return CandidateQuery(
                    **source.model_dump(), status="timeout", result_count=0,
                    message=TIMEOUT_MESSAGE,
                ), []
            except Exception:
                # Do not reflect raw upstream exceptions, URLs or secrets, even
                # for unexpected integration failures. Cancellation propagates.
                return CandidateQuery(
                    **source.model_dump(), status="failed", result_count=0,
                    message=FAILURE_MESSAGE,
                ), []

    tasks = [asyncio.create_task(search(source)) for source in sources]
    try:
        done, pending = await asyncio.wait(tasks, timeout=BATCH_TIMEOUT_SECONDS)
        for task in pending:
            task.cancel()
        # Drain cancelled HTTP requests and semaphore waiters before returning.
        # The 12-second deadline covers the entire batch, including queued work.
        await asyncio.gather(*pending, return_exceptions=True)
        outcomes = [
            task.result() if task in done else (
                CandidateQuery(
                    **source.model_dump(), status="timeout", result_count=0,
                    message=TIMEOUT_MESSAGE,
                ), [],
            )
            for source, task in zip(sources, tasks, strict=True)
        ]
    finally:
        # Also clean up when the whole request/service task itself is cancelled.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    queries = [query for query, _ in outcomes]
    successes = sum(query.status == "success" for query in queries)
    return CandidateResponse(
        status="success" if successes == len(queries) else "partial" if successes else "failed",
        queried_at=datetime.now(timezone.utc),
        keywords=[source.keyword for source in sources],
        queries=queries,
        candidates=merge_candidates(request, sources, [places for _, places in outcomes]),
    )
