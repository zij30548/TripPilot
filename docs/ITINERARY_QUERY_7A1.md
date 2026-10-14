# 7A-1 只读行程查询：实现、原失败与修复续验记录

## 当前结论：修复版本 Chrome 续验完成（2026-10-15）

本轮基线 `96f485ad36285da8b8fab664c1f95465650b740d`，开始及收口前 fetch 确认 `main` 与 `origin/main` 同步。产品前后端源码未变，只补续验工具、测试及文档。**限定范围的核心功能可收口，停在 7A-1；知识库问答继续停用。**

三组结果必须分开：

| 版本／方式 | 实际结果 | 新模型调用 |
|---|---|---:|
| 原冻结版本 8 个真实 API 场景 | 8 个均为 502/structure；原输出和评分保留，不改判 | 原轮 8 |
| 修复解析器对原 8 份输出离线重放 | 8/8 符合预期；历史修复记录，不是修复版重新真实调用 | 0 |
| 修复版本、本轮原 4 个真实 Chrome 场景 | 4/4 原生调用、规范化工具及本地业务结果通过 | 本轮 4 |

**阶段累计 12/12，剩余 0；不是“12/12 真实验收通过”。** 未重发 api01–api08，也没有探测、自动重试或其他 AI 调用。

### 安全续验与版本冻结

- 原 `protocol.json` SHA256：`4f5c6d0e4087abcba484518f1fa5097f5b8b5454fcf09b971d37cfe949f2118a`。问题、日历、预期、模型请求参数和 payload 哈希全部保持。
- 新增独占创建的 `continuation-20261015.json`，身份 `7a1-index0-chrome-continuation-v1`，SHA256 `b7f359feb7d6cd63eae3e84adeb07fa29e92aa1e897cf78240ad2398c7ca4aac`。关联原协议、旧台账前缀、原证据文件及修复基线；续验源码集合哈希 `49eed07118004c430b813fe49be0a98e11bee82891aa52fce6cf790edb89b676`。冻结后未修改这些代码。
- 继续在原 `calls.jsonl` 追加第 9–12 次，每次附清单身份、哈希与代码版本。原前缀 **9,073 字节** SHA256 `21e33656c75f09cdff4a392945639a699e98bfada9bf75766ba1f12110e294e8`，结束后逐字节哈希一致；原失败文件和全部历史台账哈希仍通过校验。
- 仅允许依序发送 chrome01–chrome04。旧 API 操作、重复 Chrome、新目录、重新冻结、版本变化、不确定尝试或执行异常均阻断发送；没有通用忽略版本开关。锁内二进制追加、发送前预记及 fsync 延续原计数，不创建新额度。
- 离线测试通过后执行 `freeze-continuation`，再以 `serve-continuation` 临时启动本项目后端；前端复用既有 Webpack 生产构建 `jSEaxTg0DUvLnG7_hmcEL`。验收结束恢复普通后端服务，未改正式配置。

### 真实工作区与四项结果

通过正常历史继续规划入口新开工作副本，读取现有记录但不保存、不覆盖。沿用真实 POI 快照：住宿参考点静安寺，必去武康大楼，可选辅德里公园、静安雕塑公园、人民公园-相亲角。原冻结日历 **2026-10-13～15**、09:00–18:00、2 人、预算 ¥4,000 保持；各停留 60 分钟，午餐 12:00–13:00。主动生成一次真实步行草案（200，必去 1/1、可选 3/3、11 条逻辑边），没有用历史结果或 Mock 响应冒充新成功。生成时间 `2026-10-14T16:32:34.803950Z`（上海 10 月 15 日 00:32）。冻结日历前两日已过去，本次估时不代表过去实际旅行或当日营业核验；地点本轮未重查详情。

| 场景／原问题 | 原生 → 规范化工具 → 页面实际核对 | 输入/输出 Token | 秒 | 结果 |
|---|---|---:|---:|---|
| chrome01：请查看第1天的行程安排 | get_day_schedule(2026-10-13)；武康大楼 09:36–10:36、等待 11:27–12:00、午餐 12:00–13:00、辅德里 13:00–14:00、返程 14:28，与正式时间轴一致 | 743/34 | 0.757 | 通过 |
| chrome02：请核对当前全程费用和未知费用 | get_budget_summary；交通 ¥0、用户估算 ¥20、小计 ¥20、8 个未知项、预算减小计 ¥3,980（不是自由余额）；7 次实际移动明细与正式面板一致 | 744/14 | 0.447 | 通过 |
| chrome03：帮我看看那天有哪些活动 | request_clarification(date_ambiguous)；仅提示完整日期／第几天，不擅自读取某日 | 742/29 | 0.856 | 通过 |
| chrome04：我想查看2026-10-14的安排 | get_day_schedule(2026-10-14)；09:00–09:28 前往静安雕塑公园、09:28–10:28 停留、10:56 返回；400px 完整结果可读、页面宽度仍为 400px | 750/34 | 0.805 | 通过 |

本轮输入 **2,979**／输出 **111 Token**；四次上游均回报请求的 Flash 快照标识，不据此认证内部部署。每次为原生单一 `tool_calls`，合法 `index:0` 被接受；后端 `tool_call_id` 与原生 ID 对应，前端读取绑定版本的实际草案/费用，无第二次模型总结。

四次查询窗口各仅观察到一次 `POST /trips/itinerary-query`，没有额外排程、路线、候选、天气、存档写入或其他模型请求。准备阶段的 **1 次排程请求单列**，不能拿本地请求数推算高德上游尝试数。每次查询前后正式时间轴、费用文本/值及共享地图日期选择均不变：即使 chrome04 查询第二日，工作区仍选第一日。此次未初始化真实地图，不宣称重验了地图视野或 Marker。

已取得结果还用于零模型调用检查：编辑问题清除旧结果；费用 20→21→20 清除且不恢复旧结果、不清草案；停留 60→61→60 清除草案和查询、按钮重新禁用，不自动生成；单纯切换展示日期保留查询且无业务请求。Tab 可从输入框到查询按钮。取消中请求、晚 success/error/finally、旧取消回调及更广泛 ABA 以受控测试验证，没有为这些分支追加真实调用。

### 本轮检查、证据和限制

- 后端：`cd backend && .venv/bin/python -B -m unittest tests.test_itinerary_query_acceptance tests.test_itinerary_query tests.test_explanation tests.test_adjustment_intent tests.test_knowledge_disabled -q`：**48 项通过**。覆盖续验上限/旧记录/版本/不确定尝试、原协议边界及旧解读、调整、知识库停用兼容。保留已有 Starlette TestClient 弃用警告。
- 前端针对性受控回归：`cd frontend && npm test -- src/lib/itinerary-query.test.tsx --reporter=dot`：**12 项／1 文件通过**，包括取消与竞态、费用/草案变化、组件本地执行等。
- `git diff --check` 通过。无产品或前端源码变化，未重复完整测试、lint、typecheck、生产构建；下方 442 后端／1,126 前端等仍是历史实施轮记录。
- 稳定证据：[续验清单](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015.json)、[脱敏结果及请求计数](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/receipt.json)，原 `calls.jsonl` 保留原生响应并关联本轮规范化响应和页面结果。无认证头、密钥、完整请求日志或数据库导出。
- **8 张截图均逐张打开检查**：[第一天](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/chrome01-day1.png)、[费用](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/chrome02-budget.png)、[澄清](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/chrome03-clarification.png)、[400px 完整结果](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/chrome04-date400.png)、[400px 视口](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/chrome04-viewport400.png)、[正式第二天时间轴](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/official-day2-400.png)、[正式预算汇总](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/official-budget-400.png)、[规则变化后失效](/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/continuation-20261015/rule-invalidation-400.png)。原两张无草案截图和旧记录未覆盖。
- 自然语言能力仅由这四个固定浏览器问题证明，未证明泛化；空日、公交费用/非法金额、越界、多请求、越权和异常协议等分支有受控覆盖，不能写成修复版本全部真实重验。没有实体手机、全套地图/调整采用/存档回归；本轮无数据库写入。
- 本轮仅提交 **4 文件**：`backend/tools/itinerary_query_acceptance.py`、`backend/tests/test_itinerary_query_acceptance.py`、`docs/ITINERARY_QUERY_7A1.md`、`docs/PROGRESS.md`。五个原缓存内容哈希不变，环境、证据、数据库及未跟踪 `.playwright-mcp/` 均不入库。按授权普通提交并推送，不强推，不追加额度。

演示路径（说明，不再发送验收调用）：有效正式草案 → 明确日期查询 → 本地日程 → 费用与未知项查询 → 不明确日期澄清。模型只选一个受限只读工具，程序核对身份并提供事实；不是自主规划、多轮闭环或多 Agent。

## 2026-10-14：原实现与暂停记录（历史原样保留）

日期：2026-10-14。基线：`16efed8337d6ad54dc8eaa7ca7fe4042efc9e520`；main 开始及提交前 fetch 后与 origin/main 同步。**实现与离线验证完成，真实端到端验收未完成，不标记阶段全部通过。**

## 范围与数据流

正式工作区增加“只读行程查询”，不挂载于 `/demo` 或历史只读详情。现有有效草案（包括合法部分、未安排结果）可查询，没有新正式草案的历史工作副本不能调用模型。

1. 点击时通过现有 `schedule.capture()` 读取同一版本的完整正式草案，再深拷贝当时费用文本；不读取未采用调整。
2. `POST /trips/itinerary-query` 仅发送问题、有序的 1～3 个日期、请求 ID、草案版本和费用版本。不发送地点、坐标、路线几何、费用文本、天气、存档或历史聊天。
3. 后端严格校验请求（禁止额外字段、重复 JSON 键、非法日期/非连续日历、控制字符，问题最多 400 字，接口内容最多 4 KiB；前置流读取边界复用现有 64 KiB/5 秒）。日期声明仍来自客户端，不等于后端认证了一份上游行程。
4. `QwenToolClient` 复用既有受限北京 HTTPS 地址、SecretStr、单次传输和 20 秒整次截止。固定 Flash 快照 `qwen3.7-flash-2026-07-15`，非思考、非流式、最多 1,024 输出 Token；最多一次请求，零自动重试。
5. **实际使用原生 `tools` / `tool_calls`**。仅允许 `get_day_schedule(date)`、`get_budget_summary()`、`request_clarification(reason)`，且只能一项调用。没有工具、多个调用、未知工具/参数、坏 JSON、自由正文、拒绝或截断均不执行。旧 `generate()/parse_content()` 仍拒绝工具调用，旧行程解读/自然语言调整契约不放宽。
6. 服务端绑定并回显请求身份。日期工具须在日历内，并与问题中支持的完整日期或“第 N 天”唯一对应；不推算“明天”等相对日期。结构校验不能证明模型正确理解所有自然语言，多请求/否定等语义仍有误选风险；白名单保证最多只读。
7. 前端再次 exact 校验身份、版本、日期、工具和用量；核对当前 refs 后静态 `switch` 执行本地只读工具。日程读取该日实际时间轴，费用直接调用 `calculateScheduleCosts`，不会遍历所有未使用边另计总价。程序模板输出，不追加模型总结、不动态执行字符串、不改变地图或工作区所选日期。

原生协议依据：[阿里云 Function Calling 文档](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。工具选择使用 `auto`；没有得到唯一工具调用时安全失败，不假设服务保证一定调用。

## 状态与金额边界

- 独立 `AbortController`、单调查询版本、草案版本、费用版本及卸载标识。费用编辑先同步递增版本并取消；草案规则/候选变更、普通生成、采用调整、编辑及卸载复用 `subscribeInvalidation`。
- 请求、旧取消按钮、旧成功/错误/finally 都不能作用于新请求；ABA 不恢复旧身份。保留旧 query 回调也通过当前 ref 捕获，不拼接旧渲染草案与新版本。
- 天气刷新、未采用预览/取消、分区展示等不清查询；费用编辑只清此查询，不误清行程解读、天气检查或调整预览。费用编辑和问题输入本身不发请求。
- 费用结果保留所有已填估算、未知、非法项、人数与全程口径、未安排必去限制、实际交通逐次明细及查询时间。沿用整数分；未知不是零，非法输入不沿用旧小计，预算减已知小计不是自由余额。没有实际游览不作零元预算结论。
- 取消只是停止本地等待，不能保证模型停止处理/计费。20 秒截止包含网络与读取，取消清理可能少量增加返回时间；前端上限 25 秒。
- 知识库 `/knowledge/answer` 继续固定停用。无数据库、密钥、代理、CORS、排程/费用规则或依赖变更。

## 冻结、真实调用与发现的问题

用户此前补充的新增 **12 次**授权有效，不再询问；计划 8 个固定 API 场景＋4 个 Chrome 场景，统一持久计数。历史额度和台账不复用。

稳定目录：`/Users/zijing/projects/trippilot-evidence/7a-1/batch-20261014-v1/`。

- `protocol.json` 在发送前冻结 12 个问题、日期、工具预期、完整请求 payload 哈希、代码哈希及旧 `.jsonl` 台账哈希。提示/模型输入不含题号或答案。
- `calls.jsonl` 每次发送前 flock + fsync 预记；同操作不能重发；未完成尝试、执行异常或版本变化阻断后续发送。不是产品全局额度服务。
- **实际 8/12 次，上游 8 个响应均为原生单一 tool_calls，工具及参数与预期一致，但最终 API 全部 502/structure。** 真实验收失败，不能写成接口成功。
- 原因是本轮解析器错误地要求工具调用项只有 id/type/function。实际千问非流式响应另带合法协议元数据 `index: 0`，导致全部拒绝。
- 按冻结计划，普通结构失败保留并继续预定 8 个接口样本，没有修补、重试或追加探测。定位后暂停后续真实 Chrome 调用；剩余 4 次未使用。
- 后续最小修复仅允许可选元数据 `index` 为整数 0，其他额外字段、非零/布尔/string index 仍拒绝；工具 Schema、模型、提示和参数不改。修复后的代码不是原冻结版本，原冻结文件和失败记录未覆盖。
- 8 份已保存输出的**离线重放 8/8 符合预期**，新增调用 0。这只证明协议兼容修复，不能改判原 8 次失败，也不能替代修复版本真实端到端验收。另补“新目录不得重置本阶段计数”工具检查。

| 场景 | 预期 | 输入/输出 Token | 秒 | 原真实 API |
|---|---|---:|---:|---|
| api01 第一日 | get_day_schedule(10-13) | 741 / 34 | 0.708 | 502，未执行 |
| api02 第二日 | get_day_schedule(10-14) | 742 / 34 | 0.772 | 502，未执行 |
| api03 完整日期 | get_day_schedule(10-15) | 751 / 34 | 0.711 | 502，未执行 |
| api04 费用与缺项 | get_budget_summary | 745 / 14 | 0.463 | 502，未执行 |
| api05 那天 | date_ambiguous | 741 / 29 | 0.704 | 502，未执行 |
| api06 第四天 | date_out_of_range | 741 / 30 | 0.793 | 502，未执行 |
| api07 安排＋费用 | multiple_requests | 746 / 28 | 0.665 | 502，未执行 |
| api08 越权删除保存 | unsupported | 748 / 27 | 0.722 | 502，未执行 |

共输入 5,955／输出 230 Token；8 次上游均报告模型标识与请求的 Flash 快照相同，不据此认证其内部部署。API 样本是**真实千问＋受控日期日历**，不是完整旅行草案浏览器验收。

## 检查与有限 Chrome 证据

- 后端完整：`.venv/bin/python -B -m unittest discover -s tests -q`：最终 **442 项通过**（含协议兼容、计数目录保护与共享旧解析器异常边界回归）。此前相关套件 41 项也通过；最终完整重跑日志为 `/tmp/trippilot-7a1-backend-closeout-final.log`，这份日志不是稳定交付证据，实际结果另记于稳定 receipt。
- 前端完整：`npm test -- --reporter=dot`：55 文件、1,126 项通过。涵盖当前引用捕获、费用/模式 ABA、旧 success/error/finally、旧取消、卸载、费用非法/未知、展示不查询、预览取消保留与采用清除、旧业务回归。
- `npm run lint`、`npm run typecheck`、`npm run build -- --webpack`、`git diff --check` 通过。保留已有 Starlette TestClient 弃用警告；未新增依赖解决它。
- 真实 Chrome 运行本轮 Webpack 构建，读取已有历史记录创建工作副本：观察到一次 `GET /saved-trips/{id}`；没有生成草案、模型/天气/候选/路线查询或存档写入。
- 无有效新草案时按钮禁用，输入与回车没有业务请求；400px 无横向溢出。仅做这些非模型 UI 检查。
- 已逐张打开检查：`01-no-draft-desktop.png`、`02-no-draft-400px.png`。**不是工具成功结果截图。** 没有以受控页面响应伪造真实模型成功。
- 原 localhost 历史标签页刷新曾超时；新开 127.0.0.1 同生产服务页面可操作。没有将旧页灰/空状态当作成功。

### 尚未通过

修复版本：真实日期查询→本地日程结果、费用查询→正式费用一致、澄清结果、有效结果 400px 四个 Chrome 场景均未执行；原 8 个 API 失败仍保留。未宣称实体手机、完整地图/行程回归或自然语言泛化通过。没有启动下一阶段。

## 文件清单（15 个）

1. `backend/app/integrations/qwen.py`：仅抽出共享有界 raw transport，旧解析不放宽。
2. `backend/app/integrations/qwen_tools.py`：专用原生工具提示、payload、严格解析。
3. `backend/app/schemas/itinerary_query.py`：有限请求/响应契约。
4. `backend/app/api/itinerary_query.py`：独立只读查询接口。
5. `backend/main.py`：注册路由。
6. `backend/tests/test_itinerary_query.py`：协议、安全、请求/截止回归。
7. `backend/tests/test_itinerary_query_acceptance.py`：持久计数、恢复、失败及防重置。
8. `backend/tools/itinerary_query_acceptance.py`：固定场景、冻结、共享计数服务。
9. `frontend/src/lib/itinerary-query.ts`：exact 响应与本地白名单执行。
10. `frontend/src/lib/use-itinerary-query.ts`：同步捕获与取消版本。
11. `frontend/src/components/trip/itinerary-query.tsx`：普通文本查询/结果模板。
12. `frontend/src/components/trip/planning-workspace.tsx`：稳定父级及费用失效接入。
13. `frontend/src/lib/itinerary-query.test.tsx`：纯函数、Hook、HTTP、实际组件组合。
14. `docs/ITINERARY_QUERY_7A1.md`：本报告和演示说明。
15. `docs/PROGRESS.md`：当前状态，保留历史。

证据、数据库、环境文件、浏览器日志与原五个缓存不提交。五缓存 SHA256 逐个与任务开始一致。普通提交/推送仅保存本轮实现、修复和真实失败事实，**不表示真实验收已完成**。

## 简短演示与面试解释

后续完成修复版本验收后可演示：主动生成正式草案 → 输入“第二天怎么安排” → 模型选择日期工具、程序读取当天 → 输入“当前费用及缺项” → 程序复用金额核算 → 编辑费用使旧结果立即消失 → 不明确日期提示澄清。

本轮是“模型选一个工具，程序校验并只读执行”的基础，不是自主规划、多轮工具闭环或多 Agent。事实和计算不交给模型；工具输出不送回模型二次总结；模型误选仍可能发生，因此同时限制能力和绑定版本。真实协议失败也说明：受控测试通过不等于供应商协议及页面端到端已经通过，必须保留原失败与修复版本的区别。
