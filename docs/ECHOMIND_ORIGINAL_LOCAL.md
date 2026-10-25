# 原版 EchoMind 独立本机入口（2026-10-25）

**后续界面更新**：5173前端已更名“智应 ServeFlow”并轻量调整品牌与样式，详见[SERVEFLOW_UI](SERVEFLOW_UI.md)。下文源码diff为空及检查结果记录的是初次启动时状态；当前前端有明确的5文件本地修改，后端框架／模型提示／接入配置未随品牌修改。原来源与内部兼容键保留。

本轮按用户新要求：**先直接运行上传的原版工程，只将模型接入配置改为千问，不经过 TripPilot 的旅行适配层。** 不删除或覆盖现有 TripPilot，不修改原 Agent、提示、意图融合、路由、RAG、记忆、工具、Skills、监控或评测代码，也不把客服示例当旅行能力。

## 实际运行的项目

| 项目／入口 | 实际状态 |
| --- | --- |
| `EchoMind/` | 原 Python 后端，提交`ef847eb5b719ef98be9d5673f3429e62cd88109e`，源码diff为空 |
| `EchoMindFrontend/` | 原 Vue/Vite 前端，提交`2ec9d14ed094203d455ceae311ebb5fc52911674`，源码及锁文件diff为空 |
| `http://127.0.0.1:5173/` | **本次原版页面**，选择Python；`/api/python`经Vite转发8816 |
| `http://127.0.0.1:8816/docs` | 原版API文档，后端仅监听回环 |
| `http://127.0.0.1:3110/` | 既有TripPilot旅行适配入口保留；中断后续查服务未运行，本轮未重启它 |

Java后端没有启动；不点击Java作为本次使用步骤。原版开发环境没有登录要求，但这**不是公网多用户隔离或发布方案**。浏览器的`user_id`及原Redis／Chroma记忆与TripPilot的MySQL／匿名工作区不是同一个权限或数据体系。

## 为什么不用重写模型协议

[阿里云官方 Anthropic Messages 说明](https://help.aliyun.com/zh/model-studio/anthropic-api-messages)已列出北京业务空间端点及`qwen3.7-flash-2026-07-15`。原工程使用Anthropic SDK，所以直接配置官方兼容入口，不再写OpenAI／Anthropic翻译层：

```dotenv
ANTHROPIC_BASE_URL=https://<北京业务空间ID>.cn-beijing.maas.aliyuncs.com/apps/anthropic
ANTHROPIC_MODEL=qwen3.7-flash-2026-07-15
ANTHROPIC_API_KEY=<服务端百炼Key，不填写到前端>
```

SDK请求路径为`/apps/anthropic/v1/messages`，不是TripPilot原OpenAI兼容的`/compatible-mode/v1/chat/completions`。变量名里的ANTHROPIC表示SDK协议，并不代表调用Claude或另购Anthropic Key。模型名、地址和Key的配置兼容不证明账号权限或回答质量；**本轮没有发送真实模型请求**。

本机私有启动器仅在服务端内存读取`backend/.env`里的三项既有千问配置，验证原北京HTTPS域名／原固定路径及Flash快照后转换为官方Anthropic路径；给全部角色与Judge指定同一Flash。原两个项目的`.env`和TripPilot环境文件均未改写，密钥不写入启动器、前端、文档或命令行。

## 运行环境与重新启动

独立私有运行目录：

```text
/Users/zijing/projects/trippilot-evidence/original-echomind-local-20261025.maJ6EL/
```

目录700，包含Python3.12.14虚拟环境、私有启动器、Redis Unix socket及AOF、独立Chroma、检查日志和截图。原上传虚拟环境指向本机不存在的Python3.12路径，因此没有复用或移动它；在独立路径按原`requirements-dev.txt`安装112包，保留解析版本清单。原版数据不复制或改写。

以下是**服务停止后的**三终端启动命令。已经运行时不要重复启动、清库或覆盖目录；先检查8816／5173和私有socket。现有启动器只支持运行或`--check`，不接任意模型／URL请求参数。

```sh
# 终端一：独立Redis，只开放当前用户可访问的Unix socket，TCP端口关闭。
redis-server --port 0 \
  --unixsocket /Users/zijing/projects/trippilot-evidence/original-echomind-local-20261025.maJ6EL/redis.sock \
  --unixsocketperm 700 \
  --dir /Users/zijing/projects/trippilot-evidence/original-echomind-local-20261025.maJ6EL \
  --dbfilename working.rdb --appendonly yes --appendfsync everysec --save ''

# 终端二：原版API，使用独立Python环境和仅配置启动器。
/Users/zijing/projects/trippilot-evidence/original-echomind-local-20261025.maJ6EL/venv/bin/python -B \
  /Users/zijing/projects/trippilot-evidence/original-echomind-local-20261025.maJ6EL/start_original.py

# 终端三：原版前端，明确覆盖默认8000，避免连到旧应用。
cd /Users/zijing/projects/trippilot/EchoMindFrontend
VITE_PYTHON_API_URL=http://127.0.0.1:8816 VITE_DEFAULT_BACKEND=python \
  npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

前端使用原锁文件`npm ci --ignore-scripts --no-audit --no-fund`重装本机依赖：搬来的Rollup模块先被macOS代码签名策略拒绝，重装后构建通过，没有删除锁文件、放宽系统安全策略或修改源码。若浏览器保存过旧连接配置，选择Python并核对Python地址为`/api/python`，不使用旧8000。

Chroma沿用原development嵌入式持久模式，三层记忆／知识库代码不改；首次下载原MiniLM模型约79.3MiB到标准用户缓存，并导入原6个客服示例片段。下载依赖／模型文件是网络资源获取，**不写成完全没有网络请求**；千问、高德、硅基流动业务调用均0。意图向量使用原可配置local n-gram；外部MCP及告警Webhook在本次本机配置中关闭，没有删实现或把Microsoft技术资料接成旅行来源。标准本地MCP仍由原应用挂载。

## 实际检查与未验收范围

- 原后端**136项／7.02秒通过**，`-B`、禁INET连接审计钩子、假模型凭据，未调用真实模型或写原库；日志保留。原SDK0.40.0与原HTTPX0.28.1客户端离线构造通过，不代表上游鉴权成功。
- 原前端**14项通过**，重装后Vite生产构建通过。健康200、Redis PONG、知识库统计6片段；四角色健康信息均使用Flash，调用统计为0。
- 真实Chrome只读开页及三页切换，页面已连接；桌面和400px截图逐张打开检查，无横向溢出。验收浏览器限定本机GET，聊天／评测／导入等POST不能执行；不将页面显示或受控测试冒充真实模型回答验收。
- 两份原工程源码／锁文件diff为空；旧TripPilot服务、库、五缓存及旧证据保持。没有新增Docker、MySQL迁移、云端操作或旅行功能；根仓库只记录本次操作，不把上传的嵌套仓库及其数据盲目加入提交。

**停止点：原版工程已独立启动并完成离线／启动／只读页面检查；千问真实聊天、RAG选材、协作收益、记忆质量及评测模型均未在本轮验收。** 操作中断后发现先前服务进程均已不在；仅使用同一私有目录恢复本次Redis／原API8816／前端5173，不重新建库／下载模型或重跑模型，原TripPilot／MySQL未重启。原SDK重试／原工具降级／后台画像保持，聊天可能触发多次物理模型请求；TripPilot旧持久计量没有被谎称自动覆盖此原版进程。只适合当前用户本机试用，不开放公网。后续若适配旅行，应另行确定领域Agent／工具／Skills／资料；本轮不再先行改造。

## 这一步如何解释

30秒口述：本轮没有重新设计或声称原创EchoMind框架，而是直接运行有明确版本的原工程。百炼提供相同Messages协议，所以使用原SDK、只替换服务端地址／模型／Key。依赖在本机重装、数据独立、旧应用不切换；测试和健康检查证明可以启动，不能证明千问回答正确或已经完成旅行场景适配。

可能的追问：

1. **为什么还看到Anthropic变量？** 它表示兼容协议和SDK接口；实际官方端点及模型是千问，不是Claude。
2. **你修改了哪些框架方法？** 本轮原源码diff为0，没有修改；贡献是核验兼容入口、修复本机依赖环境并隔离运行配置，不冒认原作者方法。
3. **健康200为什么不等于验收通过？** 健康只验证初始化；真实Key权限、工具选择、事实依据、多轮与失败恢复仍需独立验证。
4. **为什么不直接公开这个开发入口？** 原版开发权限／用户ID、SDK重试及后台调用不等于TripPilot的身份、持久额度和发布保护；本轮仅回环本机试用。
