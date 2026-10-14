# 6D-2 双模型选型报告

2026-10-14；实际基线 `4a4e64910b57cd40ff9c626941c74ed193e6c2bd`，main开始与fetch后的origin/main同步。

**选型建议：Plus更值得进入另行授权的完整复验，但当前两者都不满足正式替换或6D-2收口要求。** 同批Flash通过6/10，Plus通过8/10；Plus在a01、a05更一致，却在历史比较a04两次均失败。此结论只针对这五道已知诊断题，每题两次，不证明泛化或长期稳定性。

## 1. 固定设计及实际执行

- 按[冻结协议](KNOWLEDGE_6D2_MODEL_PROTOCOL.md)，固定B候选 `knowledge-evidence-selection-v2-bound-units` 的代码、提示、完整证据单元、输出契约、渲染、语料和检索顺序。只改变请求payload的model字段。非思考、非流式、1024输出Token、默认采样、20秒截止及输入/响应容量不变。
- 请求快照：`qwen3.7-flash-2026-07-15`、`qwen3.7-plus-2026-05-26`。官方文档列出Plus快照、北京结构化支持，JSON Schema文档列出两系列；正式配置/模型白名单不改，只有隔离试验包装器允许这两个请求快照。[Plus说明](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)、[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)（2026-10-14读取）。
- 5题×2模型×预定2轮，共**20/20次真实调用**，重试0、额外探测0、浏览器/评分模型0、b03调用0。第一轮交替起始模型，第二轮逐题反转，按冻结20个操作顺序全部执行。第二轮不是根据首轮失败决定的重试。没有权限/参数支持/超时等执行中断，没有边跑边改。
- 新独立台账发送前预记、flock/flush/fsync，唯一ID包含批次/题/快照/重复序号，重启不能复发。旧四批20/20、16/16、20/20、16/16全部保留并关联哈希；不是重置旧额度。
- 冻结fingerprint `31b932949aae195b55f57262e2f58088a0e4c9fa3938c35d16ae8b5719cb7203`；frozen.json文件SHA256 `e895de8db4d13844dba32957d1d7fca6be8285f3a22c16ea7bd01e468b9167ce`。发送后复核同题四份业务payload（排除model）完全相同，候选/语料/协议/工具哈希未变。
- 台账SHA256 `6ace27ff4d8c9634adfea04c07cc2a12fe27b231bec10f1393d0cf77f25e6e6b`，包含20条预记和20条实际结果，无修补输出。
- **模型标识限制：**20次被动观测均未取得可记录的上游model字段，记录null。现有保存信息不能判定上游缺字段还是被动观测未提取（例如响应编码影响）；不能写成“上游肯定未返回”。请求字段由冻结payload和实际发送hash确认，但不认证内部部署版本，也不为补元数据追加调用。

## 2. b03 勘误，不追溯改分

[单独勘误](KNOWLEDGE_6D2_B03_ERRATUM.md)将后续预期限定为模型可见的“提前7天（不含当天）”，不再额外要求“至少”，也不反推只能恰好第7天。原题文件、旧输出、旧评分和待核记录不改，上一批新题仍是3通过/2失败/1待核。b03不属于本轮五题，未发送也未重新评审。

## 3. 同批逐题结果

评阅者为参与实现的开发助手，逐条对照冻结期望、实际输入和确定性展示；没有另一个评分模型，不宣称独立人类盲评。结构合法、文字未改写和业务正确分别核对。

| 题目 | Flash第1/2轮 | Flash通过 | Plus第1/2轮 | Plus通过 |
| --- | --- | --- | --- | --- |
| a01 借车及馆外限制 | 失败／失败 | 0/2 | 通过／通过 | 2/2 |
| a03 人广数字馆专项无依据 | 通过／通过 | 2/2 | 通过／通过 | 2/2 |
| a04 2025/2026历史语境比较 | 失败／通过 | 1/2 | 失败／失败 | 0/2 |
| a05 真正的渠道冲突 | 失败／通过 | 1/2 | 通过／通过 | 2/2 |
| a06 纯实时名额缺口 | 通过／通过 | 2/2 | 通过／通过 | 2/2 |

### 六次失败全部保留

| 操作 | 模型/轮次/题 | 实际输出问题 |
| --- | --- | --- |
| 1 | Flash r1 a01 | family已完整支持押金或证件、仅馆内，却添加not_in_materials，错误partial |
| 12 | Flash r2 a01 | 同样多余not_in_materials；没有真实未答事项 |
| 5 | Flash r1 a04 | booking＋entry选对且条件完整，却将可说明的历史语境差异标conflicting_materials/partial |
| 6 | Plus r1 a04 | 同样误标历史差异为冲突，题目没有要求裁决现行规则 |
| 15 | Plus r2 a04 | 仍正确选两来源，但改成current_applicability/partial；题目也未询问今天是否有效 |
| 8 | Flash r1 a05 | 正文完整保留真实渠道冲突，却用current_applicability代替conflicting_materials；未准确表达缺口类别 |

a03四次均没有无关特展/团队资料填充，a06四次均只返回live_availability而不选静态规则。这是本批观察，不能抹去旧批的失败。a05的入馆票/儿童条件是相关费用背景，但部分回答还展示开放时间等旁支，有阅读成本；没有仅因整块资料存在就自动判完整。

### 汇总与维度

| 指标 | Flash | Plus |
| --- | --- | --- |
| 计划／实际执行／未执行 | 10／10／0 | 10／10／0 |
| 整体通过／失败 | 6／4 | 8／2 |
| 结构/引用入口合法 | 10/10 | 10/10 |
| 所选证据直接相关 | 10/10 | 10/10 |
| 全部合法事项有内容或明确缺口 | 10/10 | 10/10 |
| 日期、数字、条件/例外/人群保留 | 10/10 | 10/10 |
| 状态与missing正确 | 6/10 | 8/10 |

完整摘要有需要的信息，但额外missing或错误missing仍算失败：本批差异集中在“哪些事项没有回答，以及属于哪种缺口”的判断。程序原样投影保住了文字，不证明该判断正确。没有通过删除失败或合并旧版本成功得到上述数字。

按题及重复轮次配对的10组中：两模型均通过5组、仅Plus通过3组、仅Flash通过1组、均失败1组。结构失败0、执行失败0；若有失败用量也会纳入统计，而不是只计成功。

## 4. 用量、耗时与取舍

| 指标（包含全部调用） | Flash，10次 | Plus，10次 |
| --- | --- | --- |
| 输入Token | 11,304 | 11,304 |
| 输出Token | 244 | 182 |
| 操作平均耗时 | 0.8330秒 | 1.2878秒 |
| 操作中位耗时 | 0.8023秒 | 1.2598秒 |
| 最小–最大 | 0.6284–1.3223秒 | 1.0542–1.5439秒 |

所有20次用量已取得，合计输入22,608、输出426 Token。计时包含同一次网络调用、解析、绑定和投影，不含CLI启动、冻结重算及最终落盘，不是纯模型推理时长。本批Plus平均约慢55%，这不是长期性能保证；默认采样、网络及提供方缓存等未做因果控制。未采集账单，不能把较少输出Token直接当成实际花费更低。

**推荐下一步方向为Plus完整复验，而非直接上线。** 理由是同批a01/a05均2/2，整体8/10；代价是本批更高延迟及仍需验证的实际价格/稳定性。尤其Plus在a04是0/2，不能被总分掩盖；Flash a04虽1/2也不稳定。若决定的是“现在是否替换正式问答”，答案是**暂缓，两者都未达质量门槛**。

未来若获得新授权，应先冻结完整题集与一致标准，涵盖历史比较、真冲突、实时/静态混合、条件/例外、未知和越权等；保留重复测量与未见表达，确认上游元数据观测和页面状态隔离。这里仅列尚需验证事项，本轮不改提示、不扩大题集、不另调模型、不做浏览器验收。旧成绩只作背景，不混入本批；这五题已知且仅两次，不能声称泛化或完全稳定。

## 5. 检查、证据与交付

实际命令：

```sh
cd backend
.venv/bin/python -B -m unittest tests.test_knowledge_model_selection tests.test_knowledge_b_validation tests.test_knowledge_ab_trial tests.test_knowledge_evidence_trial tests.test_knowledge_acceptance tests.test_knowledge_answer tests.test_knowledge_answer_fix tests.test_knowledge_fix_report -q
.venv/bin/python -B -m tools.knowledge_model_selection init
# next已按冻结顺序执行20次，额度用尽；不是继续调用的指令。
.venv/bin/python -B -m tools.knowledge_model_selection status
git diff --check
```

**68项相关测试通过**（新增10项，含多子场景），差异格式检查通过。测试覆盖固定顺序、仅model变化、原配置白名单未放宽、20次持久上限、并发防重、重启/孤立预记、失败不修复、执行暂停、绑定/版本ABA、响应容量/重定向/思考输出限制、失败用量与安全元数据。仅MockTransport/临时目录，不读写业务数据库。原Starlette/httpx弃用警告和测试事件循环耗时提示保留。不改前端，未重复前端全套构建或后端全业务测试。

另离线核对20份实际输出、请求hash、人工标注与重新投影一致；冻结候选及历史47＋42＋6＋6份证据、四份台账与五缓存哈希均不变。正式问答/配置、数据库、密钥、代理及CORS未改。

稳定目录：`/Users/zijing/projects/trippilot-evidence/6d-2/model-selection/`。

- `protocol.md`、`frozen.json`：冻结协议、20个计划操作/完整payload/逐项期望及版本哈希。
- `calls.jsonl`：发送前预记、原始解析JSON、完整程序投影、请求模型/重复序号、用量和耗时；不含认证头或敏感原始网络日志。
- `evaluation-report.json`：20项评审、全部结果关联及两模型汇总；未执行列表为空。
- `selection-report.md`、`receipt.json`：稳定报告副本及提交同步/证据/保护哈希回执。
- 本轮不生成Chrome截图，不能拿历史截图冒充本轮真实模型页面验收。

本轮7个交付文件：`backend/tools/knowledge_model_selection.py`、`backend/tests/test_knowledge_model_selection.py`、`backend/knowledge/answer-model-selection-review.json`、`docs/KNOWLEDGE_6D2_B03_ERRATUM.md`、`docs/KNOWLEDGE_6D2_MODEL_PROTOCOL.md`、本报告、`docs/PROGRESS.md`。仅按授权普通commit/push这些文件，缓存、浏览器日志、环境、数据库与仓库外证据不提交。

停在模型选型报告：**推荐方向不是正式采用；6D-2回答质量验收仍未完成。**
