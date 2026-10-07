"""Deterministic fixed-order preview; all walking data is injected, no I/O."""

from datetime import datetime, timedelta

from app.schemas.schedule import (
    ScheduleDay, ScheduleEdge, ScheduleItem, ScheduleRequest, ScheduleResponse,
    OptionalAttempt, OptionalResult, UnscheduledPlace, clock_minutes,
)
from app.services.schedule_routes import edge_key, normalized_endpoint


RULES = [
    "先按已确认必去地点原顺序安排，不跳过或自动重排；必去完整后才尝试你明确选入的可选地点。",
    "每日从住宿参考点出发并返回；每次加入地点前验证当日回程时间。",
    "步行采用本次高德查询的预计秒数，逐段向上取整到分钟；没有直线或 Mock 用时替代。",
    "停留为默认 60 分钟或你的设置，不代表真实游玩需求或营业时间。",
    "启用午餐时，移动与参观均避开保留时段；必要时等待，不虚构餐厅。",
    "当天放不下时下一天从住宿重试同一地点；需要的路线不可用时保留前缀，不绕过失败继续。",
    "同一 POI ID 或六位小数坐标相同按零移动处理，不查询上游；该地点的停留仍保留。",
    "未采用路线的查询失败只供参考，不否定已完成的排程。所有时间按上海本地时间解释。",
    "可选地点仅接在当天最后一个必去之后、返回住宿之前，每天最多一个；不会改变已成立的必去参观时间。",
    "按日期和所选可选地点顺序尝试；失败后可尝试下一项或下一天，不代表其他日期也无法安排。",
]
UNKNOWNS = [
    "尚未校验地点营业时间、预约与闭馆、真实建议停留或旅行日期可用性。",
    "未校验票价、餐饮住宿费用或预算，也未接入真实天气。",
    "步行时间是查询时参考估计，不保证旅行当天路况与实际速度；不提供公交或多方式比较。",
    "午餐仅保留时间，不代表找到或预订餐厅；这份预览不替换原 Mock 行程、活动绑定或路线。",
]


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def append_block(request: ScheduleRequest, items: list[ScheduleItem], now: int, duration: int, **fields: object) -> int:
    lunch_start, lunch_end = clock_minutes(request.lunch.start_time), clock_minutes(request.lunch.end_time)
    if duration > 0 and request.lunch.enabled and now < lunch_end and now + duration > lunch_start:
        if now < lunch_start:
            items.append(ScheduleItem(kind="wait", start_time=hhmm(now), end_time=hhmm(lunch_start),
                                      duration_minutes=lunch_start - now))
        items.append(ScheduleItem(kind="lunch", start_time=hhmm(lunch_start), end_time=hhmm(lunch_end),
                                  duration_minutes=lunch_end - lunch_start))
        now = lunch_end
    items.append(ScheduleItem(start_time=hhmm(now), end_time=hhmm(now + duration),
                              duration_minutes=duration, **fields))
    return now + duration


def append_walk(request: ScheduleRequest, items: list[ScheduleItem], now: int, edge: ScheduleEdge) -> int:
    assert edge.duration_minutes is not None
    return append_block(request, items, now, edge.duration_minutes, kind="walk",
                        from_place_id=edge.origin.place_id, to_place_id=edge.destination.place_id,
                        edge_id=edge.id)


def build_required_schedule(
    request: ScheduleRequest, edges: list[ScheduleEdge], generated_at: datetime,
) -> ScheduleResponse:
    """Pure given request/normalized edges and the injected display timestamp."""
    by_edge = {edge_key(edge.origin, edge.destination): edge for edge in edges}
    durations = {setting.place_id: setting for setting in request.duration_settings}
    start, end = clock_minutes(request.daily_start_time), clock_minutes(request.daily_end_time)
    dates = [request.start_date + timedelta(days=index) for index in range((request.end_date - request.start_date).days + 1)]
    days = [ScheduleDay(date=date, items=[]) for date in dates]
    stay = normalized_endpoint(request.accommodation_place)
    current_day = 0
    current_time = start
    current_endpoint = stay
    prefix: list[ScheduleItem] = []
    completed_day: list[ScheduleItem] = []
    visited = 0
    blocked_reason = "time_window"
    blocked_message = "在当前固定顺序、停留与每日时间窗下，没有剩余日期可安排此地点并返回住宿。"

    def finish_day() -> None:
        if completed_day:
            days[current_day].items = completed_day.copy()
            days[current_day].return_time = completed_day[-1].end_time

    for place in request.must_visit_places:
        duration = durations[place.id]
        destination = normalized_endpoint(place)
        placed = False
        route_blocked = False
        while current_day < len(days):
            # A known impossible remaining stay needs no adjacent route. Move
            # days before treating that unused edge's failure as consequential.
            lower_bound: list[ScheduleItem] = []
            earliest_finish = append_block(request, lower_bound, current_time, duration.minutes, kind="visit",
                                           place_id=place.id, duration_source=duration.source)
            if earliest_finish <= end:
                outward = by_edge.get(edge_key(current_endpoint, destination))
                backward = by_edge.get(edge_key(destination, stay))
                unavailable = next((edge for edge in (outward, backward)
                                    if edge is None or edge.status not in ("ok", "same_place")), None)
                # None may mean a missing map edge as well as no failure; use
                # explicit membership checks rather than invent missing times.
                if outward is None or backward is None or unavailable is not None:
                    status = unavailable.status if unavailable is not None else "data_error"
                    blocked_reason = {"timeout": "route_timeout", "budget_exhausted": "route_timeout", "no_route": "no_route",
                                      "data_error": "route_data_error"}.get(status, "route_failed")
                    blocked_message = {
                        "route_timeout": "前往此地点或从此地点返回住宿所需的步行路线超时，当前无法继续核实排程。",
                        "no_route": "未找到前往此地点或返回住宿所需的步行路线，已保留此前可完成的安排。",
                        "route_data_error": "所需步行路线数据不完整或异常，当前无法继续核实排程。",
                        "route_failed": "所需步行路线获取失败，当前无法继续核实排程。",
                    }[blocked_reason]
                    if status == "budget_exhausted":
                        blocked_message = "本次查询截止前未启动所需步行路段，当前无法继续核实排程。"
                    route_blocked = True
                    break
                trial = prefix.copy()
                arrival = append_walk(request, trial, current_time, outward)
                visit_end = append_block(request, trial, arrival, duration.minutes, kind="visit",
                                         place_id=place.id, duration_source=duration.source)
                returning = trial.copy()
                return_end = append_walk(request, returning, visit_end, backward)
                if return_end <= end:
                    prefix, completed_day = trial, returning
                    current_time, current_endpoint = visit_end, destination
                    visited += 1
                    placed = True
                    break
            finish_day()
            current_day += 1
            prefix, completed_day = [], []
            current_time, current_endpoint = start, stay
        if not placed:
            if route_blocked:
                finish_day()
            break
    else:
        finish_day()

    unscheduled: list[UnscheduledPlace] = []
    for index, place in enumerate(request.must_visit_places[visited:]):
        unscheduled.append(UnscheduledPlace(
            place_id=place.id,
            reason=blocked_reason if index == 0 else "current_order_not_continued",
            message=blocked_message if index == 0 else "按当前固定顺序未继续尝试此地点；不代表此地点本身无法完成。",
        ))
    used_ids = {item.edge_id for day in days for item in day.items if item.kind == "walk"}
    return ScheduleResponse(
        status="complete" if not unscheduled else "partial" if visited else "unscheduled",
        generated_at=generated_at, request=request,
        days=days, unscheduled=unscheduled,
        edges=[edge.model_copy(update={"used": edge.id in used_ids}) for edge in edges],
        rules=RULES.copy(), unknowns=UNKNOWNS.copy(),
    )


OPTIONAL_MESSAGES = {
    "scheduled": "已加入当天尾部，并验证可在日末前返回住宿。",
    "time_window": "当天尾部时间不足以容纳移动、停留和回住宿，可继续尝试其他日期。",
    "no_route": "未找到当天尝试所需的步行路线。",
    "timeout": "当天尝试所需的步行路线查询超时。",
    "data_error": "当天尝试所需步行路线数据异常，未据此安排。",
    "failed": "当天尝试所需的步行路线获取失败。",
    "budget_exhausted": "本次查询截止前未启动所需路段，尚未完成当天核实。",
    "day_slot_used": "当天已选入更靠前的一个可选地点，此地点当日未尝试，可继续尝试其他日期。",
}


def add_optional_schedule(
    request: ScheduleRequest, baseline: ScheduleResponse, edges: list[ScheduleEdge],
) -> ScheduleResponse:
    """Pure tail insertion over immutable required-day prefixes and injected edges."""
    results = [OptionalResult(place_id=place.id) for place in request.optional_places]
    if baseline.unscheduled:
        for result in results:
            result.not_attempted_reason = "must_incomplete"
        return baseline.model_copy(update={"optional_results": results})
    days = [day.model_copy(deep=True) for day in baseline.days]
    by_edge = {edge_key(edge.origin, edge.destination): edge for edge in edges}
    durations = {setting.place_id: setting for setting in request.duration_settings}
    musts = {place.id: place for place in request.must_visit_places}
    stay = normalized_endpoint(request.accommodation_place)
    end = clock_minutes(request.daily_end_time)
    for day_index, baseline_day in enumerate(baseline.days):
        visit_indices = [index for index, item in enumerate(baseline_day.items) if item.kind == "visit"]
        if visit_indices:
            last = visit_indices[-1]
            # Remove only the old return suffix, including a lunch reservation
            # that belonged solely to that return. Never alter the visit prefix.
            prefix = baseline_day.items[:last + 1]
            now = clock_minutes(prefix[-1].end_time)
            origin = normalized_endpoint(musts[prefix[-1].place_id])
        else:
            prefix, now, origin = [], clock_minutes(request.daily_start_time), stay
        chosen = False
        for place, result in zip(request.optional_places, results, strict=True):
            if result.scheduled_date is not None:
                continue
            outcome = "day_slot_used" if chosen else "time_window"
            if not chosen:
                setting = durations[place.id]
                lower_bound: list[ScheduleItem] = []
                earliest_finish = append_block(request, lower_bound, now, setting.minutes, kind="visit",
                                               place_id=place.id, duration_source=setting.source)
                if earliest_finish <= end:
                    destination = normalized_endpoint(place)
                    outward = by_edge.get(edge_key(origin, destination))
                    backward = by_edge.get(edge_key(destination, stay))
                    failed = [edge for edge in (outward, backward)
                              if edge is None or edge.status not in ("ok", "same_place")]
                    if failed:
                        outcome = failed[0].status if failed[0] is not None else "data_error"
                    else:
                        trial = prefix.copy()
                        arrival = append_walk(request, trial, now, outward)
                        visit_end = append_block(request, trial, arrival, setting.minutes, kind="visit",
                                                 place_id=place.id, duration_source=setting.source)
                        return_end = append_walk(request, trial, visit_end, backward)
                        if return_end <= end:
                            days[day_index] = ScheduleDay(date=baseline_day.date, items=trial,
                                                          return_time=hhmm(return_end))
                            chosen, outcome = True, "scheduled"
                            result.scheduled_date = baseline_day.date
            result.attempts.append(OptionalAttempt(date=baseline_day.date, outcome=outcome,
                                                    message=OPTIONAL_MESSAGES[outcome]))
    used_ids = {item.edge_id for day in days for item in day.items if item.kind == "walk"}
    scheduled = sum(item.kind == "visit" for day in days for item in day.items)
    total = len(request.must_visit_places) + len(request.optional_places)
    return baseline.model_copy(update={
        "status": "complete" if scheduled == total else "partial" if scheduled else "unscheduled",
        "days": days, "optional_results": results,
        "edges": [edge.model_copy(update={"used": edge.id in used_ids}) for edge in edges],
    })


def build_schedule(request: ScheduleRequest, edges: list[ScheduleEdge], generated_at: datetime) -> ScheduleResponse:
    """Pure public wrapper, preserving required-only callers and their schedule."""
    return add_optional_schedule(request, build_required_schedule(request, edges, generated_at), edges)
