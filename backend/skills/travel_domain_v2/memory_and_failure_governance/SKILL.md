---
name: memory_and_failure_governance
description: 明确同意才保存偏好，失败与未知结果不能盲重跑。
agents: travel_coordinator, travel_planning, travel_budget, travel_place_reference
enabled: true
---
# memory_and_failure_governance

触发：每轮必用，尤其偏好保存／撤回、取消、错误及恢复。

流程：区分原话、明确偏好和模型推断；仅明确同意后提议保存来源、范围及版本，撤回优先于旧记忆；失败定位识别／检索／工具／权限／上游／提交阶段；检查未发、已发、完成与未知，再决定停止或只读核查。

输出：有限错误类别、真实停止原因、待用户补充／确认事项；推断画像不是已确认偏好，缓存摘要不是正式草案或完整任务恢复。

禁止：自动全局learn、越权画像、把偏好宣称已满足、隐蔽模型调用、重启返额、未知结果重发、用其他Agent新预算补跑、泄露凭据或思维链。

程序硬检查：owner／epoch／单调版本、CAS与幂等、共享任务和持久发送前计数、取消排空及容量；身份变化清理旧结果。监控技术成功不证明语义正确，Judge分数不豁免事实／权限阻塞。
