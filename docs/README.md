# 文档导航

**独立客服项目**：本机5173现为[智应 ServeFlow](SERVEFLOW_UI.md)，基于上传的原EchoMind客服前端进行品牌／样式调整；[启动说明](ECHOMIND_ORIGINAL_LOCAL.md)。下方TripPilot旅行重构、权限和验收范围不自动适用于这个独立开发入口。

**当前ServeFlow导航**：[对话／监控两页](SERVEFLOW_MONITOR_UI.md)，无Java/Python切换、独立知识管理或评测操作。聊天按需RAG与后台评测保留，监控指标是技术执行统计；历史三页截图不代表当前导航。

[ServeFlow知识资料](SERVEFLOW_KNOWLEDGE.md)：六篇智能设备＋数字服务演示规则、公开参考、实际本机更新和质量边界；不是新旅行语料或真实企业政策。

[ServeFlow演示订单](SERVEFLOW_DEMO_ORDERS.md)：独立MySQL合成业务记录、只读工具、服务端演示会话、浏览器入口、检查与未验范围；不执行真实退款或新增模型质量成绩。

当前使用方式已改为[匿名浏览器工作区](ANONYMOUS_WORKSPACE.md)：打开即用，无登录／注册／邀请码／账号菜单。旧身份专题与验收记录保留历史用途，不能再将其当当前使用前提。

当前界面仍为纯聊天／知识库／Agent评测；最新[旅行能力聊天接入](TRAVEL_DIALOGUE_INTEGRATION.md)以38项有限动作复用原控制器，不挂载旅行卡片。媒体／地图／需求编辑仅主动打开轻量窗口；资料覆盖、技术核查及未实现范围见专题。[原三界面交付](THREE_SURFACE_UI.md)和旧成绩保留。

默认只读当前入口；历史证据按任务查阅，不把每轮报告都当开工前置。文档齐全不代表能力已实现，旧“当前／下一步／额度”只在其当时版本成立。

## 先读这几份

1. [根README](../README.md)：定位、现状、入口与启动概览。
2. [PROJECT_CONTEXT](PROJECT_CONTEXT.md)：当前事实、长期目标和未实现边界。
3. [ROADMAP](ROADMAP.md)：依赖及未来方向，不是实施授权。
4. [PROGRESS](PROGRESS.md)：最新状态、本轮检查与停止点。

开发同时遵守[根AGENTS](../AGENTS.md)和[前端局部指令](../frontend/AGENTS.md)。不要用历史摘要替代实际HEAD、代码与工作区核对。

## 当前聊天重构

| 文档 | 什么时候读 |
| --- | --- |
| [ECHOMIND_TRAVEL_DOMAIN](ECHOMIND_TRAVEL_DOMAIN.md) | 最新第11节：固定真实验收46次、4/8通过／4失败，质量未通过；真实准备／有限双读与合成业务分开。第9／10节保留旧阻塞和受控接线，不切默认服务或恢复知识问答 |
| [KNOWLEDGE_NATIONAL](KNOWLEDGE_NATIONAL.md) | 第8节保留相关性候选失败；最新第9节为语义召回三臂／新40题／子问依据及8次建议预算，只有离线实现，实际Embedding0且无授权，不是质量改善，生产仍BM25／问答停用 |
| [AMAP_MCP_SEARCH](AMAP_MCP_SEARCH.md) | 最新高德MCP只读搜索接线、标准协议／持久计量、配置和受控验证；默认REST，真实服务待验，不接路线天气或恢复知识问答 |
| [ECHOMIND_TRAVEL_LIVE_ACCEPTANCE](ECHOMIND_TRAVEL_LIVE_ACCEPTANCE.md) | 原真实3/21失败／18暂停及旧L02计划缺陷保留；最新第8节为零调用终止补修、保存输出重放、回归和面试说明，不是新真实通过或网站切换 |
| [ECHOMIND_TRAVEL_ACCEPTANCE_PLAN](ECHOMIND_TRAVEL_ACCEPTANCE_PLAN.md) | 最新第7节为受控计数钩子／原ASGI＋临时MySQL首发捕获及独立续冻；132相关项通过、真实0、live模式关闭。既定7模型＋7本地／21建议上限不是授权，实际live上下文及payload仍待准入 |
| [ECHOMIND_CHAT_INTEGRATION](ECHOMIND_CHAT_INTEGRATION.md) | 第4步显式接线与受控闭环；最新第6节为零调用原生预初始化／退出补修，保留旧挂起／证据缺口，默认网站未切换，真实语义及生产待验 |
| [ECHOMIND_FULL_MIGRATION](ECHOMIND_FULL_MIGRATION.md) | 完整核心保真与第2步：千问Embedding、隔离Redis／Chroma、安全执行和历史受控验证；本轮原生退出限制见顶部指针 |
| [ECHOMIND_TRAVEL_ADAPTATION](ECHOMIND_TRAVEL_ADAPTATION.md) | 第3步旅行意图／角色／只读及提议工具、规则捕获与16条离线场景准备；历史未接线状态由最新第4步补充，不改旧成绩 |
| [ANONYMOUS_WORKSPACE](ANONYMOUS_WORKSPACE.md) | 当前无登录入口、Cookie隔离、数据保留、额度和验证边界 |
| [TRAVEL_DIALOGUE_INTEGRATION](TRAVEL_DIALOGUE_INTEGRATION.md) | 当前dialogue-v2的38项受限动作、明确确认、原领域控制器、程序事实／缺证说明及本地状态边界；新真实语义待验 |
| [THREE_SURFACE_UI](THREE_SURFACE_UI.md) | 三主导航设计及原dialogue-v1交付记录；顶部最新状态与下方历史范围分开 |
| [ECHOMIND_MIGRATION](ECHOMIND_MIGRATION.md) | 后端分层、有界原生工具回路、任务收据／明确偏好、此前规划面板接入、独立空目标迁移及历史真实复验 |
| [CHAT_REBUILD_PLAN](CHAT_REBUILD_PLAN.md) | 迁移前chat-1～chat-9阶段细则；保留设计来源，不再用旧阶段停止点覆盖本次迁移授权 |
| [CHAT_1](CHAT_1.md) | 原需求卡、聊天持久化／版本、本机旧试用环境，以及当轮语义待验记录 |
| [CHAT_2](CHAT_2.md)／[城市目录](CHAT_CITY_CATALOG.md) | 已复用的明确搜索／POI确认与城市目录边界，及原开发轮检查；不是全国排程准入 |
| [CHAT_ACCEPTANCE](CHAT_ACCEPTANCE.md) | 历史原版1次与修复版8次分别保留，累计9/12仍暂停；截断补修未真实复验，不用新受控结果改判 |
| [HARNESS_ENGINEERING](HARNESS_ENGINEERING.md) | H01～H12原则、当前缺口和交付卡；Agent／架构／评测工作必查 |
| [AGENT_EVOLUTION](AGENT_EVOLUTION.md) | 任务分流、权威状态、恢复、工具／Skill及专业协作的架构决策 |

`/`只用聊天文字展示提议、编号地点与确定性操作结果，复用原排程、地图、费用、预览采用和保存控制器，不恢复旅行面板／执行记录按钮。日期／费用读取当前有效本地草案；通用旅行问题使用固定建议或缺证说明，模型不自由编写事实正文。草案、估算与程序追加消息仍是前端临时状态，服务端提议回执不代表浏览器动作完成或完整规划恢复。

本轮聊天接入真实模型／高德0，受控机制检查不证明真实自然语言质量；新验收须另获授权。此前Echo迁移独立授权模型5/8、高德4物理，首版1失败保留、修复后3个剩余请求通过及一份上海步行存档链；不是当前全部动作同版通过，不改旧9/12。原3104仍为旧schema，用户数据／生产切换未完成。当前入口、实际结果和停止点看PROGRESS，不承诺所有EchoMind模块均已搬入。

## 开发与运维（仍是操作参考，不归档失效）

| 文档 | 内容 |
| --- | --- |
| [LOCAL_DEVELOPMENT](LOCAL_DEVELOPMENT.md) | 本机配置、启动、检查命令及隔离测试边界 |
| [MYSQL_REGISTRATION](MYSQL_REGISTRATION.md) | 历史注册、私有MySQL、额度、原迁移与备份边界；当前无登录使用看ANONYMOUS_WORKSPACE，助手扩展复制看ECHOMIND_MIGRATION |
| [QWEN_SETUP](QWEN_SETUP.md) | 既有千问服务端配置；不是新的调用授权 |
| [RELEASE_1C](RELEASE_1C.md) | 同源转发、上传／取消及可信后端origin |
| [RELEASE_1D](RELEASE_1D.md)／[1E](RELEASE_1E.md) | 身份隔离和实际发送前的持久额度 |
| [RELEASE_1F](RELEASE_1F.md) | Linux发布、权限、数据与HTTPS手册；不得照抄旧状态执行 |
| [DEPENDENCY_SECURITY](DEPENDENCY_SECURITY.md) | 依赖补修与当轮残余告警，不是实时安全审计 |
| [RELEASE_1A](RELEASE_1A.md)／[1B](RELEASE_1B.md) | 真实公交诊断、失败分类及有限场景验收 |

## 旧版旅行能力：按影响面查

这些能力的确定性实现已由聊天内无面板控制器复用；`/legacy`只保留兼容入口，不再出现在聊天主界面。模型并未获得自动采用、保存或任意修改权限。每份报告仍保留其历史样本、失败和未验范围，不替代当前聊天语义验收：

- 城市／日期／快照：[DESTINATION_1](DESTINATION_1.md)、[DESTINATION_2](DESTINATION_2.md)、[DURATION_1](DURATION_1.md)。
- 照片／视频：[MEDIA_1](MEDIA_1.md)、[MEDIA_2A](MEDIA_2A.md)、[MEDIA_2B](MEDIA_2B.md)。照片及选中单帧是线索辅助，不是身份认证或批量识别。
- 原生单次工具查询：[ITINERARY_QUERY_7A1](ITINERARY_QUERY_7A1.md)。一次tool_calls不等于Agent loop。
- 资料检索的旧三馆区基线：[KNOWLEDGE_6D1](KNOWLEDGE_6D1.md)；当前多城扩展见[KNOWLEDGE_NATIONAL](KNOWLEDGE_NATIONAL.md)。旧评分保持，生成问答仍停用。

## 历史、实验与冻结件

完整旧进度、旧架构讨论和旧演示在[历史索引](archive/README.md)，附源提交及哈希清单。知识问答实验、照片冻结标准／样本和工具固定路径继续原位保留，不能为精简改评分或破坏核验。

仓库外`../trippilot-evidence/`保存对应报告指向的脱敏证据；图片、数据库、凭据和浏览器日志不纳入文档提交。当前应做什么只以PROGRESS及本次明确授权为准。
