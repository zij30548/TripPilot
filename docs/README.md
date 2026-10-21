# 文档导航

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
| [CHAT_REBUILD_PLAN](CHAT_REBUILD_PLAN.md) | 唯一chat-1～chat-9阶段细则：范围、依赖、验收、教学与停止点 |
| [CHAT_1](CHAT_1.md) | 当前实现、存储与版本、受控证据、真实语义待验，以及本机普通账号自动登录 |
| [HARNESS_ENGINEERING](HARNESS_ENGINEERING.md) | H01～H12原则、当前缺口和交付卡；Agent／架构／评测工作必查 |
| [AGENT_EVOLUTION](AGENT_EVOLUTION.md) | 任务分流、权威状态、恢复、工具／Skill及专业协作的架构决策 |

`/`目前只做需求意向与确认，真实语义待验；旧旅行工具未自动迁入聊天。下一阶段、真实模型额度与生产切换均需单独授权。

## 开发与运维（仍是操作参考，不归档失效）

| 文档 | 内容 |
| --- | --- |
| [LOCAL_DEVELOPMENT](LOCAL_DEVELOPMENT.md) | 本机配置、启动、检查命令及隔离测试边界 |
| [MYSQL_REGISTRATION](MYSQL_REGISTRATION.md) | 注册、私有MySQL、额度、显式迁移与旧备份边界；聊天增量另看CHAT_1 |
| [QWEN_SETUP](QWEN_SETUP.md) | 既有千问服务端配置；不是新的调用授权 |
| [RELEASE_1C](RELEASE_1C.md) | 同源转发、上传／取消及可信后端origin |
| [RELEASE_1D](RELEASE_1D.md)／[1E](RELEASE_1E.md) | 身份隔离和实际发送前的持久额度 |
| [RELEASE_1F](RELEASE_1F.md) | Linux发布、权限、数据与HTTPS手册；不得照抄旧状态执行 |
| [DEPENDENCY_SECURITY](DEPENDENCY_SECURITY.md) | 依赖补修与当轮残余告警，不是实时安全审计 |
| [RELEASE_1A](RELEASE_1A.md)／[1B](RELEASE_1B.md) | 真实公交诊断、失败分类及有限场景验收 |

## 旧版旅行能力：按影响面查

`/legacy`保留这些能力，不代表聊天已会执行。每份报告仍保留其历史样本、失败和未验范围：

- 城市／日期／快照：[DESTINATION_1](DESTINATION_1.md)、[DESTINATION_2](DESTINATION_2.md)、[DURATION_1](DURATION_1.md)。
- 照片／视频：[MEDIA_1](MEDIA_1.md)、[MEDIA_2A](MEDIA_2A.md)、[MEDIA_2B](MEDIA_2B.md)。照片及选中单帧是线索辅助，不是身份认证或批量识别。
- 原生单次工具查询：[ITINERARY_QUERY_7A1](ITINERARY_QUERY_7A1.md)。一次tool_calls不等于Agent loop。
- 正式开放的资料检索：[KNOWLEDGE_6D1](KNOWLEDGE_6D1.md)。生成问答仍停用。

## 历史、实验与冻结件

完整旧进度、旧架构讨论和旧演示在[历史索引](archive/README.md)，附源提交及哈希清单。知识问答实验、照片冻结标准／样本和工具固定路径继续原位保留，不能为精简改评分或破坏核验。

仓库外`../trippilot-evidence/`保存对应报告指向的脱敏证据；图片、数据库、凭据和浏览器日志不纳入文档提交。当前应做什么只以PROGRESS及本次明确授权为准。
