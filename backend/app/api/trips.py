from datetime import time

from fastapi import APIRouter

from app.schemas.trip import Activity, DayPlan, TripPlan, TripRequest

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("/plan", response_model=TripPlan)
async def plan_trip(request: TripRequest) -> TripPlan:
    """Validate the request and return a fixed fixture, independent of its values."""
    return TripPlan(
        destination="上海",
        estimated_cost=1000,
        notice=(
            "固定两日 Mock 行程，仅用于演示。费用与时间均为虚构测试值，"
            "并非真实报价、开放时间或交通数据；尚未按提交的日期、预算及偏好调整。"
        ),
        days=[
            DayPlan(
                day=1,
                title="城市漫步示例",
                activities=[
                    Activity(name="外滩漫步", description="示例活动：沿江散步与摄影。",
                             start_time=time(9), end_time=time(10, 30)),
                    Activity(name="午餐", description="示例活动：午餐与休息。",
                             start_time=time(12), end_time=time(13)),
                    Activity(name="南京东路散步", description="示例活动：街区漫步。",
                             start_time=time(14), end_time=time(16)),
                ],
            ),
            DayPlan(
                day=2,
                title="建筑与街区示例",
                activities=[
                    Activity(name="武康路漫步", description="示例活动：观察街区建筑。",
                             start_time=time(9), end_time=time(11)),
                    Activity(name="午餐", description="示例活动：午餐与休息。",
                             start_time=time(12), end_time=time(13)),
                    Activity(name="街区自由活动", description="示例活动：自由漫步与摄影。",
                             start_time=time(14), end_time=time(16)),
                ],
            ),
        ],
    )
