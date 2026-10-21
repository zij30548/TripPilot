# 历史文档索引

这里保存过去的交付与架构语境，不是当前功能、操作顺序或调用授权。当前入口：[README](../../README.md) → [PROJECT_CONTEXT](../PROJECT_CONTEXT.md) → [ROADMAP](../ROADMAP.md) → [PROGRESS](../PROGRESS.md)。

## 2026-10-21文档精简

归档源为提交`edd32d442900306037ea966c0e488d6b2c6c25c4`。没有重写旧失败、评分、台账或当时停止点；只增加历史标识并修正移动后的Markdown链接目标。源文件、正文及归档SHA256见[迁移清单](2026-10-21/manifest.json)。工作区原有的未提交手工试用说明不混入归档或本轮提交。

| 原路径 | 历史副本／新位置 | 处理方式 |
| --- | --- | --- |
| `docs/PROGRESS.md` | [完整旧进度](2026-10-21/PROGRESS.md) | 保留全部已提交历史；原路径改为最新摘要与导航 |
| `docs/AGENT_EVOLUTION.md` | [完整旧架构讨论](2026-10-21/AGENT_EVOLUTION.md) | 原路径保留当前决策；阶段细则统一指向CHAT_REBUILD_PLAN |
| `docs/DEMO_6C1.md` | [旧旅行演示](legacy/DEMO_6C1.md) | 移动，旧页面和旧版本语境不冒充当前聊天流程 |
| `docs/DEMO_6D2.md` | [旧知识问答演示](legacy/DEMO_6D2.md) | 移动，问答现在停用，不按旧演示调用模型 |
| `docs/DELIVERY_6D2.md` | [旧知识问答交付](legacy/DELIVERY_6D2.md) | 移动，保留未达质量准入与旧版本结果 |

根README／AGENTS／PROJECT_CONTEXT／ROADMAP的旧版本仍可按源提交从Git读取；不为每次修改再建重复总报告。原代码、数据、评测工具与仓库外证据未删除。

## 原位保留的研究记录

不是所有旧Markdown都能随意移动。以下文件被工具、固定协议或历史引用使用，继续按原路径保留，**不代表正式开放或允许重新调用**：

- [知识问答实现记录](../KNOWLEDGE_6D2.md)、[补修](../KNOWLEDGE_6D2_FIX.md)、[诊断](../KNOWLEDGE_6D2_DIAGNOSIS.md)。
- A/B：[冻结协议](../KNOWLEDGE_6D2_AB_PROTOCOL.md)、[报告](../KNOWLEDGE_6D2_AB_REPORT.md)。
- B候选：[冻结协议](../KNOWLEDGE_6D2_B_PROTOCOL.md)、[复验](../KNOWLEDGE_6D2_B_VALIDATION.md)、[b03勘误](../KNOWLEDGE_6D2_B03_ERRATUM.md)。
- 双模型：[冻结协议](../KNOWLEDGE_6D2_MODEL_PROTOCOL.md)、[报告](../KNOWLEDGE_6D2_MODEL_SELECTION.md)。
- Plus定向：[冻结协议](../KNOWLEDGE_6D2_PLUS_GAP_PROTOCOL.md)、[报告](../KNOWLEDGE_6D2_PLUS_GAP_VALIDATION.md)。
- 完整回归：[冻结协议](../KNOWLEDGE_6D2_PLUS_FULL_PROTOCOL.md)、[报告](../KNOWLEDGE_6D2_PLUS_FULL_REGRESSION.md)。
- 照片：[冻结标准](../MEDIA_1_EVALUATION_STANDARD.md)、[固定复验报告](../MEDIA_1_FIXED_VALIDATION.md)。旧两轮与固定8图的评分、台账含义独立保留。

原位专题能力／运维手册见[总导航](../README.md)。文件名较早不等于无用；当前事实与历史证据分开维护，比删掉失败记录更利于排障和面试说明。

## 后续整理规则

新交付只在当前PROGRESS写摘要，细节放对应专题，避免同一规格复制到四五个入口。较长历史可按批次追加不可覆盖归档，并记录源提交／哈希、重定向链接；不得更新冻结件迁就现状、改旧成绩或把历史额度当新预算。文档整理不授权业务改动或部署。
