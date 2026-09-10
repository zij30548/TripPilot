from datetime import time, timedelta

from fastapi import APIRouter

from app.schemas.trip import (
    Activity, BudgetBreakdown, DayPlan, TransportSegment,
    TripPlan, TripRequest, WeatherSummary,
)

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("/plan", response_model=TripPlan)
async def plan_trip(request: TripRequest) -> TripPlan:
    """Attach request context to a fixed, explicitly fictional two-day template."""
    days = [
        DayPlan(
            day=1, date=request.start_date, title="沿江风景与城市漫步",
            activities=[
                Activity(id="d1-a1", name="外滩漫步", category="sightseeing",
                         estimated_cost=0, description="沿江散步，记录城市的建筑轮廓。",
                         start_time=time(9), end_time=time(10, 30)),
                Activity(id="d1-a2", name="午餐与休息", category="food",
                         estimated_cost=120, description="留出一段时间，慢慢吃顿午饭。",
                         start_time=time(12), end_time=time(13)),
                Activity(id="d1-a3", name="南京东路街区", category="shopping",
                         estimated_cost=80, description="街区漫步与自由购物示例。",
                         start_time=time(14), end_time=time(16)),
            ],
            transports=[
                TransportSegment(from_activity_id="d1-a1", to_activity_id="d1-a2",
                                 mode="metro", duration_minutes=20, estimated_cost=10,
                                 description="前往午餐区域；路线与用时为演示值。"),
                TransportSegment(from_activity_id="d1-a2", to_activity_id="d1-a3",
                                 mode="taxi", duration_minutes=15, estimated_cost=10,
                                 description="前往下一街区；未查询真实交通。"),
            ],
            weather=WeatherSummary(date=request.start_date, condition="多云",
                                   min_temperature=21, max_temperature=27, rain_risk=20),
        ),
        DayPlan(
            day=2, date=request.start_date + timedelta(days=1), title="文化探索与街区慢游",
            activities=[
                Activity(id="d2-a1", name="博物馆参观示例", category="museum",
                         estimated_cost=100, description="文化参观占位活动，费用不对应任何真实场馆。",
                         start_time=time(9), end_time=time(11)),
                Activity(id="d2-a2", name="午餐与休息", category="food",
                         estimated_cost=120, description="午餐后稍作休息，为下午留点余地。",
                         start_time=time(12), end_time=time(13)),
                Activity(id="d2-a3", name="武康路漫步", category="sightseeing",
                         estimated_cost=0, description="观察街区建筑，寻找喜欢的拍摄角度。",
                         start_time=time(14), end_time=time(16)),
            ],
            transports=[
                TransportSegment(from_activity_id="d2-a1", to_activity_id="d2-a2",
                                 mode="metro", duration_minutes=25, estimated_cost=20,
                                 description="前往午餐区域；交通方式与费用为示例。"),
                TransportSegment(from_activity_id="d2-a2", to_activity_id="d2-a3",
                                 mode="walking", duration_minutes=15, estimated_cost=0,
                                 description="步行衔接下一活动；未校验真实距离。"),
            ],
            weather=WeatherSummary(date=request.start_date + timedelta(days=1), condition="小雨",
                                   min_temperature=20, max_temperature=25, rain_risk=60),
        ),
    ]
    activities = [activity for day in days for activity in day.activities]
    breakdown = BudgetBreakdown(
        transport=sum(segment.estimated_cost for day in days for segment in day.transports),
        food=sum(activity.estimated_cost for activity in activities if activity.category == "food"),
        tickets=sum(activity.estimated_cost for activity in activities if activity.category == "museum"),
        other=sum(activity.estimated_cost for activity in activities
                  if activity.category not in ("food", "museum")),
    )
    return TripPlan(
        destination="上海",
        request=request,
        budget_breakdown=breakdown,
        estimated_cost=sum(breakdown.model_dump().values()),
        notice=(
            "固定两日 Mock 示例：日期从你的开始日期起排列。活动、费用、交通与天气均为虚构演示数据，"
            "不是报价或预报；尚未按人数、预算、偏好和每日时间约束调整。费用为全体旅客的示例总额。"
        ),
        days=days,
    )
