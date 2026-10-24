"""Chat-native proposals and vetted general guidance, with no domain writes.

The model chooses one bounded action. Program text never claims the browser
has already applied it. The caller's owner/turn guard runs for every tool.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from agents.tools import ToolSpec
from core.chat_cities import CITIES, legacy_city_id
from core.llm_utils import RuntimeFailure
from core.schemas.chat import Intent
from core.schemas.dialogue import DialogueActions, GROUPS, TOOL_VERSION, group_schema, obj, validate_action
from pydantic import ValidationError

GUIDANCE = {
    "greeting": "你好，可以告诉我想去哪里、什么时候出发，以及和谁一起去。我可以帮你整理需求、核对真实地点和行程；具体营业、预约和实时票价需要可靠资料。",
    "seniors": "带长辈旅行，可先确认同行者愿意走多久、是否需要无障碍通行，再减少一天内的转场并留出休息。把必须去的地点和可放弃的地点分开。具体场馆的电梯、轮椅与无障碍条件需要逐一核实；目前排程不会自动优化体力负担。",
    "families": "亲子出行可以围绕孩子的作息安排，给用餐、休息和临时调整留时间，先确认儿童年龄及同行人数。儿童票、身高界限、陪同和推车规定应查看对应场馆的资料，不能用另一场馆的规则代替。",
    "packing": "先按旅行天数、实际天气与活动准备证件、衣物、充电设备和个人常用物品；带儿童或长辈时再补充各自所需。证件和贵重物品随身保管。航空、铁路及场馆的携带限制需要按对应运营方核实，我没有据此确认任何具体物品一定可带。",
    "pace": "想玩得轻松，可以减少必去地点、增加停留并缩短每日活动窗口，预留休息和交通缓冲。我可以按你的明确选择修改这些设置；现有排程不会自动判断体力，也不保证路线最优。",
    "planning": "可以先确定目的地、日期、人数和总预算，再确认住宿参考点及最想去的真实地点。地点须经过搜索和你的明确确认；随后才能主动生成参考行程，核对时间、费用并按需预览调整。",
    "scope": "当前可以执行上海、杭州、北京、广州、成都或西安的一城、1～7天、一个住宿参考点，最多6个必去和3个有效可选地点。跨城想法可以继续沟通，但不能冒称已经执行跨城排程；机票、酒店预订与支付尚未接入。",
    "live_availability": "我目前没有实时预约余量或售罄数据，无法确认现在还有多少名额、是否约满或一定能入场。已有静态规则可以单独查阅，实时情况仍需在对应官方预约入口核对。",
    "current_rules": "我目前没有足以确认这个场所当前营业、预约、门票或临时活动规则的依据，因此不能给出确定结论。可以明确场馆和馆区后查询已收录资料；未收录或日期适用性不明时会如实说明。",
    "unsupported": "这项能力目前尚未接入，我不能声称已经查询或完成。你可以继续让我整理旅行需求、搜索并确认地点，或核对当前已有行程和费用。",
}


def requirement_text(intent: Intent, *, changed: bool) -> str:
    lines = ["已根据你的原话整理需求：" if changed else "当前记录的旅行需求："]
    city = CITIES.get(legacy_city_id(intent.destination_id) or intent.destination_id)
    if city:
        lines.append(f"目的地：{city.name}")
    if intent.start_date or intent.end_date:
        lines.append(f"日期：{intent.start_date or '尚未说明'} 至 {intent.end_date or '尚未说明'}")
    if intent.travelers is not None:
        lines.append(f"人数：{intent.travelers} 人")
    if intent.budget_cny is not None:
        lines.append(f"全程总预算：人民币 {intent.budget_cny} 元（全部旅客）")
    if intent.accommodation_hint:
        lines.append(f"住宿线索：{intent.accommodation_hint}（尚须核对真实地点）")
    if intent.required_place_hints:
        lines.append("想去的地点线索：" + "、".join(intent.required_place_hints))
    if intent.transport_mode:
        lines.append("交通方式：" + ("步行" if intent.transport_mode == "walking" else "公交／地铁"))
    if intent.preferences:
        lines.append("本次偏好：" + "；".join(intent.preferences) + "（未承诺已转成排程约束）")
    missing = []
    for key, label in (("destination_id", "目的地"), ("start_date", "出发日期"), ("end_date", "结束日期"),
                       ("travelers", "人数"), ("budget_cny", "总预算")):
        if getattr(intent, key) is None:
            missing.append(label)
    if missing:
        lines.append("尚需说明：" + "、".join(missing) + "。")
    lines.append("这些是需求输入，不代表地点已确认或行程已生成。")
    return "\n".join(lines)


def tools(current: Callable[[], Awaitable[dict]]) -> list[ToolSpec]:
    result = []
    for name, (description, names) in GROUPS.items():
        async def propose(arguments: dict, allowed=names) -> dict:
            await current()
            action = arguments.get("action")
            if action not in allowed:
                raise RuntimeFailure("dialogue_action_invalid")
            proposal = validate_action(action, {key: value for key, value in arguments.items() if key != "action"})
            return {"message": "正在核对这项请求；实际结果以接下来的程序反馈为准。", "dialogue_action": proposal}
        result.append(ToolSpec(name, description, group_schema(names), propose, terminal=True, version=TOOL_VERSION))

    async def information(arguments: dict) -> dict:
        await current()
        return {"message": GUIDANCE[arguments["topic"]], "kind": "general_guidance", "topic": arguments["topic"]}
    result.append(ToolSpec("travel_information", "选择程序审定的一般旅行建议；不提供具体场馆/日期事实。实时余量用live_availability；没有可靠的营业预约票价依据用current_rules。不得用泛泛建议冒充用户问题已核实。",
                           obj({"topic": {"type": "string", "enum": list(GUIDANCE)}}), information,
                           terminal=True, version=TOOL_VERSION))
    return result


def project_action(conversation: dict, task: dict | None) -> dict:
    base = {"schema_version": "dialogue-action-v1", "conversation_id": conversation["id"],
            "revision": conversation["revision"], "operation_id": None, "action": None}
    turn = conversation["last_turn"]
    if (task is None or task["stale"] or task["conversation_revision"] != conversation["revision"]
            or task["state"] not in {"waiting", "succeeded"} or turn is None or turn["state"] != "succeeded"
            or task["operation_id"] != turn["operation_id"]):
        return base
    result = task["checkpoint"].get("result")
    if not isinstance(result, dict) or result.get("surface") != "dialogue-v2":
        return base
    base["operation_id"] = turn["operation_id"]
    rows = result.get("tool_results")
    if not isinstance(rows, list):
        raise RuntimeFailure("dialogue_receipt_invalid")
    proposals = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("data"), dict):
            raise RuntimeFailure("dialogue_receipt_invalid")
        data = row["data"]
        if "dialogue_action" not in data:
            continue
        value = data["dialogue_action"]
        if (row.get("name") not in GROUPS or set(data) != {"message", "dialogue_action"}
                or not isinstance(value, dict) or set(value) != {"name", "arguments"}
                or value["name"] not in GROUPS[row["name"]][1]):
            raise RuntimeFailure("dialogue_receipt_invalid")
        proposals.append(validate_action(value["name"], value["arguments"]))
    if len(proposals) > 1:
        raise RuntimeFailure("dialogue_receipt_invalid")
    base["action"] = proposals[0] if proposals else None
    return base


def project_actions(conversation: dict, task: dict | None) -> dict:
    """New exact surface only: single work or the two independent read pair.

    Receipts bind the submitted browser generation; the current browser must
    still recheck its snapshot and record actual execution. No database writes
    or tool execution happens when reading this projection.
    """
    base = {"schema_version": "dialogue-actions-v2", "conversation_id": conversation["id"],
            "revision": conversation["revision"], "operation_id": None,
            "workspace_version": None, "actions": [], "program_messages": []}
    turn = conversation["last_turn"]
    if (task is None or task["stale"] or task["conversation_revision"] != conversation["revision"]
            or task["state"] not in {"waiting", "succeeded"} or turn is None or turn["state"] != "succeeded"
            or task["operation_id"] != turn["operation_id"]):
        return base
    result = task["checkpoint"].get("result")
    if not isinstance(result, dict) or result.get("surface") != "dialogue-v3":
        return base
    rows = result.get("tool_results")
    if not isinstance(rows, list) or len(rows) > 4:
        raise RuntimeFailure("dialogue_receipt_invalid")
    proposals = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("data"), dict):
            raise RuntimeFailure("dialogue_receipt_invalid")
        data = row["data"]
        if "dialogue_action" not in data:
            continue
        value = data["dialogue_action"]
        if (row.get("name") not in GROUPS or set(data) != {"message", "dialogue_action"}
                or not isinstance(value, dict) or set(value) != {"name", "arguments"}
                or value["name"] not in GROUPS[row["name"]][1]):
            raise RuntimeFailure("dialogue_receipt_invalid")
        proposals.append(validate_action(value["name"], value["arguments"]))
    runtime = result.get("runtime", {})
    if not isinstance(runtime, dict):
        raise RuntimeFailure("dialogue_receipt_invalid")
    messages = runtime.get("program_messages", [])
    if messages and runtime.get("runtime") != "echomind_travel_full_v2":
        raise RuntimeFailure("dialogue_receipt_invalid")
    try:
        return DialogueActions.model_validate(base | {"operation_id": turn["operation_id"],
            "workspace_version": result.get("workspace_version"), "actions": proposals,
            "program_messages": messages}).model_dump(mode="json")
    except ValidationError:
        raise RuntimeFailure("dialogue_receipt_invalid") from None
