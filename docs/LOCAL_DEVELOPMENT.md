# 本地开发与配置

更新：2026-10-22。本页是从根README抽出的操作入口，不执行初始化或迁移。当前助手分层与存储增量见[ECHOMIND_MIGRATION](ECHOMIND_MIGRATION.md)，状态／授权看[PROGRESS](PROGRESS.md)，身份／数据库细则看[MYSQL_REGISTRATION](MYSQL_REGISTRATION.md)，服务器操作看[RELEASE_1F](RELEASE_1F.md)。已有服务先核对PID、端口和目录，不误停或覆盖。

## 本地环境必须分清版本

- **原专用试用服务**：`http://trippilot-test.localhost:3104`→8802，仍为旧schema／旧运行环境，不是本次助手迁移版；原独立MySQL与普通测试账号、模型／高德发送关闭等记录见[CHAT_1本地入口](CHAT_1.md#2026-10-21本机测试暂时免填登录不删除功能)。自动登录工具只替普通账号正常登录，不关闭鉴权；不能据此宣称原服务已自动升级。
- **本轮3110环境**：先受控、再固定真实复验，实际5/8模型、4次高德，验收全部scope已封存。用户随后明确选择本机试用，8804已改为无验收覆盖的普通应用，复用同一独立测试MySQL、身份锚与全部消费记录，不重置数据／额度。一次性`http://127.0.0.1:8810/`通过正常登录跳到3110；会话有效时直接打开`http://127.0.0.1:3110/`。天气／地图代理／视觉和媒体处理仍为0额度，其余也有限额，不是全功能或公网准入。私密启动资料留在仓库外，生命周期、端口及限制见[试用说明](ECHOMIND_MIGRATION.md#75-独立本机手工试用入口)。
- **从源码启动**：按下文配置自己的独立数据库和账号，使用默认3000→8000。真实查询还需要权限、凭据、产品额度；代理验收调用另需明确授权，不能靠配置密钥就视为授权。

后端源码当前按 `api/agents/core/tooling/memory/monitor/evaluation/skills` 组织，`main.py`仍为FastAPI入口；`app/`只保留受保护旧缓存，不是现运行模块目录。无需安装EchoMind原Anthropic／Redis／Chroma依赖；实际依赖以锁文件为准。

## 环境与依赖

- Node>=20.9、npm，Python>=3.13、uv；实际版本锁在[package-lock](../frontend/package-lock.json)和[uv.lock](../backend/uv.lock)。当前Next16.3.8／React19.2.8，修改前读[前端指令](../frontend/AGENTS.md)和已安装Next文档。
- npm安装脚本按项目allowScripts策略，不全局批准；生产audit历史为0、开发braces链5项high与ESLint9维护风险是当轮记录，不是本轮新审计，见[DEPENDENCY_SECURITY](DEPENDENCY_SECURITY.md)。不要`audit fix --force`或无故降级。
- 视频另依赖可信固定路径FFmpeg/ffprobe，既有macOS9.0.1验证不代表其他平台已验；缺少或兼容检查失败仅视频明确不可用。不要为启动顺便安装或升级，见[MEDIA_2A](MEDIA_2A.md)。

## 私密配置与数据库准备

后端读取固定`backend/.env`，进程环境优先；前端读取`frontend/.env.local`。参考[后端示例](../backend/.env.example)与[前端示例](../frontend/.env.example)，已有文件仅补缺项，不覆盖、不发到聊天、不进Git。

| 位置／变量 | 用途与边界 |
| --- | --- |
| 后端`TRIPPILOT_MYSQL_URL` | 当前开发选择MySQL，账号／会话／邀请／存档／额度／聊天同一配置库；仅后端SecretStr，连接错误不回退SQLite |
| 后端`BUSINESS_USAGE_IDENTITY_PATH` | 与原账本匹配的库外身份锚；不是第二份额度，不可丢失后自动重建 |
| 独立迁移环境`TRIPPILOT_ASSISTANT_COPY_TARGET_URL` | 仅助手扩展复制工具使用的新空隔离MySQL目标，不是应用自动切库设置，不填写到前端 |
| 后端`AUTH_COOKIE_SECURE`／`AUTH_ALLOWED_ORIGINS`／`AUTH_SESSION_SECONDS` | 精确页面Origin与会话期限；仅本地HTTP明确Secure=false，生产保持true |
| 后端`AMAP_WEB_KEY` | 真实POI、路线、天气服务；未配置则明确失败 |
| 后端`AMAP_JS_KEY`／`AMAP_JS_SECURITY_CODE`／`AMAP_ALLOWED_PAGE_ORIGINS` | 固定安全代理与SDK页面来源，安全码不进浏览器；不等于任意代理或CORS放开 |
| 后端`QWEN_API_KEY`／`QWEN_BASE_URL`／`QWEN_MODEL` | 当前受支持Flash、北京官方HTTPS固定路径；不静默换模型／地域，见[QWEN_SETUP](QWEN_SETUP.md) |
| 后端`QWEN_VISION_MODEL` | 独立`qwen3-vl-plus`；未明确识别不调用，历史授权不可复用 |
| 后端`BUSINESS_RATE_*`／`BUSINESS_DAILY_LIMITS` | 持久短窗口／服务分类日额度，账户与全局同时生效，不是精确人民币预算 |
| 前端`TRIPPILOT_BACKEND_ORIGIN` | 仅Next服务端可信HTTP(S) origin，不能用`NEXT_PUBLIC_`，禁止凭据／子路径／query／fragment；构建与启动一致 |
| 前端`NEXT_PUBLIC_AMAP_JS_KEY` | 仅浏览器专用公开地图Key，不得填服务端Web Key或安全码 |

**数据库必须先显式准备**：原MySQL复制迁移／verify、chat-storage-v1、chat-places-storage-v1及普通账号按[MYSQL_REGISTRATION](MYSQL_REGISTRATION.md)、[CHAT_1迁移边界](CHAT_1.md#存储与迁移不自动动旧库)和[CHAT_2](CHAT_2.md)核对。本次助手另需 `assistant-storage-v1`；应用启动不补表、不改旧库或自动给新额度，缺少扩展会明确报 `assistant_migration_required`。

新增 `tools.assistant_storage` 只从已停写、包含聊天与地点扩展的MySQL源，复制到**新的空隔离目标**并增加助手扩展；源已有助手表时完整保留。它不修改当前 `.env`、不自动切换服务、不重置额度，不把旧数据自动赋给其他账号。目标名称须符合工具限制 `trippilot_chat_` 或 `trippilot_test_` 加32位随机十六进制；用独立私密迁移环境提供目标连接，不能把真实凭据写到命令参数或报告。

以下只展示可重复检查的工具参数，先停全部源写入者、准备空目标和新的私有receipt路径，并保留原身份锚；不是建议现在对用户库直接执行：

```sh
# backend目录；源由已核对的TRIPPILOT_MYSQL_URL指定，目标由独立私密配置指定。
.venv/bin/python -B -m tools.assistant_storage \
  --receipt /absolute/private/NEW-assistant-copy-receipt.json \
  --anchor /absolute/private/ORIGINAL-usage.identity \
  --confirm-source-stopped
```

成功收据为 `state=verified`、`quota_refunded=false`，含版本及表摘要；目标非空、原锚不符、receipt已有或结果不确定则停止并保留现场。MySQL DDL不是可回滚事务；两次源快照相同也不代替停写。源码推送不自动选择新库，实际用户数据切换、新助手完整生产备份／灾难恢复仍未准入；旧备份／复制工具若不支持助手表会安全拒绝，不能拿遗漏新表的备份上线。

旧SQLite兼容仍存在：`TRIPPILOT_DATABASE_PATH`／`BUSINESS_USAGE_PATH`只有该模式下选择本机独立文件；旧`backend/data/saved-trips.sqlite3`不作写入目标，自助注册在旧SQLite模式不可用。**不要对已使用库运行init、删库重建或恢复旧台账返额**；这不是当前新版MySQL的并行写入路径。

## 启动（独立库已就绪）

从项目根目录，终端一：

```sh
cd backend
uv sync --frozen
# 只读验证基础私有库／额度／锚；不负责添加或证明assistant扩展已准备。
.venv/bin/python -B -m tools.mysql_migration verify
# 仅本地HTTP；默认允许localhost:3000与127.0.0.1:3000。
AUTH_COOKIE_SECURE=false .venv/bin/python -B -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

终端二：

```sh
cd frontend
npm ci
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run dev -- --webpack
```

打开`http://localhost:3000`或`http://127.0.0.1:3000`。这里的8000是Next服务器的后端，不是访问者本机地址；浏览器只用同源`/api/*`和`/_AMapService`。本地健康检查仅`GET /health`；其余业务需普通会话，写操作需Origin／CSRF。登录／注册不会自动授权模型验收。

首页普通消息使用 `/chat/conversations/{id}/assistant-turns`；任务记录和明确偏好分别读取 `assistant-state`、`assistant-preferences`。不要用旧 `/turns` 的单次意向成绩描述新有界执行。打开面板、选城市或输入文字不会自动调用模型；主动发送可能进入共享任务预算内的多步执行，真实验收每次模型发送仍须有适用授权。正式行程面板复用原本地控制器，未保存内容刷新会丢失；任务记录存在不代表整份正式草案可恢复。

普通账号CLI仍需交互式终端，不把密码写进参数／环境：

```sh
# backend目录，已选择正确独立数据库；仅首次需要创建测试账号。
.venv/bin/python -B -m tools.account_admin create YOUR_INVITED_USERNAME
.venv/bin/python -B -m tools.account_admin revoke YOUR_INVITED_USERNAME
```

用户名3～48位小写ASCII（字母、数字、`_.-`），密码12～128字；revoke只撤销会话，disable另外禁用账号。MySQL邀请码注册／管理员生成方式见专题，勿公开邀请码。没有生产跳过登录开关。

## 生产构建与实际检查命令

下面只列命令，不能把旧结果写成本轮执行。已有同目录服务时先检查，避免生产／开发产物互相覆盖；历史验收沿用Webpack，不代表Turbopack在所有环境有故障。

```sh
# frontend目录；改后端origin需重建，并以相同值启动。
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build -- --webpack
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run start -- --hostname 127.0.0.1 --port 3000

# frontend目录，按实际改动运行。
npm test -- --reporter=dot
npm run lint
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run typecheck

# backend目录，Python -B保护原缓存；不运行付费验收脚本。
.venv/bin/python -B -m unittest discover -s tests -q

# 仓库根目录
git diff --check
```

实际MySQL测试仅在显式独立测试连接时运行，缺少测试环境会skip，不能冒充数据库验收。普通受控测试不需要真实上游凭据。`next/font/google`在干净构建可能需要字体网络；纯文档不重复业务构建。

## 部署与历史说明

同源转发、上传大小、75秒代理空闲上限及取消语义见[RELEASE_1C](RELEASE_1C.md)；它不等于公网HTTPS／入口防护或业务质量验收。不再使用旧`NEXT_PUBLIC_AMAP_SERVICE_HOST`，也不将CORS改成`*`。Linux权限、原生模块、持久数据、HTTPS和操作员分步命令见[RELEASE_1F](RELEASE_1F.md)，MySQL增量和聊天备份准入分别核对，代码push不自动部署。

旧阶段的操作／失败／测试原记录在[历史索引](archive/README.md)，不是重新调用或迁移授权。当前产品与停止点只看[PROJECT_CONTEXT](PROJECT_CONTEXT.md)和[PROGRESS](PROGRESS.md)。
