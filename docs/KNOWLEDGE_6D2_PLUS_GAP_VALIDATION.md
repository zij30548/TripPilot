# 6D-2 Plus 缺失事项定向复验报告

2026-10-14；实际基线 `9a989604cec1155ec8ca5d1b9c0563239a1a4619`，main与fetch后origin/main同步。

**结论：7/8通过，但本轮候选未全部通过。原a04第二次仍产生多余的current_applicability，不能认定已稳定修复。** 保留失败并停止，不继续调提示或追加调用；不替换正式问答，不宣布6D-2收口。

## 1. 实际变更和不变边界

独立候选`knowledge-evidence-selection-v3-plus-question-gaps`在旧B-v2系统提示后追加语义限定：missing必须对应用户确实问了但仍无法回答的事项。只比较历史规则不自动产生冲突/今日缺口；若追加今日实际适用判断则保留current_applicability；若要确定的实际做法存在不能消解的不同说法则保留conflicting_materials。行前核实提醒不自动算缺失。

不按题号、整句或关键词判断，不在模型返回后删改missing。旧B候选文件和全部旧输出/评分保持原样。模型仍为`qwen3.7-plus-2026-05-26`；复用原Choice Schema、完整证据组织、服务端请求/馆区/语料/检索绑定及确定性渲染。仅实验版本和绑定元数据独立；摘要、短摘录、引用及状态推导代码委托旧实现，不能据JSON合法证明缺失判断正确。

正式模型/配置仍未改变；正式页面、数据库、密钥、代理、CORS和五个Python缓存不改。不增加依赖或第二评分模型。未做Chrome或正式问答验收。

## 2. 先冻结问题，再冻结候选

[冻结协议](KNOWLEDGE_6D2_PLUS_GAP_PROTOCOL.md)与`backend/knowledge/answer-plus-gap-questions.json`保留逐项预期。

- 在新增候选代码前已独占保存`questions-frozen.json`，包含四题、当时实际检索/完整可见user消息、旧payload、预期、旧证据哈希；SHA256：`ac8a45a486586e3c17774b48b60b6184740d1108228bc1f0eb6d2cb2914bfe56`。
- a04/a05原问题、预期及资料顺序不变。g02只问各自规定；g03在同样比较后明确要求今日适用判定。二者实际资料同样是booking、entry、group，没有删掉干扰单元，没有要求摘要外事实。
- 实现和受控测试完成后冻结8个完整payload、代码、参数、问题、协议与语料；`frozen.json`文件SHA256：`a4b1f05def73ddce6df33fde310ef592e91cfef138518d91056ad9a3d73013c8`，内容fingerprint：`4a0a71feb33c5dfc81ab4d32258f75ad09cea673e87a34fe0b241e9bb7b81722`。
- 两轮均固定a04→g02→g03→a05；第二轮是预定测量，不是失败重试。同题两次payload完全一致，题号、评审答案、预期不发模型。相较前版Plus只改变system提示，不改变Schema、user、模型或显式参数。
- 非思考、非流式、1024输出Token、20秒总截止、默认采样，以及48000/32768/8192字节输入/响应/内容上限不变。

## 3. 逐次结果

评阅者为本轮开发助手，逐条对照冻结预期、原始解析JSON、选中主题及程序投影。没有评分模型，不宣称独立人类盲评。

| 次序 | 题/轮 | 实际选择 | missing / 状态 | 结果 | 输入/输出Token | 耗时秒 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | a04 / 1 | booking、entry | 空 / answered | 通过 | 1426 / 18 | 1.4077 |
| 2 | g02 / 1 | booking、entry | 空 / answered | 通过 | 1440 / 18 | 1.9103 |
| 3 | g03 / 1 | booking、entry | current_applicability / partial | 通过 | 1439 / 22 | 1.3022 |
| 4 | a05 / 1 | fossil、entry、child | conflicting_materials / partial | 通过 | 1603 / 26 | 1.5324 |
| 5 | a04 / 2 | booking、entry | current_applicability / partial | **失败** | 1426 / 22 | 1.1692 |
| 6 | g02 / 2 | booking、entry | 空 / answered | 通过 | 1440 / 18 | 1.0367 |
| 7 | g03 / 2 | booking、entry | current_applicability / partial | 通过 | 1439 / 22 | 1.1982 |
| 8 | a05 / 2 | fossil、entry、child | conflicting_materials / partial | 通过 | 1603 / 26 | 1.6252 |

| 题 | 通过次数 | 核对要点 |
| --- | --- | --- |
| a04 原历史比较 | **1/2** | 第2次比较内容完整但擅增今日适用缺口，状态错误 |
| g02 新历史表述 | **2/2** | 分别保留规则及例外，不把明确不问的今日判断列缺失 |
| g03 比较＋今日适用 | **2/2** | 比较事实保留，明确未答的今日判断保留缺口 |
| a05 真实渠道冲突 | **2/2** | 没有清除真实冲突，保留参加条件和费用边界 |

每次分别评审六项：证据相关8/8、合法事项有内容或对应缺口8/8、条件/来源保留8/8、missing对应真实未答事项**7/8**、状态正确**7/8**、引用/结构有效8/8。整体7/8；语义失败1，结构失败0，执行失败0，未执行0。失败仍计入全部分母及用量。

**失败证据（第5次，原样）：**`{"evidence_ids":["E1","E2"],"missing":["current_applicability"]}`。原a04只问两时期预约要求/停止入场差异；E1/E2已分别提供2025免预约条件及16:00、特展预约购票及15:00。用户没有问今天应采用哪一条，所以通用来源核实提醒不构成缺口，应该answered而非partial。这不是检索漏证据、条件改写或引用错误，而是模型仍扩大了用户所问范围。

g03不是自动修复missing：同样的比较依据加上明确的当前判断，原样保留模型选择的current_applicability。a05则保留现场/官网微信差异、1.0–1.3米与现场测量、提前15分钟、活动不另收费但需入馆票；没有把真冲突改成“全部回答”。入馆票和儿童费用主题相关，但整块包含开放时间等旁支，仍有阅读负担；它不等于更简洁的最终用户答案。

## 4. 用量、限制与建议

- 新授权**8/8**已全部用完，自动重试0、额外探测0、浏览器模型调用0。每次发送前持久预记，操作包含批次/题/模型/重复序号。`calls.jsonl` SHA256：`ceb466356aa7c22334179436e91c34e82691048c34191ad8280d7a57086551fc`。
- 包含失败在内总用量：输入**11,816**、输出**172**Token；用量均取得。平均操作耗时**1.3977秒**，中位**1.3550秒**，范围**1.0367–1.9103秒**。计时包含单次网络、解析、绑定及投影，不是纯推理耗时；没有采集实际账单。
- 本轮8次上游model标识均未有效取得，继续记null/未知；上一批的未知也不补造。请求模型由payload/发送hash证明，不以此认证内部部署版本，不补调元数据。
- 旧20/20、16/16、A/B20/20、B候选16/16、双模型20/20台账与证据全部哈希核对保留。未重算、混入旧成绩或挑选成功样本拼接。
- 四题是同语料、已知问题族定向复验。g02有显式“不判断今天”的限定，更容易帮助模型，不代表自然表述都能正确；原a04第二次失败尤其说明仍对表述/采样敏感。两次不能证明稳定性；不把跨版本小样本计数直接当作统计改善。

**建议：暂不正式采用该候选，缺失事项判断仍未稳定。** 本轮细化体现了可行方向，真实冲突也没有被错误清除，但不能把7/8称为修复通过。若未来另获授权，应先确定是否继续这一表示方式及其可验证边界，再冻结完整复验；本轮不继续调参、不提出自动追加调用、不进行正式集成。6D-2回答质量验收仍未完成。

## 5. 检查与证据

实际相关测试命令（本轮执行，非历史结果）：

```sh
cd backend
.venv/bin/python -B -m unittest tests.test_knowledge_plus_gap_validation tests.test_knowledge_model_selection tests.test_knowledge_b_validation tests.test_knowledge_ab_trial tests.test_knowledge_evidence_trial tests.test_knowledge_acceptance tests.test_knowledge_answer tests.test_knowledge_answer_fix tests.test_knowledge_fix_report -q
git diff --check
```

**78项通过**，其中本轮新增10项受控测试（含子场景）。涵盖仅提示变化、原题预期不变、可见资料/身份绑定、跨版本/ABA、完整投影、非法引用/字段拒绝、语义错误不偷偷修补、8次持久上限/并发防重、重启/孤立预记/fsync失败、结构失败继续及执行异常暂停。不是通过提示字符串断言证明模型语义。使用MockTransport和临时台账，不改用户数据库。既有Starlette/httpx弃用及asyncio测试耗时提示非失败。无前端或生产业务代码修改，不机械重跑前端构建/全部业务测试。

真实调用后离线重放8份输出，核对原JSON、投影、冻结版本、评审和计数；差异检查/敏感信息/五缓存哈希检查完成。正式接口与旧候选、语料和检索不变。

稳定证据：`/Users/zijing/projects/trippilot-evidence/6d-2/plus-gap-validation/`：

- `questions-frozen.json`：提示修改前的题目、完整资料和预期；`protocol.md`、`frozen.json`：候选/代码/参数/8操作冻结。
- `calls.jsonl`：全部发送前预记、原始解析JSON、完整投影、用量和耗时，不含认证头/原始敏感HTTP日志。
- `evaluation-report.json`、`validation-report.md`：逐项评审与本报告副本；`receipt.json`：提交/同步/保护哈希回执。

本轮8个交付文件：

1. `backend/knowledge/answer-plus-gap-questions.json`
2. `backend/knowledge/answer-plus-gap-review.json`
3. `backend/tools/knowledge_plus_gap_candidate.py`
4. `backend/tools/knowledge_plus_gap_validation.py`
5. `backend/tests/test_knowledge_plus_gap_validation.py`
6. `docs/KNOWLEDGE_6D2_PLUS_GAP_PROTOCOL.md`
7. 本报告
8. `docs/PROGRESS.md`

仅上述文件按授权普通commit/push；五缓存与未跟踪浏览器日志不暂存。停在定向复验报告，候选未通过仍如实交付，不推进下一阶段。
