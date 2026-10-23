# EchoMind 完整迁移：原核心、千问与安全依赖适配

**当前：2026-10-23，第2步依赖／安全适配已完成离线及真实本地存储验证，产品入口仍未切换。** 本轮新增范围、复现命令、限制和停止点见[第9节](#9-第2步依赖计数与安全组合2026-10-23)。下方第1～8节是第1步原交付记录，其中“本步／当前”按当时理解，不覆盖本节及PROGRESS最新摘要。

更新：2026-10-23。开发基线 `1d5a8e3d4a1a870dbc94461ee43db18cdd1047db`。

## 1. 这次改变了什么

用户要求的是保留 EchoMind 的完整通用引擎，再替换旅行业务，而不是仅借鉴目录、把高级机制缩减成同名简化类。这次按该要求重新开始完整迁移。

**本步完成范围是独立核心候选与离线验证，不是正式产品已经切换。** 现有聊天、匿名工作区、MySQL、行程、费用、确认、存档及知识检索入口不变；知识库生成式问答继续停用。未修改前端、环境文件、用户数据库或云端，也没有真实模型／高德调用授权。

## 2. 原代码如何完整保留

来源为用户提供的 EchoMind `ef847eb5b719ef98be9d5673f3429e62cd88109e`；原目录保持不动。交付到 [echomind_core/upstream](../backend/echomind_core/upstream) 的是 **20个核心Python模块、4份原Skill文档、11份原单元测试及1份MCP测试服务**，共36个原文件／7743行，另有11个空命名空间文件。

只做以下可重建变换：内部包导入加命名空间；原 `AsyncAnthropic` 导入改接千问兼容门面；测试辅助导入／两处模块名字符串同步重定位。没有删掉融合、并行、合成、缓存、熔断、记忆、评分或热加载算法。

[SOURCE.json](../backend/echomind_core/SOURCE.json) 逐文件记录原哈希、交付哈希和变换；[标准库核验工具](../backend/tools/echomind_core_manifest.py) 在原目录存在时按原字节重建核对，不在时验证交付清单。多文件、缺文件、额外字段、链接及路径越界均拒绝。只核验来源与代码保真，不证明业务质量。

没有复制原 `.env`、数据、嵌套Git、虚拟环境、客服API／认证或服务器启动入口。原API集成测试与4个独立服务验证脚本不适用于当前工作区，未作为可运行测试迁入；清单保留其原哈希与排除理由。原客服角色、提示、示例、默认模型值仍保留作原版行为对照，**不是旅行适配已完成**。

原提供目录未找到根许可证。保留来源与贡献归属，不声称原框架全部为本项目原创；进一步公开分发前仍需确认复用权限。

## 3. 八类能力的本步状态

| 原能力 | 本步迁入／离线依据 | 尚未完成的产品接入 |
| --- | --- | --- |
| AgentOrchestrator | 原专业池、主辅路由、真正并行执行、合成及降级；受控跨模块测试 | 旅行专业角色、统一业务提交、效果对照 |
| IntentRecognizer | 原LLM／关键词／向量分数融合、模板纠正和缓存失效 | 旅行意图与示例、千问语义向量；本步向量对照为明确标记的本地ngram，不称语义模型 |
| MCPToolManager | 原改写、并行召回、去重、重排、熔断、TTL及上下文缓存 | 旅行工具、可信远端白名单、持久额度与权限边界 |
| KnowledgeBase | 原Chroma文档分块、幂等导入和检索实现；原受控集合测试 | 独立索引、来源／馆区规则、Embedding及检索效果验证；未替换现有BM25 |
| MemoryManager | 原Redis工作记忆／Chroma情景及画像实现原样保留；相关锁、状态和隔离断言 | 独立服务、同意／撤回、MySQL权威状态协调与恢复演练；未迁移用户数据 |
| PerformanceMonitor | 原指标、异常、建议和实际路由降权反馈；受控验证 | 持久化／多进程指标、告警配置及语义质量评估 |
| Evaluator | 原Judge、校准、失败及分母逻辑；受控无效评分不补分 | 旅行题集、人工校准、真实调用授权；不能以同模型评分自证正确 |
| Skills | 原发现、匹配与热重载，同一Agent实例重读规则测试 | 可信目录及权限／版本治理、旅行Skill注册；现主聊天仍未切换 |

“在线学习”目前是纠正模板追加和缓存失效，不是模型权重训练。原版的本地ngram向量不能冒称远程语义向量。原机制存在、测试通过和适用于旅行是三件不同的事。

## 4. 千问适配不是改一个地址

[provider.py](../backend/echomind_core/provider.py) 保留原核心使用的 `messages.create` 调用形状，但线上协议是千问原生 `tools/tool_calls`，没有安装Anthropic SDK：

1. `system`、文本块、`tool_use`、`tool_result` 转成千问消息及工具历史。
2. 千问响应转回原算法可读取的文本／工具块，保留调用ID、用量及实际返回模型标识（未知不补造）。
3. 严格校验工具名、参数Schema、历史引用、重复ID、坏JSON、截断、拒绝及尺寸；不截取、修补或猜测答案。
4. 必须由可信代码绑定配置快照、`MeteredAsyncClient`及共享任务预算；构造器不接实际Key、任意URL或重试。旧Claude默认值不会静默映射成功，调用者须显式选择当前固定Flash快照。
5. 使用现有北京官方地址校验、20秒整次HTTP截止与响应限制。每任务最多3次模型、4次工具、45秒；每调用最多2048输出Token，合计最大Token预约6144。并行子Agent共享同一预算；失败、取消、不确定不返额。模型适配失败后锁止新发送；已在途请求不承诺停止计费。
6. 绑定结束会撤销子任务继承的上下文；旧子任务不能重新发送或发布晚结果。持久额度仍由原服务端owner上下文在实际传输前计数，缺失／故障即拒绝。

原工具执行仍需可信派发包装器调用工具预算；本步桥接集成测试已明确使用该包装，而非声称原算法自动受保护。原Judge在“合法文本但评分内容非法”时的自动重试也未被偷偷改掉：它仍是准入阻塞，不能直接开放付费评测。适配器失败锁止不等于能检测所有下游语义失败。

## 5. 依赖与隔离

[pyproject.toml](../backend/pyproject.toml) 中 `echomind` 为选装依赖：ChromaDB0.5.23、Redis客户端5.2.1、MCP SDK1.26.0、Prometheus客户端0.21.1、jsonschema4.26.0；`echomind-test` 沿用原测试依赖范围，具体版本锁在 `uv.lock`。没有覆盖原requirements或降级FastAPI。原生产依赖版本全部保留，另有[基线](../backend/echomind_core/application-dependencies.json)及检查。

本轮只在仓库外独立Python3.13.12环境安装，没有改运行中的 `.venv`、没有安装／启动Redis或Chroma服务，也没有下载Embedding模型。原Chroma的默认Embedding行为仍保留，未据此发起下载／导入。

首次未指定Python时uv选择3.14，旧tokenizers构建元数据失败；失败环境保留。改用明确3.13的**新独立环境**后安装、导入及测试通过。因此本步只确认本机macOS/Python3.13，不声称Python3.14、Linux原生依赖或低内存服务器已验。

可重复的离线命令（在仓库根执行；`ECHO_ENV`须是新建、仓库外的专用路径，不是现有服务venv）：

```sh
cd backend
ECHO_ENV="$(mktemp -d /tmp/trippilot-echo.XXXXXX)/venv"
UV_PROJECT_ENVIRONMENT="$ECHO_ENV" uv sync --python .venv/bin/python --frozen --extra echomind --group echomind-test
"$ECHO_ENV/bin/python" -B tools/echomind_core_manifest.py
"$ECHO_ENV/bin/python" -B -m unittest tests.test_echomind_core_manifest tests.test_echomind_candidate_boundary tests.test_echomind_core_behaviors tests.test_echomind_provider -q
"$ECHO_ENV/bin/python" -B -m pytest echomind_core/upstream/tests -q -p no:cacheprovider
```

普通应用不安装选装依赖时，新适配／行为测试明确跳过，不算通过；原清单和隔离测试仍可执行。原测试专用 [conftest](../backend/echomind_core/conftest.py) 只在候选测试范围替换虚拟SDK构造，未设响应就报错，并禁止INET连接；不是生产假接口或自动兜底。原MCP测试用真实SDK、stdio子进程和内存ASGI完成Tools／Resources，不访问外部服务。桥接测试独立使用实际有界／持久计数传输加 `MockTransport`，与原算法测试分别报告。

## 6. 检查与证据

最终实际结果：

- 完整依赖环境：新来源／产品隔离／跨模块／桥接检查 **47项通过**（1.235秒）；原核心pytest **124项通过**（6.41秒）。两组分开，不重复叠加早期子集。原SDK构造替身不用于桥接组。
- 默认应用venv：新候选相关discover **17项发现，15项执行通过、2个选装依赖模块明确跳过**；不把跳过记通过。
- 原应用定向回归：`tests.test_echo_runtime tests.test_assistant_api tests.test_assistant_dialogue_surface tests.test_assistant_preferences tests.test_assistant_store tests.test_workspace_identity tests.test_usage_runtime tests.test_explanation tests.test_adjustment_intent tests.test_itinerary_query tests.test_knowledge_disabled`，**145项发现，128执行通过、17个独立MySQL条件跳过**（8.001秒）。均使用受控上游／独立临时库，不读写用户库。
- 来源完整重建、`uv lock --check`、原生产依赖版本比较通过；文档链接、敏感信息和`git diff --check`核对。没有前端改动，不重复前端构建／Chrome或全量业务测试。
- 保留上游MCP/Pydantic `lifespan` 未解析前向引用警告，以及既有Starlette/httpx弃用、SQLite ResourceWarning；受控模型／Judge／熔断／ConnectError不是实时服务故障。准备时本地清单测试脚本曾因系统Python缺tomllib及括号错误失败，已改用明确3.13并修脚本；不是业务或上游缺陷，未删除原断言。

证据目录：`/Users/zijing/projects/trippilot-evidence/echomind-core-stage1-20261023.6yzijT/`，保存必要哈希、安装／检查结果与边界说明，不保存Key、私密请求或原始媒体。原五缓存／两SQLite／额度锚8哈希与历史保护清单相同，原EchoMind仍干净。无浏览器／真实语义验收，不启动或重启现有服务。源码提交不等于云端或本机当前入口已切换。

## 7. 后续步骤与明确停止点

1. **本步：核心保真＋千问桥接＋离线对照。** 验收代码没有被削成简化类、真实协议转换和计数边界；不切换产品。
2. **依赖与安全适配。** 原SiliconFlow直连须换成明确的千问Embedding路径；Chroma模型下载／索引、Redis、MCP传输须受配置、隔离、额度与资源治理。原日志有查询／参数／异常泄露风险；原开发身份fallback不能接入当前产品。Judge重试／Token和多Agent合成与共享总预算冲突须显式设计，不静默扩额。
3. **旅行领域接入。** 新旅行意图、专业Agent、Skill、真实资料与工具注册；MySQL继续持有权威业务／任务数据，缓存和向量索引不能成为另一套互相冲突的主数据。地点确认、预览／采用和保存必须沿用现有提交边界。
4. **固定效果验收后切换。** 简单问题、缺信息澄清、多步协作和不能回答分别测；以现有单步方案作对照。真实调用必须另获明确额度，不借旧台账、不自动开放知识库问答。三个简洁页面不变。

当前停在第1步。后续步骤不是本次已经完成，更不是新的真实调用／部署授权。也不声称现有网站已经具备完整EchoMind能力。

## 8. 面试解释与Harness

**口述**：最初只借鉴框架组织方式，没有完整保留能力。这次把原核心作为可验证基线迁入，不重写算法；通过独立千问适配层转换原生工具协议，并保留任务和持久额度。原测试证明机制没丢，受控桥接证明协议可通；旅行效果、存储和生产安全另行准入，不能拿类名或测试数量代替业务质量。

- **为什么要命名空间隔离？** 原框架与当前项目都叫core／agents等；不隔离会串类、串存储和配置，也无法安全作行为对照。
- **为什么不直接复制requirements？** 原SDK、旧Web依赖与当前Python／FastAPI不同；只引入选装核心依赖，原应用依赖版本不漂移，原生模块仍须目标平台验证。
- **保真如何证明？** 原哈希＋确定性变换重建＋原断言；后续必要补丁必须有独立版本和理由，不能偷偷删失败边界。
- **多Agent为什么不自动更好？** 分流、召回和合成会增加调用／延迟；共享预算可能中止任务，必须对照最终任务完成与正确性，不以Agent数为成绩。
- **为什么还不能直接上线？** 原日志、自动评分重试、直连Embedding、默认身份和存储生命周期尚需适配；本步刻意不暴露HTTP入口。

Harness交付卡：H01范围／H02来源版本／H03协议／H04预算／H08安全／H09计数／H11受控评测／H12隔离与依赖有本步代码检查；H05旅行正式提交、H06记忆同意、H07持久任务恢复及H10完整运行监控保留为后续准入，不能用原类存在判为通过。模型及外部业务API调用均0。

## 9. 第2步：依赖、计数与安全组合（2026-10-23）

### 9.1 范围与实际数据流

开发基线 `main / cc38d0e99a344abf186e7f8cfc5db7692c71344f`。用户要求仔细实施第2步，本轮只处理独立Redis／Chroma、千问Embedding与原核心的安全组合。**没有旅行角色替换、HTTP开放、前端修改或产品切换；当前3110／8804及旧服务未重启，MySQL和原数据未迁移。** 真实模型、高德及外部业务API调用均 **0**。

组合入口为 [runtime.py](../backend/echomind_core/runtime.py)：显式 `StorageConfig`＋服务端owner／会话／独立session／单调input_version → 私有Redis与Chroma → 受限本地只读检索工具 → 原编排器。打开资源不请求Embedding、不导默认文档、不生成画像。调用必须同时绑定当前执行门禁和已有持久额度上下文；owner与conversation路径不符，即便只是读缓存也拒绝。模板索引、工作缓存写入是可信调用方显式操作，不是模型可执行的写工具。

| 文件／能力 | 本轮适配 | 未实现或不作为本轮结论 |
| --- | --- | --- |
| [embedding.py](../backend/echomind_core/embedding.py)、provider.py | 固定千问 `text-embedding-v4`／1024维，显式异步向量，严格索引／维度／有限数／JSON校验，与聊天共享计数和截止 | 未真实发送或衡量中文语义检索；未开放自动模板准备 |
| [storage.py](../backend/echomind_core/storage.py) | 真Redis工作缓存与版本屏障、真Chroma知识／情景／明确同意偏好索引；复用原分块、更新、检索与上下文组装 | 缓存不是MySQL主数据，不是完整任务断点恢复；不自动猜偏好或从模型正文提取同意 |
| [execution.py](../backend/echomind_core/execution.py) | 原融合／路由／并行／合成及检索链外包裹可信工具、请求捕获、预算、作用域、取消与单次Judge | 原客服角色／话术尚未适配旅行，不能直接把生成正文作为可靠旅行事实 |
| [diagnostics.py](../backend/echomind_core/diagnostics.py) | 原候选日志命名空间进入空sink；新事件只允许阶段／枚举码／数量／耗时／固定模型 | 不是完整生产监控平台；可信注册代码仍不可自行安装泄露正文的新日志handler |
| core/usage_runtime.py | 仅合法assistant-turns上下文中的北京官方 `/embeddings` 计入原 `qwen_text` | 不新增／重置业务额度；次数不是人民币费用上限 |

`upstream/**`、`SOURCE.json`、原测试及原Skill **0修改**，原算法仍可作对照；本轮是明确的适配层，不是删掉原实现后写同名简化类。选装依赖和锁文件未改变，复用第1步的独立Python3.13环境；正式venv未安装新包。

### 9.2 存储职责、持久性与资源边界

- **MySQL仍唯一权威**：用户同意、会话、业务任务、存档和额度不转交Redis／Chroma。当前候选只接受可信调用方传入的来源版本，内部一致性不等于已查询MySQL核验；与现有MySQL读取／提交的自动接线属于第3步。
- Redis只连当前用户私有目录的Unix socket；读取配置核验 `port=0`、AOF开启、`appendfsync=always`、`noeviction`、正数且不超过64MiB的maxmemory、私有数据目录。不设置已有服务、不连默认6379、不回退其他库。AOF重启仍保留版本／撤回屏障；故障时失败，不当作空画像继续。
- Chroma为本地 `PersistentClient`，不是新增网络服务。显式向量且 `embedding_function=None`，禁默认下载与种子内容；注入无导出的遥测组件，不只依赖“关闭匿名遥测”布尔值。namespace＋owner哈希、存储版本、模型和维度绑定集合与强制过滤；不匹配拒绝复用。
- 每owner最多知识128片段、情景128条、偏好32项（含pending／撤回屏障）；工作缓存64会话（含非过期版本屏障）／每会话20条消息；输入与结果正文／上下文状态投影64KiB，非整个Python对象的线缆序列化承诺。工作正文TTL为1～86400秒，版本屏障不随正文过期，撤回／正文过期不自动释放身份容量。生产清理须另定权威版本与保留策略。原500字符主题分块保留；一次最多10个不同文本，不自动拆成额外付费批次。
- 显式用户偏好写入与撤回使用Redis持久pending／generation和Chroma版本交叉检查。中断或结果不明会阻止读取／同版重放，须从权威状态明确协调，**不是Redis与Chroma跨库事务**。撤回是当前检索逻辑失效，不宣称物理擦除WAL或备份。
- Chroma同步操作在线程执行，取消后等待实际退出再释放Redis租约；重复取消不遗弃线程。`aclose()`排空操作并关闭Redis连接，不调用共享Chroma私有 `_system.stop()`伤害其他owner。Chroma0.5没有公共close，原生句柄随隔离进程退出释放；因此长期服务的进程生命周期、磁盘容量／清理、备份与Linux低内存验收仍是开放前门槛。

### 9.3 发送、取消、预算和原机制的取舍

Embedding采用官方[当前规格](https://help.aliyun.com/zh/model-studio/embedding)及[OpenAI兼容接口](https://help.aliyun.com/en/model-studio/embedding-interfaces-compatible-with-openai)：每批最多10条；本地进一步限定单条8000字符且8000 UTF-8字节、整请求48000字节、响应2MiB、单次20秒／整任务45秒。字节上限不是Token用量或价格保证。返回索引／数量／非零有限向量不符就拒绝，不截断、补零或切换本地模型。

模型、Embedding、改写、重排、子Agent、合成和Judge共用**最多3次模型／4次实际工具／45秒／6144最大输出Token预约**。Embedding每批占1次模型与1次持久`qwen_text`尝试，输出Token预约为0不代表免费；实际输入用量未知就记未知。实际传输前原子持久预记，失败、取消、dispatching不确定均不返额；传输无自动重试或重定向。Judge只尝试1次、最多1024输出Token；非法评分保留未评分，不修复循环。

这里存在实际成本约束：原54条意图模板冷启动至少6批Embedding，再加查询，不可能塞进3次任务。现在先核验全部文本／预算，**发送前报 `embedding_preparation_required`**；支持有owner／revision／模型／维度／指纹的显式已准备向量加载，但本轮未调用模型生成这些向量。即便已准备，完整融合的LLM＋查询Embedding已占2次，Agent首答占第3次；再做工具反馈、改写或合成可能明确超限。组件能力保留不等于所有复杂组合都能在3次内完成，后续按旅行任务选择必要路径，不能暗加额度。

原 `learn()` 改全局模板，有跨用户污染风险；安全候选暂拒绝该入口，原算法不删。需要显式owner隔离、审核和模板版本发布后才能启用。远端MCP原SDK／测试保留，但本候选只注册可信有限的本地只读工具，不接受模型URL、任意代码或副作用；标为read-only是注册者责任，不是程序能证明任意函数绝无副作用。

原始Request在首个await前白名单深拷贝；缓存绑定owner／conversation／session，输入版本只增不退，A→B→A不恢复旧资格。失效会取消模型／工具和直接存储准备任务，发送后取消不能保证供应商停止处理或计费。同步工具取消也须等线程退出。原框架捕获异常后的词法／检索降级，不得掩盖任务预算或模型错误成为假成功。

### 9.4 本机复现与配置（不切换现有服务）

本机已存在 `/opt/homebrew/bin/redis-server`，检查版本为8.10.2；未安装或重配系统服务。每项存储测试在私有临时目录启动独立Unix socket Redis，结束时停止；Chroma使用另一私有子目录。测试实际连接、AOF重启、独立进程重新读取索引，但所有向量及聊天HTTP均受控，不代表真实语义效果。

第1步独立venv安装命令仍适用。下列为**离线验证**，不会调用付费模型；需本机已有Redis可执行文件，缺失明确跳过／不可用，不自动安装：

```sh
cd backend
# ECHO_ENV 指向按第5节创建的仓库外专用venv，不能使用运行服务的venv替换它。
"$ECHO_ENV/bin/python" -B tools/echomind_core_manifest.py
"$ECHO_ENV/bin/python" -B -m unittest tests.test_echomind_core_manifest tests.test_echomind_candidate_boundary tests.test_echomind_core_behaviors tests.test_echomind_provider tests.test_echomind_embedding tests.test_echomind_execution tests.test_echomind_storage tests.test_echomind_runtime -q
"$ECHO_ENV/bin/python" -B -m pytest echomind_core/upstream/tests -q -p no:cacheprovider
uv lock --check --offline
```

如需开发独立存储，以下是非敏感参数示例，**不是正式应用配置或自动启用开关**。用`mktemp -d`新建私有目录并以`pwd -P`得到真实绝对路径；不使用现有数据路径。Redis前台参数为：

```sh
ECHO_ROOT="$(mktemp -d /tmp/trippilot-echo-store.XXXXXX)"
ECHO_ROOT="$(cd "$ECHO_ROOT" && pwd -P)"
redis-server --port 0 --unixsocket "$ECHO_ROOT/redis.sock" --unixsocketperm 700 \
  --dir "$ECHO_ROOT" --appendonly yes --appendfsync always \
  --maxmemory 64mb --maxmemory-policy noeviction --save '' --logfile "$ECHO_ROOT/redis.log"
```

可信组合层显式传入 `StorageConfig("echo-stage2-lab", Path(root)/"redis.sock", Path(root)/"chroma", "text-embedding-v4", 1024, ttl_seconds=3600)`；模型沿用既有后端配置和北京地址校验，不新建Key、不修改环境文件。手工调用仍须明确真实调用授权、现有持久owner上下文及执行门禁；启动本地Redis或产品有余额并非模型调用许可。测试会自行准备和回收资源，不需要先运行这个示例。

### 9.5 本轮检查、证据、停止点

最终检查结果见PROGRESS最新摘要；证据目录 `/Users/zijing/projects/trippilot-evidence/echomind-core-stage2-20261023.FoAscI/`，保存检查结果和保护哈希，不提交临时库／模型正文／日志。范围分为真实本机存储、受控HTTP／算法、现有应用回归，互不冒充真实模型或Chrome效果。未改前端，不重复构建或浏览器验收；本轮未启动／重启当前产品或云端。

**停止于第2步依赖与安全候选，不进入第3步旅行意图／Agent／Skill接入或正式切换。** 真实Embedding语义检索、当前中文旅行意图／答案、多Agent收益、生产磁盘／备份／隔离与远端MCP仍待分别设计和验收。知识库生成问答保持停用，所有旧失败／台账和调用额度保持独立。

面试简述：完整保留原引擎后，不直接让它拿环境变量任意发请求；由受信任组合层把身份、输入版本、工具权限和同一份任务预算注入。MySQL管事实与额度，Redis管可丢的工作缓存及一致性屏障，Chroma管可重建索引。原测试验证机制保真，真实本地存储验证隔离／重启，MockHTTP验证协议与计数；三者都不能证明旅行回答质量。

- **为什么不直接开放原始类？** 原自动下载、直连、全局学习、日志及评分重试不符合现有权限／费用边界；适配包裹而非删除原算法。
- **Redis重启为什么不恢复额度？** 硬额度仍在已有持久存储，Redis不是额度库；AOF只保护候选缓存的版本／撤回屏障，不提供新调用许可。
- **并行为什么不等于免费加速？** 所有子任务共享3次上限，实际发送前竞争同一持久额度；预算不足就停止，不看哪个先成功来拼答案。
- **取消为什么要等线程？** Python取消不能杀掉已执行的同步Chroma操作；提前释放租约可能让旧写覆盖新写，必须等回收后再放行。
- **还有哪些未证明？** 真实语义质量、偏好被旅行约束满足、复杂链在预算内完成以及生产长生命周期；未将受控测试写成已经上线或全面智能。

Harness本轮：H01～H04范围／版本／契约／预算、H06同意／撤回机制、H08隐私／权限、H09持久尝试、H11分层受控检查和H12选装依赖均有具体测试；H05旅行正式提交仍用旧产品、H07完整任务恢复和H10生产追踪／告警未因此完成。原版权许可缺口与第1步归属保留。
