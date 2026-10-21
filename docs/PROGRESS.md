# TripPilot Progress

## 最新摘要（2026-10-21，修复版同预算续验：未通过并暂停）

- **授权／基线**：实际`main / ea81cdf9f2cef72d381a22bfdd85d361cf1b0a30`。用户答复“下一步吧”，仅继续原12次授权未执行项，不新增额度或chat-3；先补最小显式续验工具，再冻结版本。提示、Schema、模型、参数、题目／预期、前端、业务接口与数据库结构均未在本轮修改；未操作云端或用户库。
- **本轮真实结果**：新增千问 **8次**，原api01的1次不重发、不改判，累计 **9/12**、剩3未用。修复版结构4通过／4失败：api05～08的原生工具选择符合固定预期；api02～04与chrome01均上游HTTP200、应用502 `chat_truncated`、输出1024Token，无可采纳意向。高德仅api05 **1逻辑／1物理**，261ms、20候选；其中本馆／西馆及其他馆、停车场、公交站等不能混称全相关，未确认任何POI。无路线、天气、排程、媒体或行程存档请求。
- **真实Chrome范围**：复用生产BUILD_ID`72tZLvMzFb5UcBEsL7L5x`与原独立测试MySQL，正常普通账号会话；显式本地真实响应桥，不是Mock也不称Next原生rewrite重验。原chrome01空会话Enter仅提交一次，页面失败不应用提议；主动GET读回原话保留、意向空、confirmed空，地点revision0无城市／候选／必去／住宿。400px宽度400且无横向溢出，4图逐张检查；读回补充图有输入区遮挡，不冒称完整卡片展示。完整键盘和成功地点确认链未完成；**chrome02～04因首题前提失败未执行**，不手工seed成功、不再消耗3次。
- **版本／台账**：不可覆盖`continuation-v1.json` hash `ecba95df91b0cd590d17007143bcab692faef322c85059d63fc468e15831738a`，关联原协议、原1259字节台账前缀、原stop与415份源码精确字节；继续同一`calls.jsonl`、registry及上限12，新增行均带续验hash。原api01三行、16份旧台账、五缓存／两原SQLite／身份锚8哈希均匹配；独立MySQL额度只读核对9模型／1高德。新`continuation-stop-v1.json`为`chrome_dependency_failed`；原台账`paused=false`只表示无未决尝试，运行仍因停止收据拒绝发送，不能解释成可以补发。
- **诊断／限制**：修复版合计65208输入＋4263输出＝69471 Token，模型耗时合计46745ms；原api01的6124 Token／5402ms另列。失败只保留安全诊断／hash／用量，未存非法正文或finish_reason，**不能证明完整名称恢复、原正则为唯一根因或真实质量改善**。工具结构通过不是意向通过，安全拒绝也不等于理解正确；已知固定题、每题一次不是泛化或稳定性证据。
- **文件／证据**：仅`backend/tools/chat_acceptance{,_runtime}.py`及两份新`test_chat_acceptance_{continuation,resume_runtime}.py`；追加[CHAT_ACCEPTANCE第9节](CHAT_ACCEPTANCE.md#9-修复版本续验2026-10-21已执行并暂停)，同步README／上下文／路线／聊天专题和导航。新证据追加原`/Users/zijing/projects/trippilot-evidence/chat-acceptance/batch-20261021-v1/continuation-v1/`：新请求／源码、逐项评审、browser-evidence、MySQL读回、保护检查及4图；原结果不覆盖，不提交证据、私密会话、日志、缓存或数据库。
- **实际检查**：发送前相关工具组合51项通过，完整chat发现168、151执行通过／17独立MySQL条件跳过；收尾含environment组合58项通过，重复不相加。保留既有Starlette/httpx弃用／受控ConnectError提示。冻结、链接、敏感信息及`git diff --check`复核；无前端源码／产物变化，不重复完整前端构建。专用8803／3106及自建浏览器上下文已关闭，原服务／页面和未提交手工试用说明保留。
- **停止点**：**完整chat-1／chat-2真实验收未通过，停在续验失败报告。** 剩3不消费，不重试／换模型／调参数，不进入chat-3、loop、部署或生产迁移。下一步只建议另行开展零调用截断诊断与方案评审，再决定修复和冻结；旧记录以下均为各自历史结果，不覆盖本摘要。

当前导航：[验收第9节](CHAT_ACCEPTANCE.md#9-修复版本续验2026-10-21已执行并暂停)看实际结果／失败／取舍／面试；[CHAT_1](CHAT_1.md)和[CHAT_2](CHAT_2.md)看实现；[CHAT_REBUILD_PLAN](CHAT_REBUILD_PLAN.md)只作后续规格，不自动授权下一阶段。

---

## 最新摘要（2026-10-21，chat-1单字名称问题离线补修）

- **范围／基线**：实际`main / 609b85977bd10662bfce0313d57d57cf3a27682d`，用户要求定位并修复首题问题。本轮新增真实模型、高德及其他业务API均 **0**；不恢复旧验收、不重启服务、不操作云端／用户库、不进入chat-3。原手工试用说明、五缓存与浏览器日志均保留不暂存。
- **定位与修复**：原生输出已含单字，非前端截断；输出stop且432Token，未达上限。模型侧三类ShortText裸`\S`按搜索／整串解释有确定差异，离线已复现；仅在`app/services/chat.py`的`provider_schema()`把住宿、必去items、偏好items三处投影为`^[\s\S]*\S[\s\S]*$`，明确允许完整文字并保留非空白要求。**消除了可确定的Schema集成歧义，但没有证实供应商内部根因或真实语义改善。**
- **版本／边界**：新增`PROVIDER_SCHEMA_VERSION=chat-intent-schema-v2-whole-text`；提示正文／PROMPT_VERSION、既有Flash、参数、超时、输出限额、共享Qwen、chat-2工具及全部本地严格校验保持。Selection、`chat-session-v1`、v1-v3存档、MySQL结构和前端均不改；不修补旧答案、不强制最短两字、不全局移除pattern。
- **文件／证据**：服务函数、`tests/test_chat_api.py`新增受控完整名持久往返、`tests/test_chat_provider_schema.py`8项新测试；[CHAT_ACCEPTANCE第8节](CHAT_ACCEPTANCE.md#8-后续离线补修消除shorttext匹配歧义2026-10-21)含原理、取舍、Harness和面试说明，同步当前README／上下文／路线及CHAT_1提示。新离线证据`/Users/zijing/projects/trippilot-evidence/chat-acceptance/schema-fix-20261021/`不提交；原冻结、payload、输出、评分、台账不改。
- **实际验证**：同一冻结api01输入离线重建完整payload，恰好3个pattern路径变化，提示／上下文／城市／模型／显式参数完全一致；Python搜索／全匹配、Pydantic Rust及Node ECMAScript正则覆盖，Node33检查通过。`cd backend && .venv/bin/python -B -m unittest discover -s tests -p 'test_chat*.py' -q`：149项发现、132执行通过、17独立MySQL配置条件跳过；`tests.test_explanation tests.test_adjustment_intent tests.test_itinerary_query`定向34项通过，重复不相加。原Starlette/httpx弃用提示保留。链接／敏感信息、diff及原五缓存／两SQLite／身份锚8哈希和16份旧台账保护复核；无前端变动，不重复整套前端构建或Chrome。
- **停止点**：**离线兼容修复完成，真实效果未验，累计仍1/12。** 旧api01结构通过／语义失败不改判，11项仍未执行；理论剩余最多11次继续暂停，不能把新payload当原api01重发或改旧哈希放行。下一步仅建议另行明确修复版续验清单／原台账关联；本轮不实现通用resume、不发模型、不进入下一阶段或部署。

---

## 最新摘要（2026-10-21，chat-1／chat-2有限真实验收暂停）

- **授权／基线**：实际`main / 6458d2e52810e3503a789cd18c0e827c933f9afd`。用户授权本轮最多12次千问及计划内有界高德；发送前固定8API＋4Chrome题目／预期、完整payload、412项文件和16份旧台账哈希。旧额度不重置，原协议／题目／提示／Schema不改，不进入chat-3。
- **实际结果**：千问 **1/12**，高德 **0逻辑／0物理**。api01 HTTP200、结构合法，但住宿“住”、必去“拙／苏”和碎片偏好语义错误；引用虽含完整名称，也不能证明字段正确。上游请求／返回模型标签为既有Flash快照，5402ms，5692输入＋432输出＝6124 Token。**语义／业务0通过／1失败；其余11项未执行，四个Chrome、400px、真实tool_calls与POI链均未验。** 不用历史结果补成绩，台账传输success原样保留并与语义评分分开。
- **暂停与离线补修**：api02读取登录身份时，验收工具误查`state`，实际契约为`authenticated`；尚未发送第二次模型即停。原`test_session_expired_or_changed`收据保留，但不能据此推断真实会话过期。仅修工具并以真实ASGI身份／CSRF＋临时持久库＋受控模型回归：连续两操作仅登录一次，撤销后模型前停止，不自动重登。
- **诊断边界**：单字已存在于模型原生JSON及应用读回，非页面截断；stop／432Token也不是直接截断证据。三类带裸`pattern: "\\S"`的ShortText与现象相关，provider整串匹配是优先排查假设，**未证实内部根因、未改正式Schema或修补输出**。仅建议下一轮离线评审模型Schema投影，再明确冻结续验，不能宣布质量改善。
- **文件／数据边界**：新增`backend/evaluations/chat12-acceptance-v1.json`、`tools/chat_acceptance{,_environment,_runtime}.py`及对应3份测试；新增[CHAT_ACCEPTANCE](CHAT_ACCEPTANCE.md)，同步当前导航。独立本机随机MySQL／DML测试身份及双层持久计数，原用户库未切换；只改验收工具，无业务／前端／配置／迁移修改。专用8803／3106已关闭，原服务保留。
- **证据与不足**：`/Users/zijing/projects/trippilot-evidence/chat-acceptance/batch-20261021-v1/`含原冻结、请求、台账、合法原始结构投影、应用读回和另存暂停评审。旧runtime字节副本匹配冻结hash；旧runtime测试源码未在编辑前另存、只有hash，不能完整恢复首发测试源码，此缺口保留不补造。修工具后旧verify正确拒绝`frozen_file_changed`；没有改旧hash、删stop或自动续跑。
- **实际检查**：`cd backend && .venv/bin/python -B -m unittest tests.test_chat_acceptance tests.test_chat_acceptance_environment tests.test_chat_acceptance_runtime tests.test_chat_logic tests.test_chat_api tests.test_chat_places_tools tests.test_chat_places_api -q`：**73项通过**；其中3份新工具测试单独39项通过，重复不相加。既有Starlette/httpx弃用提示保留。旧台账16份、五缓存／两SQLite／身份锚8文件哈希未变；链接／敏感信息与`git diff --check`核对。没有前端或业务代码改动，不机械重跑完整构建／浏览器，不伪造截图。
- **停止点**：**验收未通过，停在失败／暂停报告；不进入chat-3，不继续模型／高德。** 原12次中最多11次尚未用，但不是重发api01或变更payload的自动许可；后续须先明确修复及关联原协议／台账的续验方式，不能换批次获得新12次。线上／原手工试用环境未更新，真实地点及新聊天生产备份／恢复仍待办。

### 当前导航与下一步边界

操作计划、逐题未执行项、诊断与面试说明见[CHAT_ACCEPTANCE](CHAT_ACCEPTANCE.md)；已实现流程见[CHAT_1](CHAT_1.md)、[CHAT_2](CHAT_2.md)。当前建议只先离线验证单字问题假设，再明确后续修复／续验；不新增模型、不进入下一阶段、不部署。下方0调用、chat-2离线停止点均为各自历史轮次。

---

## 最新摘要（2026-10-21，chat-2离线实现与通俗文档）

- **授权／基线**：实际`main / c9d7a02365bdd162b5038940640a363858352cfa`。用户明确答复“编写文档并开始离线开发，不调用真实模型”。本轮真实模型及高德业务调用均 **0**；只读核对官方静态城市表，不是业务上游验收。未操作云服务器或切换当前用户库。
- **现在能做什么**：聊天首页按需展开“准备真实地点”，可零模型创建空会话；明确城市后按名称主动搜索，或一次原生工具选择；候选仅预览，明确确认才写入当前账号的地点副本。住宿1个／必去最多6个，服务端从已保存候选取POI，不信客户端Place；不生成、采用、算新费用或保存行程。
- **城市与跨城**：独立`chat-city-catalog-v1`含369个大陆城市／自治州／特殊省直辖单元，官方2026-01-07表于本日核对；精确成员归属、不任意字符串回退。多城原话保留待澄清，不静默只执行一城；错误跨城POI仍拒绝。目录生产条款／更新策略待核，不代表全国排程／天气／v3存档开放。旧六城、1～7天、1住宿及6／3容量不变，成都公交旧失败保留。
- **机制／存储**：有限工具／Skill声明；每次最多1模型＋1逻辑POI搜索，不是loop／MCP／通用插件或任务恢复。账号、对话、意向revision、副本revision、操作ID及批次绑定；发送处持久额度不返还，取消／晚回调隔离。新增`chat-places-storage-v1`两表，仅独立空MySQL目标显式复制，先完整验证旧账号／会话／邀请／额度／v1-v3重放和所有聊天收据；不运行时DDL、不回退SQLite。迁移两次独立快照相等仍依赖停写；提交后发现变化保留目标及失败收据，禁止切换或盲重试。生产完整聊天／地点备份恢复和线上MySQL切换未完成。
- **文件导航**：[CHAT_2](CHAT_2.md)用用户步骤、原理、代码、检查、Harness12维及面试追问讲清本阶段；[CHAT_CITY_CATALOG](CHAT_CITY_CATALOG.md)记来源／限制。实现分为`shared/chat-cities*.json`、`app/chat_cities`／`integrations/chat_places`／`qwen_chat_places`、`schemas/services/api/chat_places`、`chat_places_models/storage`及显式工具；前端`chat-cities`／`chat-places`契约与控制器、地点卡及工作区；补测试／实际Python→TS导出。显式同源白名单与发布验证兼容旧19／20／21路径及新22路径；README、AGENTS、上下文、路线及导航同步，历史和冻结件不改。
- **真实发现并补修**：全国地点不能套旧六城校验；MySQL RR旧快照影响并发CAS；HTTP200失败收据须识别；未知结果读回后不永久锁界面、不重发旧ID；迁移DDL前须验证旧内容而非只验表结构。均补回归，没有删失败边界或放宽旧v1-v3契约。
- **本轮检查**：后端完整789项中727执行通过、62条件跳过；独立13306随机临时MySQL组合43项通过，最终新增源内容／fresh快照／空扩展回退检查后13项再次通过，重复不相加。前端76文件1414项、lint、typecheck、Webpack生产构建通过；Node转发／发布23项，deploy完整111项通过。Python→TS16份实际导出契约在前端测试内；城市官方ZIP离线verify通过。保留既有Starlette/httpx弃用提示，无新依赖。
- **受控页面证据**：生产BUILD_ID`72tZLvMzFb5UcBEsL7L5x`，专用3105及独立Playwright页，所有`/api/*`由明确受控响应截获；预览仍0已确认、Enter确认变1/6、折叠保留、跨城提示和400px无溢出。不是实时模型／高德、真实身份或数据库浏览器验收；MySQL真实持久性单列自动验证。证据`/Users/zijing/projects/trippilot-evidence/chat-2/batch-20261021.KNgiVn/`，7图逐张检查，中间图与可读最终图明确区分；专用服务／页已关闭，原服务／页不动。
- **保护／停止点**：原五缓存、原SQLite两库与身份锚SHA256保持，原手工试用说明及浏览器日志保留且不暂存；旧成绩／协议／台账不改。**停止在chat-2实现及离线／受控检查交付；chat-1新提示真实语义、chat-2真实语义／POI与目录生产使用仍待验，不宣布完整质量通过。** 不进入chat-3、loop、跨城执行、部署或迁现有库；真实调用须另行授权。

### 当前导航与下一步边界

先读README→PROJECT_CONTEXT→ROADMAP→本页；通俗操作、迁移前提与面试说明见[CHAT_2](CHAT_2.md)，全阶段规格仍唯一维护在[CHAT_REBUILD_PLAN](CHAT_REBUILD_PLAN.md)。旧工作台、资料检索和chat-1分别按原专题查，不把下方历史停止点当当前权限。

下一步仅建议先审查全国目录生产用途／更新，并另行冻结chat-1／chat-2有限真实验收及授权；也须补新数据备份恢复和部署准入。它们不是本轮自动执行项。没有当前有效服务端地点扩展的旧本机服务，须按专题另行准备独立副本后才能实际试用，不能因推送代码便声称现有网站已更新。

---

## 最新摘要（2026-10-21，文档精简；不新增功能）

- **基线与范围**：实际`main / edd32d442900306037ea966c0e488d6b2c6c25c4`。本轮只整理文档，未修改业务代码、提示、契约、评测、配置或数据库；不启动／重启服务、不操作服务器、不执行浏览器验收。模型及外部业务API调用均 **0**。
- **当前产品**：`/`为chat-1多轮需求意向、显式确认与账号隔离持久会话；实现、离线及受控流程已交付，**真实模型语义仍待验**，没有旅行工具loop、完整任务恢复或长期偏好。`/legacy`独立保留旧真实规划，未自动接入聊天；`/knowledge`仅资料检索，生成问答停用。chat-2未开始。
- **存储／运行**：当前本机专用试用入口`http://trippilot-test.localhost:3104/`通过独立工具替普通测试账号正常登录；不是关闭鉴权或生产免登录。新版本机使用MySQL，SQLite仅历史兼容，无故障回退；真实模型／高德发送关闭。生产MySQL切换、新聊天完整备份／恢复仍未完成；用户报告的旧版HTTPS／登录与新版验收分开。
- **旅行边界**：旧旅行工具为六城单选、1～7天、单住宿、6必去／本次3可选；空日有效、无自动均分／最优保证。成都完整公交历史`data_error`和各媒体限定／旧失败继续保留，不能由重构或文档精简改判。
- **本轮整理**：缩短README、AGENTS、PROJECT_CONTEXT、ROADMAP及AGENT_EVOLUTION；阶段细则只在CHAT_REBUILD_PLAN维护，Harness12项约定保留。新增总导航、本地开发手册和历史索引；旧完整进度／架构讨论按源提交归档，3份旧演示／交付移入历史目录。前端README替换初始化模板，局部AGENTS／CLAUDE入口不变。
- **历史保护**：归档保留旧正文、评分、失败、台账与当时停止点，仅添加历史标识并调整Markdown链接；附源提交／哈希清单。冻结评测标准、协议、工具固定路径不移动不改写。原有未提交手工试用说明、五缓存、原库／锚及浏览器日志不纳入提交。
- **检查**：836个本地链接与39个锚点无断链；5份归档正文／哈希核对，另38份原Markdown（含冻结件与局部指令）未变。核对当前代码事实／命令、敏感信息、受保护8文件哈希及`git diff --check`；六份核心入口／架构正文从约690KiB降至约43KiB，默认上下文减少约94%，不以总文件数减少为目标。纯文档不重跑业务测试、构建或Chrome，不把历史成绩写成本轮执行。
- **停止点**：停在文档精简。下一步仅建议另行冻结chat-1真实语义验收及取得明确额度；不得自动调用、进入chat-2／loop、部署或迁库。

## 当前文档导航

按[README](../README.md) → [PROJECT_CONTEXT](PROJECT_CONTEXT.md) → [ROADMAP](ROADMAP.md) → 本页恢复事实，再读[文档总导航](README.md)选择专题，不默认加载全部历史：

| 需要了解 | 权威入口 |
| --- | --- |
| 当前chat-1数据流、测试、真实语义缺口、本机入口 | [CHAT_1](CHAT_1.md) |
| chat-1～chat-9阶段与验收／教学 | [CHAT_REBUILD_PLAN](CHAT_REBUILD_PLAN.md) |
| Harness全部12维、现状缺口及交付卡 | [HARNESS_ENGINEERING](HARNESS_ENGINEERING.md) |
| 架构、权威状态、工具／Skill、恢复与协作 | [AGENT_EVOLUTION](AGENT_EVOLUTION.md) |
| 本地启动与配置／迁移／部署 | [LOCAL_DEVELOPMENT](LOCAL_DEVELOPMENT.md)／[MYSQL_REGISTRATION](MYSQL_REGISTRATION.md)／[RELEASE_1F](RELEASE_1F.md) |
| 历史交付、失败、调用台账、旧演示 | [历史索引](archive/README.md)／[完整旧进度](archive/2026-10-21/PROGRESS.md) |

## 后续进度记录方式

当前摘要只记录事实、变化、实际检查、未解决项和停止点；细节在专题维护。旧记录较长时按源提交归档并留索引，不删失败或改评分。历史“下一步”和额度不覆盖当前授权，推送代码不等于更新线上。

本轮Harness适用项为H01范围、H02上下文、H11文档核验、H12工程反馈；未改运行时，H03～H10没有本轮新增验收，不豁免未来门槛。所有12维细则仍在原专题，不因精简消失。

### 简短原理与面试追问

口述：把“现在是什么”“以后怎么做”“过去如何验收”分层。新对话先读短入口，按需查唯一专题；历史归档有源提交与哈希，保留失败，不能把旧停止点或旧模型额度读成现在的授权。本轮改善上下文可恢复性，没有新增Agent能力。

- 为什么不把旧报告全部删除？失败与冻结协议是排障／复现实证，部分路径还被工具绑定；归档比丢失证据更安全。
- 为什么不把所有细则复制进README？重复规格容易漂移；当前事实、阶段规格、Harness和运行手册各有明确入口。
- 怎么保证精简没改历史？归档保留正文／分数，校验源与归档哈希、链接以外的文本一致性，冻结文件维持原路径与内容。
