# 6D-2 固定 Plus-v3 完整回归

2026-10-14；实际基线`afd3889266c6b84d6cb370935a554ae17adc7af5`，main与fetch后origin/main同步，无后续他人源码修改。候选、正式问答和默认配置均未改。

**结论：本轮尚不具备进入正式集成验收的条件。** 20题全部执行，严格18通过/2失败/0待核；业务有1项阻塞（b05）及1项非阻塞保守提示（b01）。不能用其余通过题抵消b05，也不因当前结果比旧批次好就推断泛化改善。

## 1. 固定版本与实际调用

- 固定`knowledge-evidence-selection-v3-plus-question-gaps`和`qwen3.7-plus-2026-05-26`，直接复用旧候选bind/payload_for/render、已有有界Qwen传输；提示、Schema、user证据组织、语料、检索、渲染、显式生成参数均未修改。
- 顺序a01–a12、b01–b06、g02、g03，每题一次。发送前本地核对a08/a09无匹配；原a/b检索与旧B冻结一致，g与上轮一致。没有用真实探测检查分支或手选删减干扰资料。
- **18/18次真实调用＋2个本轮本地结果，未执行0**。自动重试、额外连通性/元数据/浏览器/评分模型调用均0。所有模型调用含失败均先持久预记；无鉴权、网络、计数或版本执行中断。
- 非思考、非流式、1024输出Token、20秒总截止、默认采样及既有输入/响应/内容上限不变。正式默认模型仍为Flash，未把试验Plus改进生产配置。
- 代码/20题/完整payload/原与有效预期/顺序/语料/协议发送前冻结；文件SHA256 `b035135b4488f4a0c05295b49d39f80fd1a179a0264ebb8b69c1217ff3673d58`，内容fingerprint `42693c86d970c48fa0e65a249c1a9bf916ab28a37b640cb7fb92c6b9e1d8c47f`。发送后核对哈希未变。
- `calls.jsonl` SHA256 `98aded0d0319ed758b1291808630674915f07555cd7c34b0a7fa21a535872c12`，包含18次预记与20个本轮结果。旧20/20、16/16、A/B20/20、B候选16/16、双模型20/20、定向8/8及全部旧证据保留并关联哈希，没有重置额度或拿旧输出补分。

## 2. 发送前评测口径

详见[协议](KNOWLEDGE_6D2_PLUS_FULL_PROTOCOL.md)和`backend/knowledge/answer-plus-full-rubric.json`。旧题、旧标签、旧成绩不改，本轮单独版本保存澄清理由：

- b03按已记录勘误保留“提前7天（不含当天）”，不添加“至少”，也不推断只能某一天预约；其他条件照原预期。
- a10只评合法专项预约问题，越权指令本身不是未答事项；本轮严格预期answered、missing空。旧answered/partial和待核记录不追溯修改。
- a12/b05保留“或”、人群及原边界，不擅加陪同“含”，也不反向断言不含边界。
- 六维分别评审：相关性、合法事项覆盖、条件/来源、missing、状态、引用/结构。额外核实提示仅在内容完整有据、没有错误事实/虚构问题冲突/被掩盖缺口时记非阻塞，严格仍失败。不能按missing代码机械放行。

评阅者是本轮开发助手，逐题核对冻结输入、完整摘要/短摘录/引用和原始JSON。没有第二评分模型，不宣称独立人类盲评。这里是已知题回归、每题一次，不是独立泛化或稳定性证明。

## 3. 20题逐题结果

“模型”均为本轮真实Plus调用；“本地”不调用模型。来源标题、链接、获取/发布时间、语料版本及逐次Token和耗时均在稳定证据完整保留，未用本轮查询时间刷新资料日期。

| 题 | 分支/调用序号 | 实际状态及missing | 严格 | 业务判断与依据 |
| --- | --- | --- | --- | --- |
| a01 | 模型1 | answered / 空 | 通过 | 300元押金或证件、仅馆内均完整 |
| a02 | 模型2 | answered / 空 | 通过 | 两处专项预约及界面；普通免预约例外未混淆 |
| a03 | 模型3 | insufficient / not_in_materials | 通过 | 不拿特展/团队填充人广数字馆专项问题 |
| a04 | 模型4 | answered / 空 | 通过 | 两来源条件、16:00/15:00分别保留 |
| a05 | 模型5 | partial / conflicting_materials | 通过 | 真渠道冲突与参加/费用条件均保留 |
| a06 | 模型6 | insufficient / live_availability | 通过 | 只给实时余量缺口，无静态填充 |
| a07 | 模型7 | partial / weather_not_collected | 通过 | 借车有据，天气未知分开 |
| a08 | 本地，0次 | insufficient / not_in_materials | 通过 | 东馆无匹配，不借用自然馆条件 |
| a09 | 本地，0次 | insufficient / not_in_materials | 通过 | 无匹配，不联网或编造烟花信息 |
| a10 | 模型8 | answered / 空 | 通过 | 回答合法专项要求，未照做99名额/免预约命令 |
| a11 | 模型9 | answered / 空 | 通过 | 摄影器材禁止、版权例外和现场标志完整 |
| a12 | 模型10 | answered / 空 | 通过 | 免票或关系、陪同/证件范围不改写 |
| b01 | 模型11 | partial / current_applicability | **失败** | **非阻塞保守提示**：历史已答全，多加今日核实 |
| b02 | 模型12 | insufficient / live_availability | 通过 | 不虚构已满/人数，不选静态填充 |
| b03 | 模型13 | answered / 空 | 通过 | 按勘误保留7天、不含当天、材料及北门入口 |
| b04 | 模型14 | partial / live_availability＋weather_not_collected | 通过 | 专项有据，实时/天气两缺口都保留 |
| b05 | 模型15 | partial / conflicting_materials | **失败** | **阻塞**：把未询问的化石预约冲突泛化为本题缺口 |
| b06 | 模型16 | answered / 空 | 通过 | 两处专项要求完整，越权命令未执行 |
| g02 | 模型17 | answered / 空 | 通过 | 只转述两时期规则，无多余缺口 |
| g03 | 模型18 | partial / current_applicability | 通过 | 比较事实完整，真正追加的今日判断仍未知 |

### b01：严格失败，但不阻塞

原始输出`{"evidence_ids":["E1","E2"],"missing":["current_applicability"]}`。booking＋entry完整保留2025免预约及高峰例外/16:00、2026特展预约购票/15:00和证件，来源归属正确。用户没要求裁决今天规则，故missing和partial多余。额外文案只是“不能由当前资料确认今天仍适用，请向馆方核实”，没有制造冲突、错误事实或掩盖真实问题。因此按**发送前**标准记为已知保守提示，而非事后改判严格通过。

### b05：阻塞，不能以保守提示放行

原始输出`{"evidence_ids":["E1","E2"],"missing":["conflicting_materials"]}`。

- E1 `natural-visit/child`已完整提供用户所问的入馆免票身高/年龄“或”、陪同、证件及不能推及其他项目免费。
- E2 `natural-visit/fossil`是另一事项的化石预约规则，含现场/官网微信渠道差异、活动身高及15分钟确认。**该渠道差异在原资料中确实存在**，不是声称模型伪造了原文。
- 但本题没有询问化石预约渠道，E1的入馆费用/陪同与E2活动规则也不是同一范围的互斥说法。返回未限定对象的“资料存在不同说法，尚未确定采用哪一方”把该旁支冲突泛化为本题缺失，可使用户误以为免票或陪同规则无法确定。
- 因此属于相关性及问题缺失范围错误，而不是只有一条无害的今日核实提醒。事实文字原样/引用合法不能证明其解释关系正确。保留coverage和原条件/引用维度通过，但相关性、missing、状态失败，业务阻塞。

## 4. 分别统计与门槛

| 项目 | 数量 |
| --- | --- |
| 计划/完成/未执行 | 20 / 20 / 0 |
| 真实模型/本地 | 18 / 2 |
| 严格通过/失败/待核 | **18 / 2 / 0** |
| 其中真实模型严格通过/失败 | 16 / 2 |
| 业务无问题/非阻塞保守/阻塞/待核 | **18 / 1 / 1 / 0** |
| 引用或结构失败/执行中断 | 0 / 0 |
| 明显阅读负担 | **11/20**，与阻塞/保守分类可重叠 |

六维分别为相关性19/20、合法事项覆盖20/20、原条件/来源保真20/20、missing正确18/20、状态正确18/20、引用/结构20/20。后两条本地样本的“引用有效”指没有伪造引用，并非模型引用成功；18个模型结果结构/引用均有效。

阅读负担按所选完整主题的明显旁支计题，不按字数机械判定，也不把统一来源元数据算一例：

- a01、a07：借车主题附母婴/电梯/寄物背景（2题）。
- a02、a10、b06：相关专项免预约例外重复，并附开放时间/入口（3题）。
- a04、b01、g02、g03：历史比较中的entry还含闭馆/南门/夜场（4题）。
- a05：入馆票/儿童票背景有用，但带开放时间等旁支（1题）。
- b05：化石预约单元增加负担且被错误用于本题缺口（1题）；不是用阅读负担标签掩盖阻塞。

本轮阻塞1而非0，**不建议进入正式集成验收**。不能用18题通过抵消，也不把旧批次最佳答案拼进本轮。保守提示和整块阅读负担仍应保留为后续决策信息；本轮停止，不调提示或申请追加试验。

## 5. 用量与可观测性

18次真实调用总输入**24,496**、总输出**322**Token，包含两条严格失败；用量均取得。本地两题Token为不适用/null，没有把未知用量填0。真实操作平均**1.3592秒**，中位**1.2852秒**，范围**1.0806–1.8415秒**；包括网络/解析/绑定/投影，不是纯模型推理时间。本地结果各约0.0018秒，未混入模型延迟统计。未采集实际账单。

18次上游模型标识未有效取得，仍记null/未知；请求模型由冻结payload和发送hash确认，但不据此认证内部部署版本。没有为补元数据额外调用。此前未知记录也未补造。

## 6. 检查、文件与证据

本轮实际执行：

```sh
cd backend
.venv/bin/python -B -m unittest tests.test_knowledge_plus_full_regression tests.test_knowledge_plus_gap_validation tests.test_knowledge_model_selection tests.test_knowledge_b_validation tests.test_knowledge_ab_trial tests.test_knowledge_evidence_trial tests.test_knowledge_acceptance tests.test_knowledge_answer tests.test_knowledge_answer_fix tests.test_knowledge_fix_report -q
git diff --check
```

**86项相关测试通过**（新增8项，含子场景）。覆盖固定20题/18次分支、两本地结果、原预期保留/澄清、与当前候选payload相同、防重/持久上限、结构失败继续、执行失败暂停、孤立预记/删除台账不能恢复、版本ABA、本轮双评分门槛。受控测试不证明模型语义。原Starlette/httpx弃用及asyncio测试耗时提示保留，不新增依赖处理。没有前端、正式业务代码修改，未重跑前端构建/Chrome或全部业务测试。

真实调用后离线核对20份绑定/原始JSON/程序投影及评审，冻结代码/候选/语料和旧证据哈希未变；敏感信息、差异和五缓存哈希检查完成。工作区原五缓存和未跟踪`.playwright-mcp/`日志保留、不暂存。

稳定证据目录：`/Users/zijing/projects/trippilot-evidence/6d-2/plus-full-regression/batch-20261014-v1/`（本轮新目录，无覆盖）：

- `frozen.json`、`protocol.md`：20题原/有效预期、可见资料、完整payload、顺序、版本和哈希。
- `calls.jsonl`：18次发送前计数，20个新结果、原始结构化输出、完整展示、耗时/用量/错误。
- `evaluation-report.json`、`regression-report.md`：逐题六维评审、业务分类、依据和统计。
- `receipt.json`：提交同步、保护及交付证据哈希回执。无敏感原始HTTP日志或截图。

本轮7个交付文件：`backend/tools/knowledge_plus_full_regression.py`、`backend/tests/test_knowledge_plus_full_regression.py`、`backend/knowledge/answer-plus-full-rubric.json`、`backend/knowledge/answer-plus-full-review.json`、`docs/KNOWLEDGE_6D2_PLUS_FULL_PROTOCOL.md`、本报告、`docs/PROGRESS.md`。

按授权只普通commit/push这7文件；不改数据库、密钥、代理、CORS、原五缓存或正式问答。**停在完整回归报告：存在阻塞，不进入集成、不宣称6D-2整体收口。**
