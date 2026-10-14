# 7A-1 只读行程查询：实现与暂停验收记录

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
