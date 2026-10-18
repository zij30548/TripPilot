# release-1D：最小邀请登录与存档隔离

2026-10-18，基线 `072dbe1f58861f4a63fd27eb8da859b84bd9f813`，main，开工 fetch 与 origin/main 同步。范围：受控邀请内测，不是全部公开上线门槛通过；真实模型、高德及其他外部业务调用 **0**。六城／七日、排程、费用和 saved-trip-v1/v2/v3 内容语义未变；知识库生成问答继续停用。

## 场景与选择

原存档只有全局 operation_id，没有归属，不能直接给多人开放。本轮继续使用已有 SQLModel＋SQLite：低并发、单服务的邀请内测不需要同时引入 Redis 或新数据库。身份、会话、限流与存档在同一个受保护的磁盘数据库中提交；账号所有权由服务端会话决定。将来多实例／更大写入量须另评估连接、锁、备份与数据库迁移，不宣称当前方案无限扩展。

新私有库默认 `backend/data/trippilot-private.sqlite3`；可用固定后端 `.env` 或进程中的 `TRIPPILOT_DATABASE_PATH` 指定绝对路径，环境优先。**硬拒绝原 `backend/data/saved-trips.sqlite3` 作为新应用写入目标**。原库本轮未迁移、未改写。新库和副本权限0600，初建目录0700；数据库和 sidecar 沿用 Git 忽略规则。

## 身份、会话与配置

- 锁定 `argon2-cffi==25.1.0`（及 uv.lock 传递依赖），使用库的 Argon2id 默认参数，不自行设计密码加密。参考 [官方 API](https://argon2-cffi.readthedocs.io/en/stable/api.html)。密码只在交互式管理员 getpass／登录请求内使用，数据库保存哈希；CLI拒绝非TTY创建，避免getpass回退回显。账号小写ASCII 3～48字，创建密码12～128字，无公开注册、短信、第三方登录或后台。
- 256位随机、不透明会话，库中保存SHA256凭据摘要；另有非认证用途的随机session_id供UI识别会话更替。固定有效期默认8小时、每账号最多8个有效会话；退出撤销当前会话，CLI可撤销全部或禁用账号。已执行的请求／保存可能完成，不承诺注销必然回滚或停止上游计费。
- `AUTH_COOKIE_SECURE=true` 默认：`__Host-trippilot_session`，HttpOnly、Secure、Path=/、无Domain、SameSite=Lax。本地HTTP必须**显式** `AUTH_COOKIE_SECURE=false`，改用不带 `__Host-` 的本地Cookie；不能将此设置用于公网HTTPS部署。真实Chrome验证本地HTTP属性；生产Secure／Host前缀通过受控HTTPS测试验证，不称为真实TLS部署。
- `AUTH_ALLOWED_ORIGINS`：明确HTTP(S) origin的JSON白名单，默认localhost／127.0.0.1的3000端口。生产需配置实际HTTPS站点；不信任转发请求头自动推导来源，不改原CORS或高德代理上游限制。
- 写请求要求精确Origin＋每会话CSRF令牌；登录也要求预登录双提交令牌与Origin，不能仅靠SameSite。令牌仅前端内存，普通业务请求统一附带CSRF头，原媒体二进制、取消／超时逻辑保留。参考 [OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)、[会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。
- SQLite持久预占登录次数：同用户名每15分钟最多5次、全局每5分钟最多30次，正确与错误尝试均计；未知账号使用dummy哈希，错误文案相同。密码校验同时最多2个，忙时拒绝；不信任任意X-Forwarded-For。**这是基本登录滥用防护，不是高德／千问生产配额或完整抗DDoS系统。**
- 所有身份／业务响应no-store、Vary:Cookie，不记录密码、会话、CSRF、认证头或原始请求日志。

从后端目录运行（不填写真实密码到命令行）：

```sh
uv sync --frozen
# 只适用于本地 HTTP；HTTPS 保持默认 true。
export AUTH_COOKIE_SECURE=false
.venv/bin/python -B -m tools.account_admin create your_invited_username
.venv/bin/python -B -m tools.account_admin revoke your_invited_username
.venv/bin/python -B -m tools.account_admin disable your_invited_username
.venv/bin/python -B -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

`AUTH_SESSION_SECONDS` 为60～604800秒，默认28800。示例仅占位；实际环境文件、凭据和原有模型／高德配置均未修改。前端继续通过 release-1C 同源转发，新增三个固定auth资源，生产构建／启动仍须同一可信 `TRIPPILOT_BACKEND_ORIGIN`。

## 接口访问矩阵

| 范围 | 访问与执行边界 |
| --- | --- |
| Next页面壳、JS/CSS/font/必要静态资源 | 公开资源不含用户草案；身份边界通过前不挂载工作流程。不是靠隐藏按钮保护后端 |
| GET `/health` | 公开、无用户或上游数据 |
| GET `/auth/session` | 公开读取身份／预登录CSRF，no-store；凭据只在HttpOnly Cookie |
| POST `/auth/login` | 无需已有账号会话，但先验证预登录／现有会话CSRF＋Origin，4KiB／10秒有界读取、严格字段与重复键拒绝、限流 |
| POST `/auth/logout` | 有效会话＋CSRF＋Origin，服务端撤销当前会话 |
| `/saved-trips` 及 `/{id}` | 全部需会话；POST再校验CSRF；保存／列表／详情／重试／历史副本读取按服务端用户归属 |
| POI、候选、步行／公交、排程、天气、三个行程AI、照片／视频 | 在路由依赖、正文读取、上游请求或解码之前检查身份；所有POST再检查CSRF／Origin |
| `/_AMapService/v3/config/district`、`/trips/plan`、`/knowledge/search`、API文档 | 同样需会话；原固定代理限制与业务契约保留，/demo无特权 |
| POST `/knowledge/answer` | 未登录401；有身份及CSRF后仍固定503 `knowledge_answer_disabled`，不调用模型 |
| OPTIONS | 仅原CORS预检／无执行路由，不进行业务处理；未知业务路径不能绕过身份 |

纯ASGI门禁不读取receive，不破坏照片／视频的单接收者及断开监听；直接绕过Next访问后端同样检查。原旧业务单元测试改用**仅tests目录的显式已认证夹具**，不增加生产配置／请求头绕过。新身份、全接口准入和双账号测试使用完整生产ASGI堆栈、真实Argon2和临时磁盘SQLite。

## 存档与界面隔离

`accounts`、`auth_sessions`、`login_buckets`、`storage_metadata` 和原 `saved_trips` 共库。存储版本为 `identity-v1`，与快照v1/v2/v3分开。存档增加可空owner_id外键（空只为隔离旧资料），唯一约束改为 `(owner_id, operation_id)`；客户端owner_id仍是非法额外字段。跨账号直接ID统一404，列表和幂等查询都带owner条件。跨用户即便使用同一操作ID也不返回对方记录；同用户同内容返回原记录，不同内容409。

未改变快照白名单、2MiB、路线重放、整数分与未知／零／非法费用语义；公交字段保留，步行绘图附件仍不入存档。新保存仍为v3，v1/v2原严格规则不漂移。

前端以单调身份epoch重挂整个工作树，清除草案、估算、历史、需求编辑、预览、AI及冻结保存重试状态。所有15业务客户端使用同源带Cookie的apiFetch，挂接原AbortSignal，身份变化时abort，并在头部及正文消费后再次核对epoch；晚401不清除新身份，覆盖ABA。退出按钮／登录回调同样绑定epoch。密码输入不跨会话保留。

过期定时、401、页面恢复可见／focus核查；BroadcastChannel只发不含身份凭据的变化通知。同一会话检查仅隐藏／禁交互，不卸载保存工作；身份不符／核查失败清空。服务端撤销在下一次请求／身份检查发现，不承诺实时推送。无BroadcastChannel浏览器依赖focus／请求检查，已发送保存仍可能提交，重新登录后只从自己的历史确认，不自动重写。

### 实测发现并修复的衔接问题

初次Chrome退出后，多标签页匿名GET身份各自轮换预登录Cookie，导致正常换号POST登录403。首次失败截图06保留。修复为：匿名身份查询复用合法的预登录令牌，退出仍轮换；新增共享Cookie多次读取／退出旧令牌拒绝测试。修后原上下文主动重新检查、换号成功；未放宽Origin／CSRF。完全同时首次无Cookie的两个bootstrap仍可能互相竞争，须主动重新检查后手动登录，不自动重试写请求。

## 迁移与回退演练

新空库可以首次建表，已有无版本／错误结构库明确拒绝，不自动升级。既有identity-v1还校验精确存档字段、空值含义、主键、归属外键及完整复合唯一约束，不能只伪造版本标记绕过。

仅对**新副本**操作（从后端目录，所有路径绝对、目标与备份必须不存在）：

```sh
.venv/bin/python -B -m tools.migrate_identity migrate \
  --source /absolute/legacy.sqlite3 \
  --destination /absolute/new-private.sqlite3 \
  --backup /absolute/pre-identity.sqlite3
.venv/bin/python -B -m tools.migrate_identity rollback \
  --backup /absolute/pre-identity.sqlite3 \
  --destination /absolute/new-restored-legacy.sqlite3
```

SQLite只读连接＋backup获取一致副本，迁移副本在事务中重建存档表，原JSON、ID、时间和版本逐项保留，owner=NULL且任何邀请账号均不可读。重复目标明确拒绝；不会分给第一个账号、不会覆盖源库。失败保留迁移前备份。回退只生成旧结构的新恢复文件，**不是在当前库原地降级，也不合并迁移后的新写入**。实际回退应用还需停写、保留新私有库备份、使用上一应用版本并明确配置／装配恢复副本；上一版本原默认路径固定，不能假设旧代码会识别新环境变量，更不能直接覆盖用户原库。这一步属于部署切换，不由本轮自动执行。

本轮临时磁盘演练覆盖：初始化／重复启动、复制迁移、源hash不变、旧行隔离、相同旧操作ID新建归属行、回退原字段读取、已存在目标拒绝、损坏／版本／九种约束异常拒绝。**没有对原用户库运行迁移或回退**。

## 实际检查与Chrome证据

最终检查：

```sh
# backend
.venv/bin/python -B -m unittest discover -s tests -q
# frontend
npm test -- --reporter=dot
npm run lint
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run typecheck
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build -- --webpack
node --test tools/verify-forwarding.node-test.mjs
# root
git diff --check
```

后端 **586通过**（身份15、存储11、完整生产访问边界5、验收工具2；含既有Python→TS/v1-v3回归）；前端 **68文件／1334通过**（新增17），Node转发工具5通过；lint／typecheck／Webpack通过。Chrome使用的构建为 `nYGqEBPqP7Q20rTnzQR0E`；提交前补强视频流／非2xx响应的身份读取与取消清理、SQLite路径字面解析，再运行全部检查，最终构建 `YJUvAG1Ai0k5CmimKnljG`。这些增量不改变已验登录／存档UI和Cookie协议，流取消／路径特殊字符另有受控测试；没有冒称两次构建均重跑全套Chrome。首次新增前端断言有3ms夹具差异、测试lint别名报错，已修夹具后完整通过；一次typecheck漏传既有必需backend origin明确失败，补齐命令后通过。旧Starlette/httpx弃用提醒保留，未升级无关测试依赖。

真实Chrome＋生产构建＋独立验收库；`tools.identity_acceptance`只在验收进程用MockTransport提供**合成地点／路线**，模型依赖禁止。正常产品中没有假响应接口或认证绕过。不是实时高德／模型／交通质量验收。

| 场景 | 实际结果 |
| --- | --- |
| A/B独立浏览器登录 | 原生Cookie、真实密码验证；A页面生成合成草案并点击保存v3；B列表空 |
| 直接ID／归属伪造 | A读B、B读A均404，伪造owner_id POST422，列表传owner参数不泄露他人 |
| 跨用户幂等 | 同操作ID在B产生独立记录；B重试返回自身ID，A再登录重试仍原ID与保存时间 |
| 历史继续规划 | A自己的源可读取建副本；B访问A源明确无记录，未建半初始化工作区 |
| 退出／换号 | A退出清空原工作、同浏览器副本也失效；修后切B只看到B存档，无旧A草案 |
| 过期／撤销 | 验收库受控设置过期＋真实CLI撤销，旧请求401后清界面；主动检查并重登，存档仍各自可读 |
| 未授权外部／媒体 | 无登录保存、照片、视频、排程、天气和知识问答6次实际请求均401；完整ASGI测试证明不读body、不进入上游／解码 |
| 400px／键盘 | 登录Enter、退出／错误提示、换号和历史列表可用，无横溢 |

A记录 `4d3d280c429d44958d964c65e50daf11`、B `86f6fb4686d648d5a35bc92e40ec2139` 仅存在独立合成验收库，不是用户存档。后端重启后原保存时间与内容仍可读取。监听A/B主页面37个业务／身份响应，辅助历史副本及未登录页另行断言，**不声称监听覆盖所有浏览器网络请求或据此推算上游尝试**。全部外部业务为0；静态Next资源另计。费用／编辑晚响应、ABA等更多分支由受控组件／集成测试覆盖，没有冒称本轮重新实测全部旅行或媒体处理流程。

稳定证据：[batch-20261018](../../trippilot-evidence/release-1d/batch-20261018/)，9张截图已逐张打开；[脱敏记录](../../trippilot-evidence/release-1d/batch-20261018/receipt.json)。06是首次失败，07是修后换号、08过期、09重登持久化；不覆盖旧证据。凭据文件独立0600且不进证据／Git；验收后禁用测试账号、恢复普通服务。

最终构建在普通（无验收替身）服务上补做400px匿名冒烟：登录门禁可见、健康200、未登录历史401、无横溢；仅身份／健康／未授权读取，不调用业务上游。这不是再次登录保存全链路重验。临时凭据文件已删除，独立验收库保留在仓库外，原用户库与五缓存hash不变。

## 文件范围、取舍与停止

- 身份／存储：`backend/app/{auth,identity_models,storage,saved_trips}.py`，`app/api/{auth,saved_trips}.py`、`main.py`；CLI `tools/{account_admin,migrate_identity,identity_acceptance}.py`；`pyproject.toml`、`uv.lock`、`.env.example`。
- 新测试／夹具：`tests/{test_identity,test_storage_identity,test_release_identity,test_identity_acceptance,archive_owner,business_client}.py`；原业务测试仅显式使用已认证测试客户端，原存档／duration／destination及两个契约导出种子加入受控用户。没有删除既有失败边界断言。
- 前端：`auth-session.ts/.test.ts`、`components/auth/authentication-boundary.tsx/.test.tsx`、`app/layout.tsx`、`components/trip/saved-history.tsx`，15个业务客户端、`api-endpoints.ts/.test.ts`、`server/backend-routing.test.ts`。流式body前后检查身份，EOF／取消／错误清理；错误响应按消费惰性登记，未读取的照片错误不滞留，同时保留视频错误体取消／超时联动。
- 文档：本页、README、AGENTS、PROJECT_CONTEXT、ROADMAP、PROGRESS。原五缓存、环境、数据库、证据及浏览器日志不提交。

尚无公开注册／找回密码／权限后台、生产全局业务配额、自动备份恢复调度、公开HTTPS／网关／多实例／完整安全审计、隐私保留与删除流程或OS级媒体沙箱。SQLite写锁可能返回安全503；重试仍手动。静态壳可公开访问不等于敏感API公开；账号已授权的外部调用仍须另有生产费用准入，历史验收额度不是新授权。

本轮共72个交付文件；其中大量旧测试仅替换明确的已认证业务夹具，认证安全测试仍完整穿过生产门禁。停止在release-1D。普通提交推送仅本轮文件，不强推；不公开发布、不分配旧记录、不进入其他阶段。
