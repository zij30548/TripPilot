---
name: place_discovery_confirmation
description: 真实地点必须搜索、预览、核对与明确确认。
agents: travel_coordinator, travel_place_reference
enabled: true
---
# place_discovery_confirmation

触发：住宿、必去、候选准备、地点歧义或照片及单帧线索。

流程：确认当前城市和用户明确搜索意图；主动搜索一个有界关键词；展示真实候选及地址；点击候选只预览；用户明确确认后重查当前批次、城市、去重和容量再提交。

输出：可追溯候选或未执行的确认提议。城市未知、跨城、同名歧义或无结果时说明缺口；媒体仅提供一个可能名称／城市或拒识。

禁止：自动采用首条、伪造POI ID／坐标／行政归属、用同名命中证明照片身份、自动切城或把媒体写入存档。

程序硬检查：高德归属及坐标、服务端候选ID、当前owner／版本、六必去上限、重复地点及取消／ABA隔离。识别、搜索与确认分开授权。
