---
name: archive_media_navigation
description: 历史只读、另存新记录；地图及媒体只主动操作。
agents: travel_coordinator, travel_planning, travel_place_reference
enabled: true
---
# archive_media_navigation

触发：保存／读取／继续规划、地图、照片和视频入口。

流程：核对当前目标与权限；地图和媒体只显式打开；历史展示原保存、生成和查询时间；继续规划创建独立输入副本，主动生成成功后才可另存；保存使用同一次冻结快照和操作ID。

输出：只读历史、未执行导航提议或明确保存状态。不确定结果先读回；历史数据不代表当前重新核验。

禁止：覆盖旧存档、自动保存／生成、在地图操作时新增路线查询、打开媒体即识别、批量视频识别、把照片／帧／地图实例写进存档。

程序硬检查：owner／会话／来源／当前版本、v1／v2／v3原语义、保存幂等及未知结果、独立媒体字节与ObjectURL生命周期。取消整次需求编辑保留原草案、费用和地图；上游已发送取消不保证停费。
