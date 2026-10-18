# release-1E：业务调用限额与费用保护

2026-10-18；基线 `24bcf7e47ca99790217e65faf2a1d3b8c565c901`，`main`，开工 fetch 与远端同步。仅限受控邀请内测，不是公网安全准入。真实模型／高德／其他外部业务调用 **0**；旧模型授权与台账不变。

## 场景、方案与取舍

release-1D 的身份与存档隔离不能限制已登录用户反复发送业务请求。此次在实际 HTTP 发送前及媒体 CPU 工作前取得持久额度，不依靠按钮、单请求路线预算或 busy。

首发为**同一主机、同一磁盘、低并发邀请内测**，选择独立 SQLite 事务台账；没有新增依赖、Redis 或数据库服务。`BEGIN IMMEDIATE` 将账号／全局检查和插入置于同一事务，线程和独立进程共用额度；不依赖内存短期计数。SQLite 单写者且可因锁等待失败，3秒 busy timeout 后安全503，不适合直接当多主机共享文件系统服务。[SQLite事务说明](https://www.sqlite.org/lang_transaction.html)。

Redis 更适合未来多主机共享高频窗口，但需要独立服务、认证、可用性、原子多条件脚本及持久化／故障恢复运维；只用易失 INCR/EXPIRE 不能承载硬额度。RDB/AOF各有恢复窗口与代价，不能把重启恢复当作免费额度。[Redis持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)。本轮未接 Redis；两进程测试验证的是同一 SQLite 文件，不是分布式多主机验收。

## 完整发送盘点与单位

| 服务类别 | 实际发送／处理位置 | 入口及单位 |
| --- | --- | --- |
| `amap_poi` | `integrations/amap.py` GET `/v5/place/text` | 普通地点搜索、候选聚合的每个关键词；连接重试各1次 |
| `amap_route` | `amap_walking.py`／`amap_transit.py` | 单段路线、正式排程、调整预览的每个有向查询尝试；连接重试各1次；同点本地零移动不计 |
| `amap_weather` | `amap_weather.py` | 整批天气每次 HTTP 尝试，不按旅行日期重复计；连接重试另计 |
| `amap_proxy` | `amap_proxy.py` stream | 固定行政区代理每次发送；原路径、参数、来源、Key检查保留 |
| `qwen_text` | `qwen.py` stream，原生工具子类复用 | 行程解读、受限自然语言调整、只读查询每次模型尝试；无重试或第二次总结 |
| `qwen_vision` | `qwen_vision.py` stream | 单照片／选中单帧的每次模型尝试；不发送整段视频，无重试 |
| `media_photo` | `api/photo.py` 解码前 | 有界上传后的1个解码任务；损坏图片的处理尝试也占额，与模型次数分开 |
| `media_video` | `api/video.py` 临时处理前 | 1个探测＋整批抽帧任务，不按帧数计；原子进程退出前不释放 busy |

三个生产客户端工厂统一 `MeteredAsyncClient`，在公开 `send()` 边界取得额度，保留 HTTPX 原有 TLS／环境代理和禁重定向行为。固定 URL／方法及服务端请求上下文分类，拒绝未知目标；没有新重试层。候选／排程聚合显式传播额度错误，取消并清理同批其他任务，不伪装为高德 `data_error` 或正常“无路线”。单请求41逻辑边／29启动／58尝试及原截止、媒体限制不变；账号额度不足可能提前结束本次生成，不虚构部分成功。

`/demo` 的真实查询也经过同一客户端；Mock计划、资料本地检索、停用问答、保存及只读历史不消耗业务额度。`POST /knowledge/answer` 仍为 `knowledge_answer_disabled`，不会转用模型。

## 限额、时间与错误

所有8类的预留都计入同一短窗口：默认**账号60、全局300次／60秒**，按 UTC epoch 固定对齐窗口（不是滑动窗口，也不保证严格QPS；边界可突发）。每类同时有账号／全局每日上限，**Asia/Shanghai 每日00:00重置**：

| 类别 | 每账号／日 | 全局／日 |
| --- | ---: | ---: |
| POI | 200 | 2000 |
| 路线 | 600 | 6000 |
| 天气 | 20 | 200 |
| 地图后端行政区代理 | 40 | 400 |
| 千问文本 | 10 | 50 |
| 千问视觉 | 5 | 25 |
| 图片处理 | 10 | 100 |
| 视频处理 | 5 | 25 |

一次照片识别可能同时计图片处理1＋视觉调用1；候选和排程的一次浏览器提交可以占多个上游尝试。**这是尝试／任务次数上限，不是精确人民币费用上限**，不核算token单价、图片计价或账号已有供应商用量；也不是给开发代理新增模型调用授权。

服务端固定 `backend/.env`／进程变量：`BUSINESS_USAGE_PATH`（绝对路径），`BUSINESS_RATE_WINDOW_SECONDS`、`BUSINESS_RATE_ACCOUNT_LIMIT`、`BUSINESS_RATE_GLOBAL_LIMIT`，及完整8类JSON `BUSINESS_DAILY_LIMITS`（每类严格 `account`／`global` 整数）。示例见 `.env.example`；0代表关闭，不能解析／重复键／缺少类别等失败关闭。配置变更需所有工作进程统一停启、使用同一配置和同一存储，不能通过改路径给不同进程分配各自“全局”额度。

| 结果 | HTTP／代码 | 行为 |
| --- | --- | --- |
| 短窗口满 | 429 `business_rate_limited` | 已知重置时间与 Retry-After，需用户主动重试 |
| 当日服务满 | 429 `business_quota_exhausted` | 指明账号或全局范围；不公开其他用户计数 |
| 配置为0 | 429 `business_service_disabled` | 联系管理员，不承诺等到明天恢复 |
| 计数不可核验 | 503 `business_usage_unavailable` | 不启动新上游／处理；原历史仍可读 |

`GET /usage` 仅服务端身份本人，只返回自己的各类计数／上限、数据时间、窗口和日重置；拒绝账号查询参数，不返回全局用量或凭据。前端“我的调用额度”须主动点击，32KiB有界读取；全局可能先耗尽，个人有剩余不保证准入。共同安全提示只读白名单响应头，不消费或克隆业务正文；身份epoch检查后才发布，退出／换号清除。打开／关闭额度面板不卸载工作区或丢失保存幂等快照，无自动业务重试。

## 预占、取消及存储故障

`reserved → dispatching → received / failed / cancelled`；终态还允许从未发送的reserved进入failed/cancelled。`dispatched_at` 表示持久发送／处理意图：**不是已经证明上游收到或计费**。取消发生在SQLite预占期间会等数据库线程结束、不再发送，可能留下未发送reserved；发送意图后到实际发送前也有取消窗口。所有状态保守占额，不自动退款，不因失败、超时、取消、解析错误或进程崩溃恢复额度。`received`只表示响应读取完成，不等于业务有效或收费成功。重复发送意图被拒绝。

运行时不创建数据库，要求匹配 `.identity` 锚和严格schema／版本。文件缺失、锚缺失／不符、损坏、锁定、写入／commit失败、配置错误和观察到时钟倒退均阻止新调用；预占已提交但后续状态写入失败时仍占额。SQLite线程在取消后也等待回收；原媒体线程／进程清理后才释放容量，可能增加取消返回时间。不承诺取消上游停止计费。

### 部署、初始化、恢复与回退

独立默认 `backend/data/business-usage.sqlite3`＋`.identity`，不改 `identity-v1` 私有库、旧本机库、存档表或v1/v2/v3契约。路径拒绝指向这些数据库及sidecar、符号／硬链接。新schema初始化已在独立临时库及复制恢复测试演练，**不是原业务库自动迁移**。

从后端目录，在新服务开放前仅首次执行：

```sh
.venv/bin/python -B -m tools.business_usage init --database "$(pwd)/data/business-usage.sqlite3"
.venv/bin/python -B -m tools.business_usage verify --database "$(pwd)/data/business-usage.sqlite3"
```

自定义路径须同时配置 `BUSINESS_USAGE_PATH`，不能与身份库混用。CLI不覆盖已存在库或锚，无reset/refund入口。运行缺库时其他功能仍能启动，收费／处理功能503。不要删除库或锚“修复”；应停业务发送、调查并恢复一致匹配的库／锚。不能恢复落后于已发送尝试的旧备份来恢复额度；需保守核对台账并保持阻断。应用回退也应保留额度库，不回退为未保护的付费入口。自动备份／恢复核账、清理保留策略及版本升级迁移仍需单独设计；本轮没有新增自动清理、重置或账务退款工具。

## 实际验证与证据

本轮检查与Chrome结果见最新 [PROGRESS](PROGRESS.md)。生产构建、真实账号／Cookie／SQLite额度与存档，**上游为明确受控响应**，不称真实高德或模型验收。稳定目录：`/Users/zijing/projects/trippilot-evidence/release-1e/batch-20261018/`。不公开数据库、凭据或原始网络日志。

实际检查命令（分别在backend／frontend目录，Python全部 `-B`）：

```sh
.venv/bin/python -B -m unittest discover -s tests -q
.venv/bin/python -B -Werror::ResourceWarning -m unittest tests.test_business_usage -q
npm test -- --reporter=dot
node --test tools/verify-forwarding.node-test.mjs
npm run lint
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run typecheck
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build -- --webpack
```

最终后端完整 **628通过**（新增额度42项：核心20、发送／媒体13、完整生产边界5、工具4），含原身份／存档／七日／Python→TS回归。前端完整 **70文件／1346通过**，含实际Python额度输出→TS严格解析、身份旧响应与不消费正文、手动额度面板；Node转发5、lint、typecheck、Webpack和diff检查通过。没有新增依赖或改排程、地图代理白名单、数据库存档结构和模型配置。

开发检查中修正了HTTPS默认port为None的识别、测试夹具清理及环境代理保留；均在外部调用0的受控环境发现。最终回归还发现旧PlaceExplorer断言未等待独立Marker effect，最小改为waitFor，保留原坐标／编号／交互断言。Chrome操作两处等待条件误写（实际入口为内联选择，不是dialog；实际计数文案不含空格），仅只读核对后继续，未因此重复生成。最终SQLite rollback/close错误脱敏补强由新增受控测试覆盖，不冒称真实磁盘损坏；Chrome正常／限额流程不变。

### Chrome受控验收矩阵

生产 `BUILD_ID=Dt-Uj6omL468l8kdojmVN`，两独立浏览器会话、独立身份库＋真实额度库；POI个人3／全局5、路线8，模型类配置0且无真实传输兜底。地点与600秒步行均为合成响应，非真实高德行程。本轮没有真实地图／模型业务重验。

| 场景 | 实际结果／证据 |
| --- | --- |
| 正常业务及本人额度 | A真实登录，2次POI确认→1次排程，2条路线→保存1个明确命名的v3合成快照；额度POI2、路线2，01图 |
| 个人上限及编辑取消 | A第三次POI成功、第四次429账号日额度；编辑取消仍保留原1/1草案，查看额度3/3；02图 |
| 400px／键盘 | 03图无横溢；Enter打开额度面板；未新增业务调用 |
| 全局上限与双账号 | B前两次POI成功，第三次429全局日额度；本人2/3仍剩1，不暴露A计数，04图 |
| 计数故障与只读 | 只在独立验收库临时移走锚；B搜索503、A额度503，没有新增受控发送；A列表／详情200，05／06图 |
| 同库重启及直接请求 | 恢复原锚，安全重启验收后端；A仍POI3／路线2，A/B直接HTTP请求仍分别429；B历史为空，07图 |

7张截图逐张打开；`browser-receipt.json`仅记录脱敏路径／方法／状态和断言。登录后受控观察 **21次同源接口**：POI10（成功5、429四次、503一次），排程1，保存1，历史读3，额度读6；不声称涵盖全部身份／静态网络。传输层独立静态收据 `controlled-dispatch.jsonl` 恰好 **7次（POI5＋路线2）**，均received、无重试；不是将浏览器请求数当上游真实尝试。真实高德／模型及其他业务外部调用0。短窗竞争、媒体取消、重试、异常和幂等更多分支由受控测试验证，不称真实故障。

验收账号已禁用／撤销会话，临时凭据删除。普通3000／8000恢复；在独立库演练后，显式初始化并verify新的默认 `business-usage.sqlite3`及锚，0条初始记录；不改原私有库或旧库。原旧库和五缓存开工／收口SHA256一致。没有写入用户既有存档，验收快照只在临时验收库。

自动测试重点：双账号／全局、线程及两个独立进程原子竞争、重启／磁盘重开、午夜／固定窗口／时钟倒退、存储丢失／锚／损坏／锁定／提交失败、失败和未确定取消保守占额、重试逐次、流读取与取消、未知服务／URL、正常工厂覆盖、媒体容量、候选／排程传播、未鉴权直接请求、本人额度、历史与幂等不受限、账号隔离及原契约回归。历史业务夹具使用明确真实临时额度库，非生产绕过开关；旧研究计数／协议不改。

### 未覆盖费用与资源风险

- 浏览器直接请求高德SDK、底图、瓦片及SDK内部请求不经过本后端，不受此台账保护；需供应商控制台额度／来源限制、独立公钥策略与网关层措施。不能说所有地图网络已被账号计费控制。
- 有界上传接收／缓冲发生在媒体任务预占前；上传带宽、入口总并发、重复非法请求、存档／检索／登录等CPU和磁盘不是本业务次数额度覆盖的完整DoS边界。单进程媒体busy不等于跨进程资源并发池。
- 供应商实际账单、单次模型token与费用差异、供应商账户其他使用、告警／日月人民币预算及浏览器SDK费用仍需外部管理；不做精确RMB承诺。
- 仅单机共享磁盘多进程；多主机、服务横向扩容、稳定时钟、磁盘权限／加密、备份调度、灾难恢复和完整公网安全审计未验收。管理员同时删除库和锚或恢复旧备份属于可信运维风险，程序不能认证外部账单。

停在release-1E；保留成都公交等历史失败、知识问答停用和媒体限定结论，不公开部署或进入新功能阶段。

## 本轮交付文件

普通提交仅含以下42个审查文件；数据库／锚、sidecar、环境真实值、截图、浏览器日志和原五缓存不入库：

- `.gitignore`
- `AGENTS.md`
- `README.md`
- `backend/.env.example`
- `backend/app/api/amap_proxy.py`
- `backend/app/api/explanation.py`
- `backend/app/api/photo.py`
- `backend/app/api/places.py`
- `backend/app/api/usage.py`
- `backend/app/api/video.py`
- `backend/app/auth.py`
- `backend/app/business_usage.py`
- `backend/app/usage_runtime.py`
- `backend/app/services/candidates.py`
- `backend/app/services/schedule_routes.py`
- `backend/main.py`
- `backend/tests/business_client.py`
- `backend/tests/test_business_usage.py`
- `backend/tests/test_release_usage.py`
- `backend/tests/test_usage_acceptance.py`
- `backend/tests/test_usage_runtime.py`
- `backend/tests/test_photo_lifecycle.py`
- `backend/tests/test_video.py`
- `backend/tests/usage_fixture.py`
- `backend/tools/business_usage.py`
- `backend/tools/usage_acceptance.py`
- `docs/PROJECT_CONTEXT.md`
- `docs/ROADMAP.md`
- `docs/PROGRESS.md`
- `docs/RELEASE_1E.md`
- `frontend/src/components/auth/authentication-boundary.tsx`
- `frontend/src/components/auth/business-usage-controls.tsx`
- `frontend/src/components/auth/business-usage-controls.test.tsx`
- `frontend/src/components/places/place-explorer.test.tsx`
- `frontend/src/lib/api-endpoints.ts`
- `frontend/src/lib/api-endpoints.test.ts`
- `frontend/src/lib/auth-session.ts`
- `frontend/src/lib/auth-session.test.ts`
- `frontend/src/lib/business-usage.ts`
- `frontend/src/lib/business-usage.test.ts`
- `frontend/src/lib/business-usage-notices.ts`
- `frontend/src/lib/server/backend-routing.test.ts`
