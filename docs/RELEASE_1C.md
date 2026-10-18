# release-1C：前后端接口部署适配

2026-10-18，实际基线 `9161e82df171a0619c823c0a34823c72a58a8689`，`main`，开工fetch后与origin/main同步。直接修改实际项目，保留原五缓存、数据库、旧证据和未跟踪浏览器日志。开工已有3000生产前端和8000后端，均核对PID／目录后使用或安全重启。

**结论：浏览器业务寻址已改为固定同源路径，后端目标由可信服务端配置决定。非默认端口下的真实TCP转发与受控Chrome验证通过；不是公网部署、高德业务或模型质量重验。模型、高德及其他真实外部业务API调用均0，未改排程、费用、存档契约或数据库。** release-1B原六日案例根因未知、成都公交等历史限制不因寻址修复消失。

## 场景、完整盘点与证据

原15个浏览器请求模块直接请求 `http://127.0.0.1:8000`，部署后会连接访问者自己的电脑。另有两个地图问题：前端要求公开绝对serviceHost，后端SDK `appname`元数据只接受本地3000的页面来源。

现在浏览器只访问当前网站；以下每个模块都经 `API_ENDPOINTS` 常量表寻址，未重写业务请求函数或状态Hook：

| 范围／模块（`frontend/src/lib/`） | 浏览器路径／方法 | 保留内容 |
| --- | --- | --- |
| `places-api` | GET `/api/places/search` | 关键词、目的地、旧demo上海参数、确认与去重 |
| `candidates-api` | POST `/api/places/candidates` | 主动获取、整批语义与15秒等待 |
| `schedule-api` | POST `/api/trips/schedule-preview` | `?drawing=walking-v1`、正式／预览共用、25秒等待与严格校验 |
| `weather-api` | POST `/api/weather/forecast` | 城市日期整批、15秒等待、刷新旧快照 |
| `explanation-api`、`adjustment-intent-api`、`itinerary-query` | POST `/api/trips/{schedule-explanation,adjustment-intent,itinerary-query}` | 单次模型操作、版本与取消、原契约；本轮不真实调用 |
| `photo-api` | POST `/api/media/landmark` | 两个身份query、原始单图字节、35秒前端等待，不上传文件名 |
| `video-api` | POST `/api/media/video-frames` | request_id、原始MP4字节、60秒等待、响应5MiB限制 |
| `saved-trips` | POST／GET `/api/saved-trips`；GET `/api/saved-trips/{32位ID}` | 分页、幂等冻结内容／操作标识、保存及历史恢复、15秒等待 |
| `knowledge-api`、`knowledge-answer-api` | POST `/api/knowledge/{search,answer}` | 本地检索；保留的问答客户端仍取得停用503，不重新开放 |
| `api`、`routes-api`、`transit-api` | POST `/api/trips/plan`、`/api/routes/{walking,transit}` | `/demo`旧Mock／真实单段联调能力，未混入正式流程 |
| `amap-loader` | 当前页面绝对origin＋`/_AMapService` | 仅允许转发GET `/_AMapService/v3/config/district`，其余SDK／底图资源不在此代理范围 |

审计没有发现遗漏的业务XHR、axios、WebSocket或EventSource入口；组件复用上述请求模块。高德SDK本身仍需外部加载，但本轮没有启动真实SDK、底图或行政区查询。

## 方案、理由与取舍

### 固定同源路径＋Next内置rewrite

- 选用现有Next 16.3.3原生external rewrites，不添加依赖、自定义HTTP网关、Route Handler解析器、`proxy.ts`或通用状态框架。它支持方法／query／字节流／错误响应透传，且安装版本已实现下游close→销毁上游request／response的断开传播。
- `api-endpoints.ts`不读取任何环境值，只含固定相对路径。`next.config.ts`仅在服务端读取 `TRIPPILOT_BACKEND_ORIGIN`；支持规范HTTP(S) origin，不允许凭证、路径、query／fragment或歧义写法。请求中的 `url`、`host`、表单或模型参数不能改变目标。
- 只配置15个业务资源、health、32位小写十六进制存档详情及唯一地图路径，共18条规则；没有 `/:path*` 通用转发。未列出的资源停在Next 404。原方法、查询参数、响应正文及错误状态由原后端决定，不额外重试、不重新序列化JSON或媒体、不自动降级。
- 继续使用各客户端原来的AbortController、计时器和单调版本。没有调整组件挂载、草案失效、预览采用、费用或保存幂等逻辑；不会因转发新增模型、路线或存档操作。
- rewrites的目标写入 `.next/routes-manifest.json`，不是可随 `next start` 任意变化的运行时目标。开发阶段省略才默认服务器本机8000；生产构建／启动均须配置。生产启动校验构建规则与当前目标完全一致，缺失／损坏／不一致立即失败，不静默继续用旧目标。
- Next浏览器build manifest会移除external rewrite的目标；本轮最终35个浏览器静态文件扫描也未发现8000／18081后端URL或服务端环境变量名。服务器构建产物内仍有目标，不能把整个`.next`目录当作公开静态目录发布。

自定义Route Handler虽可实现运行时目标，但需要再维护流／跳转／请求头／取消／响应适配。当前无此必要，选择较小的内置能力，代价是**目标改变须重建**，且依赖固定Next版本的代理行为；升级时应重跑转发验证。

### 媒体边界、时限及取消

已读取安装版本的rewrites、Route Handler、环境变量、self-hosting及proxyClientMaxBodySize文档，并核对 `proxy-request.js`、`body-streams.js`、`resolve-routes.js` 和内置httpxy源码：

| 层级 | 本轮处理 |
| --- | --- |
| Next默认30秒代理超时 | 显式 `experimental.proxyTimeout=75000`，是代理连接空闲超时，不冒充业务整次硬截止 |
| Next默认10MiB克隆缓冲，超限会截断而非413 | 显式21MiB；本版本external rewrite也会调用克隆。没有引入额外body读取或`proxy.ts`。所有合法20MiB视频及边界＋1输入都可完整到达后端 |
| 照片 | 原服务端5MiB／2000万像素、上传10秒／模型操作20秒、前端35秒原样保留 |
| 视频 | 原20MiB、上传30秒／处理20秒、前端60秒原样保留；输出仍5MiB，处理与清理规则不变 |
| 浏览器取消 | 代理关闭上游socket；后端仍按原唯一receive owner、解码线程／子进程收尾后才释放容量。已发出的模型请求不能保证停止计费，这一限制不变 |

21MiB是代理缓冲配置，**不是新的业务上传许可或公网总内存限额**。Next会克隆并缓冲，不能称零拷贝；多并发请求仍可增加内存，原单后端进程media busy也不等于Next／全局入口限流。超业务限额仍由原后端明确拒绝，不能利用Next默认截断伪装有效输入。很大的非法请求可能触及Next克隆限制，未来入口应明确413拒绝而非依赖截断。

### 高德安全代理

- 前端忽略废弃的 `NEXT_PUBLIC_AMAP_SERVICE_HOST`，用当前HTTP(S)页面origin构造固定 `/_AMapService`；不会回落到访问者本机8000或外部配置地址。
- 新增服务端 `AMAP_ALLOWED_PAGE_ORIGINS` JSON列表（1～32个），默认保留原两本地3000来源。校验规范协议／host／port，拒绝凭证、非根路径、query／fragment、控制字符、重复等；SDK appname的页面路径可以存在，但来源必须精确匹配，并在上游调用前剥离。
- 它只校验SDK元数据，不是登录、来源鉴权或CORS。本轮不把允许appname解释为限制所有请求者的权限；原契约允许省略元数据的行为保留。
- 高德目标仍固定 `https://restapi.amap.com/v3/config/district`，原路径、六城／本级参数、重复query检查、JS Key匹配、服务端安全码注入、禁止浏览器安全码／任意上游、JSONP和敏感反射检查全部保留。FastAPI CORS不变，密钥不进浏览器。

## 配置与部署步骤

示例仅占位，不改已有 `.env` 文件、不粘贴凭据。本轮没有购买服务器或发布公网。

1. **后端**保留原高德和千问配置。部署时在 `backend/.env` 或进程环境补充实际页面来源，例如 `AMAP_ALLOWED_PAGE_ORIGINS=["https://travel.example.test"]`，不是随意填浏览器传来的地址；多个受信任页面origin逐项列出。还需在高德控制台按实际部署域配置浏览器Key权限（本轮未操作）。安全码及Web Key只在后端。
2. **Next服务端**设置 `TRIPPILOT_BACKEND_ORIGIN`。同机后端可用 `http://127.0.0.1:8000`，指的是服务器自身，不是访问者；分机则使用受信任内部后端origin。该变量不能加 `NEXT_PUBLIC_`。前端只公开原 `NEXT_PUBLIC_AMAP_JS_KEY`。
3. 在前端目录使用同一目标构建并启动：

   ```sh
   TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build -- --webpack
   TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run start -- --hostname 127.0.0.1 --port 3000
   ```

   更改目标后必须重新构建；不能把构建A目标的产物以B目标启动。生产配置空白／非法或清单不一致明确报错退出；服务本身不可连接时请求失败，不回退或切换目标。开发 `npm run dev -- --webpack` 可省略变量，服务端才默认本机8000；显式空值仍报错。`npm run typecheck`的Next类型生成也读取配置，例见README。
4. 此方案需要Node `next start`，不是纯静态托管。外层HTTPS入口必须把业务和地图路径送到同一Next服务，关闭会破坏上传／取消的请求缓冲，支持至少合法20MiB正文及足够响应体、代理等待和处理时长；建议入口允许21MiB并明确拒绝超限，超时不短于本轮75秒配置。还须在真实网关重验取消，不能把本机TCP验证当作公网链路保证。
5. 不为部署把FastAPI CORS改成`*`，也不要公开后端任意管理接口。现方案仅转发白名单资源，但仍不是认证、调用限额或互联网防护。小体积／短时限的托管函数方案若低于媒体边界，不能直接使用或静默截断，需另行选型。

本地原启动仍方便：在后端目录执行 `.venv/bin/python -B -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log`，前端开发默认对应它。普通生产服务已恢复3000／8000；保留原打开页面，不为验收刷新或覆盖用户工作。

## 验证：受控寻址，不是真实业务验收

### 实际TCP与生产构建

在实际项目构建Next，受控目标18081，生产前端3101。独立Node HTTP后端只生成明确标记的受控结果，不加载FastAPI、不读取凭据／数据库，也不访问任何外部业务API。没有浏览器响应拦截。

```sh
# frontend目录；两个终端，测试后端由verify自行启动并清理
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:18081 npm run build -- --webpack
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:18081 npm run start -- --hostname 127.0.0.1 --port 3101
node tools/verify-forwarding.mjs verify --output /absolute/new-evidence/forwarding.json
```

首次实际验证 **36检查／36后端dispatch通过**：15模块资源、健康／存档列表／详情／地图路径、中文及重复query顺序、请求体哈希，422／429／503错误正文／状态、停用问答503、302原样且未在服务端跟随、重复GET均到达后端（无新增缓存）、未列出路径404且不达后端、query中url／host不能换目标。

照片5MiB、视频20MiB及各自＋1字节完整传输（合法成功、越界由受控后端413，哈希／字节数相同）；5MiB响应逐字节一致；**31.017秒**受控等待成功，未遭默认30秒切断。照片、视频各有上传中／完整上传后取消，共4项，后端观察到close恰1次，无响应完成、无重复派发。这里验证的是传输，不把二进制测试字节称为可解码照片／视频，也不把受控413当真实媒体服务故障。

生产空配置、含凭证的非法origin、启动8000与构建18081不符三次真实CLI检查均退出1、无后端调用；错误不回显配置值。Next可能先输出Ready再报告配置错误并退出，不能仅凭Ready行判断启动成功。开发默认、其他非法配置由单元测试覆盖。

### 真实Chrome，受控后端

独立`serve`模式复用同一18081受控后端，3101生产页面：打开资料页没有自动业务请求，主动查询两个馆区得到**受控空结果**，不是当前语料真实命中或新知识库评测。页面明确标注“受控后端寻址验证／非真实资料、高德或模型验收”。桌面1280px、400px无横溢，Enter提交正常。

另用浏览器fetch验证停用answer503、错误422、唯一地图路径200标记，以及1MiB照片／视频上传后AbortController取消；后端两个request均已收完字节后close=1，未发送响应。7次请求全部到当前页面3101同源，由Next转发至受控18081；无浏览器8000请求、无高德SDK初始化。503／422的两条预期控制台HTTP错误不写成页面运行故障。初次截图等待使用了过窄的精确文案匹配，超时后只读取已成功的结果再截图，没有重发该查询。

截图均已逐张打开：[桌面](../../trippilot-evidence/release-1c/batch-20261018/01-same-origin-desktop.png)、[400px](../../trippilot-evidence/release-1c/batch-20261018/02-same-origin-400.png)。稳定目录：[release-1c/batch-20261018](../../trippilot-evidence/release-1c/batch-20261018/)，含`forwarding-first.json`（36项）、`browser-review.json`、`browser-backend.json`、`receipt.json`及截图，均仓库外、不提交。受控Next业务源码与最终版相同，后来只修测试桩；受控构建BUILD_ID未单独保存，不补造。本地恢复重建ID为 `yjK9HMhv2wDf0_iAJ4Un0`，目标8000，最后通过同源`/api/health`取得200；此健康检查不调用外部业务或存档。

### 本轮实际自动检查

```sh
# backend
.venv/bin/python -B -m unittest tests.test_amap_proxy tests.test_destinations -q
.venv/bin/python -B -m unittest discover -s tests -q
# frontend
npm test -- --reporter=dot
npm run lint
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:18081 npm run typecheck
node --test tools/verify-forwarding.node-test.mjs
# 两个目标的生产构建均实际运行，见上文
# repository
git diff --check
```

- 后端完整 **553通过**；地图代理定向28、城市8通过。精确来源新配置、非法来源、非默认端口／HTTPS及原密钥／路径／参数／反射等边界保持；全量回归包括照片解码／取消容量、真实本机FFmpeg、排程、费用、存档v1-v3及停用知识问答。
- 前端完整 **66文件／1317通过**；Node离线工具 **5通过**，lint／typecheck、18081受控目标及8000恢复目标的Webpack构建通过。既有七日、费用、保存重试、媒体／编辑取消和旧响应隔离断言保留。
- 首轮前端45项失败源于3个旧Mock把相对URL当绝对URL或仍匹配旧路径；仅补明确基址及`/api`前缀后4文件67定向通过，再完整1317通过。首轮lint的测试变量`module`触犯Next规则，重命名后完整lint通过；不隐藏首轮，也不称45个产品缺陷。现有Starlette/httpx弃用提醒保留。
- 没有改业务算法／响应Schema／模型配置／存档／CORS／依赖；没有运行付费验收工具。模型及高德等真实外部业务API调用 **0**，数据写入0。受控TCP／Chrome、健康及自动测试本身有本地HTTP，不声称完全没有网络。

## 保护、交付与剩余缺口

原五缓存与业务SQLite的开工／收尾SHA256相同；真实环境文件、旧证据与台账不变，原浏览器日志不删除／暂存。测试使用临时数据库，未触碰用户记录。3000原生产前端在构建前暂停，3101／18081验收服务已清理；重建恢复原3000并安全重启8000后端，Python均`-B`。

本轮54个文件，仅审查后逐项暂存：

- 后端5：`.env.example`、`app/config.py`、`app/api/amap_proxy.py`、`app/integrations/amap_proxy.py`、`tests/test_amap_proxy.py`。
- 前端配置／工具5：`.env.example`、`next.config.ts`、`vitest.config.mts`、`tools/verify-forwarding.mjs`、`tools/verify-forwarding.node-test.mjs`。
- 前端共享4：`src/lib/api-endpoints.ts`及其`.test.ts`、`src/lib/server/backend-routing.ts`及其`.test.ts`。
- 前端地图2：`src/lib/amap-loader.ts`及其`.test.ts`，同源serviceHost与旧加载／取消边界。
- 前端15个客户端及15个原测试：上表15模块对应`.ts`；测试为`api.test.ts`、`places-api.test.ts`、`candidates-api.test.ts`、`schedule-api.test.ts`、`weather-api.test.ts`、`explanation-api.test.ts`、`adjustment-intent-api.test.ts`、`itinerary-query.test.tsx`、`photo-api.test.ts`、`video-api.test.ts`、`saved-trips.test.ts`、`knowledge.test.tsx`、`knowledge-answer.test.tsx`、`routes-api.test.ts`、`transit-api.test.ts`。
- 前端组件测试3：`src/components/trip/{candidate-preparation,walking-route,transit-route}.test.tsx`，只适配Mock URL解析，不删断言。
- 文档5：`README.md`、`docs/PROJECT_CONTEXT.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`、本文。

仍未完成公网HTTPS／真实反向代理链路、云平台大小与执行时限、访问控制／数据隔离、全局配额／上传资源保护、备份恢复／监控／回滚等。没有购机、发布公网、引入Redis或新数据库；不能把本轮受控转发通过当作六城高德／媒体模型业务重新通过，更不能当作公开上线准入。**只普通提交推送本轮文件，停在release-1C，不进入下一阶段。**
