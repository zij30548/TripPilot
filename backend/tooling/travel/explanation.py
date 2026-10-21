"""Replay existing deterministic rules, then expose only program-owned facts to Qwen."""
from dataclasses import dataclass
from typing import Any

from core.schemas.explanation import ModelSelection, RenderedHighlight, ScheduleSnapshot, SnapshotEdge
from core.schemas.schedule import ScheduleResponse
from tooling.travel.schedule import build_required_schedule, build_schedule, OPTIONAL_MESSAGES
from tooling.travel.schedule_routes import optional_edges, required_edges, unique_pairs
from core.planning_limits import MAX_STARTED_ROUTES


def compact_schedule(schedule: ScheduleResponse) -> dict[str, Any]:
    """Shared test/replay projection; no map geometry, price or arbitrary explanation."""
    data = schedule.model_dump(mode="json")
    data.pop("rules")
    data.pop("unknowns")
    for place in [data["request"]["accommodation_place"], *data["request"]["must_visit_places"], *data["request"]["optional_places"]]:
        place.pop("address", None)
        place.pop("category", None)
        place.pop("region", None)
    data["edges"] = [{key: edge[key] for key in (
        "id", "origin", "destination", "transport_mode", "status", "duration_seconds",
        "duration_minutes", "source", "queried_at", "used",
    )} for edge in data["edges"]]
    for entry in data["unscheduled"]:
        entry.pop("message")
    for result in data["optional_results"]:
        for attempt in result["attempts"]:
            attempt.pop("message")
    return data


class ReplaySchedule(ScheduleResponse):
    """Internal result envelope for the same algorithm over validated estimates.

    It deliberately does not claim to carry full Walking/TransitRoute models.
    This never leaves the explanation boundary or changes /schedule-preview.
    """
    edges: list[SnapshotEdge]


def validate_snapshot(snapshot: ScheduleSnapshot) -> ScheduleResponse:
    request = snapshot.request.scheduling_input()
    # Reuse the algorithm with a compact validated edge envelope; no fabricated
    # distance/route/geometry and no unchecked model_construct bypass.
    edges = snapshot.edges
    if any(edge.transport_mode != request.transport_mode for edge in edges):
        raise ValueError("Mixed transport modes")
    # Required and optional phases use the original directed-edge planner. Exact
    # ordered identity also checks normalized coordinates and the bounded edge set.
    pairs = required_edges(request)
    if len(edges) < len(pairs):
        raise ValueError("Missing required edge outcomes")
    baseline = build_required_schedule(request, edges[:len(pairs)], snapshot.generated_at, response_type=ReplaySchedule)
    if not baseline.unscheduled and request.optional_places:
        pairs = unique_pairs(pairs + optional_edges(request, baseline.days))
    if len(pairs) != len(edges):
        raise ValueError("Unexpected edge query set")
    if sum(edge.status not in ("same_place", "budget_exhausted") for edge in edges) > MAX_STARTED_ROUTES:
        raise ValueError("Too many started route outcomes")
    for index, (edge, pair) in enumerate(zip(edges, pairs, strict=True)):
        if edge.id != f"e{index}" or edge.origin != pair[0] or edge.destination != pair[1]:
            raise ValueError("Invalid directed edge identity")
    expected = build_schedule(request, edges, snapshot.generated_at, response_type=ReplaySchedule)
    # Replaying the existing pure scheduler validates the WHOLE timeline, not
    # merely the Pydantic shape: dates, ordering, lunch/waits, home return, roles,
    # used flags, optional attempts and all recorded non-arrangement reasons.
    if compact_schedule(expected) != snapshot.model_dump(mode="json", exclude={"request": {"destination_id"}}):
        raise ValueError("Snapshot is inconsistent with the current scheduling rules")
    return expected


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    text: str


CODE_TYPES = {
    "arrangement": {"summary"}, "visits": {"visit"}, "movement": {"movement"},
    "reserved_time": {"lunch", "wait"}, "return_to_stay": {"return"},
    "not_arranged": {"unarranged"}, "optional_attempts": {"optional"}, "empty_day": {"empty"},
}


def build_facts(schedule: ScheduleResponse) -> list[Fact]:
    req = schedule.request
    required = {place.id for place in req.must_visit_places}
    places = {place.id: place for place in [req.accommodation_place, *req.must_visit_places, *req.optional_places]}
    edges = {edge.id: edge for edge in schedule.edges}
    facts: list[Fact] = []

    def add(kind: str, text: str) -> None:
        facts.append(Fact(id=f"f{len(facts)}", kind=kind, text=text))

    visits = [item for day in schedule.days for item in day.items if item.kind == "visit"]
    must_count = sum(item.place_id in required for item in visits)
    mode = "步行" if req.transport_mode == "walking" else "公交／地铁参考方案（含接驳）"
    add("summary", f"当前提交快照为 {req.start_date} 至 {req.end_date}，采用{mode}。"
        f"已安排必去 {must_count}/{len(required)} 个、可选 {len(visits) - must_count}/{len(req.optional_places)} 个。"
        "必去按输入顺序；可选只在必去完整后于每日尾部最多补充一个。")
    for day in schedule.days:
        if not day.items:
            add("empty", f"{day.date} 尚未安排景点，没有为此日期填入虚构活动。")
        for item in day.items:
            at = f"{day.date} {item.start_time}–{item.end_time}"
            if item.kind == "visit":
                role = "必去" if item.place_id in required else "可选"
                source = "默认规划设置" if item.duration_source == "default" else "用户设置"
                add("visit", f"{at}：{role}地点「{places[item.place_id].name}」，停留 {item.duration_minutes} 分钟（{source}，不是已核实游玩时长）。")
            elif item.kind in ("walk", "transit"):
                edge = edges[item.edge_id]
                detail = ("同地点，无需移动。" if edge.status == "same_place" else
                          f"采用本次记录的 {edge.duration_seconds:g} 秒，向上取整为 {edge.duration_minutes} 分钟；查询时间 {edge.queried_at.isoformat()}。")
                add("movement", f"{at}：「{places[item.from_place_id].name}」→「{places[item.to_place_id].name}」，{mode}。{detail}")
            elif item.kind == "lunch":
                add("lunch", f"{at}：午餐预留，未找到或预订餐厅，寻找餐厅和绕路未计入。")
            else:
                add("wait", f"{at}：等待 {item.duration_minutes} 分钟，为避免后续整段移动或参观跨越午餐预留。")
        if day.return_time is not None:
            add("return", f"{day.date} 预计 {day.return_time} 返回住宿参考点「{req.accommodation_place.name}」，未晚于每日结束 {req.daily_end_time}。")
    for item in schedule.unscheduled:
        add("unarranged", f"必去「{places[item.place_id].name}」尚未安排：{item.message}")
    for item in schedule.optional_results:
        if item.scheduled_date is None:
            why = ("因必去未完成，本次未试排。" if item.not_attempted_reason else
                   "；".join(f"{attempt.date}：{attempt.message}" for attempt in item.attempts))
            add("optional", f"可选「{places[item.place_id].name}」未采用：{why}")
    return facts


def render_selection(selection: ModelSelection, facts: list[Fact]) -> list[RenderedHighlight]:
    lookup = {fact.id: fact for fact in facts}
    used: set[str] = set()
    rendered = []
    for highlight in selection.highlights:
        selected = []
        for identifier in highlight.fact_ids:
            fact = lookup.get(identifier)
            if fact is None or identifier in used or fact.kind not in CODE_TYPES[highlight.message_code]:
                raise ValueError("Unknown, repeated or incompatible fact reference")
            used.add(identifier)
            selected.append(fact.text)
        rendered.append(RenderedHighlight(**highlight.model_dump(), text="\n".join(selected)))
    return rendered


def fixed_limitations(schedule: ScheduleResponse) -> list[str]:
    notices = [
        "基于当前提交快照的一致性核对，不代表后端重新向高德核实地点或路线来源；AI 仅选择解读重点。",
        "尚未核验营业、预约和旅行日期可用性；固定顺序与每日尾部有限补充不代表最优路线。",
    ]
    if schedule.unscheduled:
        notices.append(f"仍有 {len(schedule.unscheduled)} 个必去地点未安排，当前结果不代表全部要求已完成。")
    if schedule.request.transport_mode == "transit":
        notices.append("公交／地铁采用整条参考方案总耗时（含接驳），未验证旅行日期及出发时刻的运营班次、等车和换乘可行性。")
    else:
        notices.append("步行使用本次查询的参考估时，逐段向上取整到分钟，不保证旅行当天路况或实际速度。")
    return notices
