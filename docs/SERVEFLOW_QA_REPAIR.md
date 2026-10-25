# ServeFlow 实测、补修与尚未完成的验收

2026-10-25，实际基线 `main / c207267350498aed55d730528fc229d9e1765308`。对象是本机5173的**智应 ServeFlow 客服项目**，不是3110的TripPilot旅行入口或云端。用户要求先实际试用、找问题、修复并解释；本轮另明确授权最多20次千问物理发送，四个固定问题共用计数。

## 找到了什么、怎样定位

| 问题 | 核验依据 | 本轮处理 |
| --- | --- | --- |
| 失败时页面显示整个后端JSON，丢失失败状态／会话身份 | 实际`requestChat`代码及受控503重放；旧normalizer丢弃status/retryable/error_code | 严格接收结构化失败，保留会话／操作绑定，仅显示错误码白名单固定提示；未知错误不显示原始正文 |
| 模型已经回答，页面仍等执行记录 | 原sendMessage先await trace GET；受控浏览器悬住trace即可复现 | 先发布回答／结束输入忙状态，trace独立补充；换会话、身份或卸载取消并隔离晚响应 |
| partial被当成完成 | 原normalizer及Inspector未区分 | 保留并明确显示“部分完成”；failed显示“未完成” |
| 快捷退款问题默认`#12345`，演示工具只接受`DEMO-`加4数字 | UI与工具Schema直接对照 | 从当前演示身份可读目录产生查单／退款示例；无演示数据时不编订单号，点击仍只填框 |
| 坏工具批次可以执行 | 纯Mock复现：`input=[]`被`or {}`修补；缺ID、截断工具结果仍派发 | 执行整批前检查参数为dict、ID合法／唯一及非截断；无效整批不执行，不修补模型答案；纯文本截断仍partial |
| 旧失败只有空白异常，无法辨认阶段 | 旧日志29次intent、3次Agent、2次Composer异常文本为空；旧trace约15秒／30秒 | 固定阶段／错误码／白名单异常类型／有界耗时，不记录异常原文、请求响应、私密数据或思维链。**旧失败具体根因仍未知** |
| 当前实际分类超过8秒截止 | 本轮第二个真实场景：intent.transport、TimeoutError、8003ms；传输预记后取消 | 保留失败和不确定计费，按协议暂停后续真实调用；不把总503直接归为MySQL或工具故障 |

旧schema失败只留下pattern拒绝，**无法判断旧实参是否就是`#12345`**。默认示例不匹配是已确认的UI问题，不能倒推历史请求。监控周期性重复告警也不能按行数当新失败次数。

## 固定真实批次：4/20发送后暂停

执行证据：`/Users/zijing/projects/trippilot-evidence/serveflow-qa-20261025.K2kVve/`。

- 正式冻结`protocol-live.json` SHA256：`7c2890757dd7ed7a84ee341e03c3939529531b62aafe0c2e7220664d771c71d5`。54份源码／资料／Skill哈希、生产前端产物、模型及现有参数、四题和预期在发送前固定。
- 独立8817后端、5174生产预览、私有Redis／Chroma／演示签名会话；只读同一合成MySQL库，不混入用户8816既有对话。分类、Agent、检索、Composer、后台记忆全部经过同一个物理发送计数器，SDK自动重试关闭。
- `journal-live.jsonl`发送前flock＋fsync＋哈希链预记；每题一次，失败／已发取消／未知不退额，版本／网络／身份／计量异常暂停。重启不能重开未结场景，也不能绕过20上限。只复用旧台账的标准库原语，未使用旧额度。
- 两份**0发送的预检记录**原样保留并封口，原因是验收启动器Python namespace导入冲突及最终安全文案补齐；正式记录关联其哈希，仍是本轮同一个20次授权，未恢复任何已发送额度。实际服务import离线核对后才开始发送。

| 场景／原冻结问题 | 真实结果 | 物理发送 | 业务判断 |
| --- | --- | --- | --- |
| greeting：你好 | 200／success，页面及时显示问候；分类、General、后台画像各1次 | 3 received | 问候通过；另发现仍自称EchoMind，作为品牌文案问题处理 |
| split_order：请查询演示订单 DEMO-1001：每件商品分别发货了吗？还有未发货的吗？ | 分类8秒截止，已发送请求取消、结果未知；保护随后阻止Agent发送；页面503清楚标失败，无原始JSON | 1 unknown | 失败，查单工具没有执行，不能宣称真实查单通过 |
| refund：请查询演示订单 DEMO-1004 的退款进度，退款金额是多少？ | 按执行异常停止规则未发送 | 0 | 未执行 |
| policy：组合订单的设备和配件会分开发货吗？数字订阅有快递单号吗？ | 同上 | 0 | 未执行，不能宣称RAG真实回答通过 |

累计**4/20**，3 received、1 unknown；剩余16处于暂停状态，不能自动续发或重跑。三个完成响应的已知用量：输入2536、输出1587 Token；第4次用量及上游结果未知，不能当0。物理等待合计26912ms含后台处理，不能当用户端耗时或人民币费用。用户端问候13812.7ms、拆单失败8112ms。上游前三次返回模型标识与请求Flash快照相同，仅是响应标识，不认证内部部署。

原台账末条链哈希：`ca0e0013755b97bf5e48d9524702a109eeb334423ef0873d485c9ef8c4d39570`。`end/finished`只表示场景封口，不是通过评分。没有自动补测、连通性探测、评分模型、高德或其他业务外部调用。

## 暂停后的离线兼容修复

另一个已核实的接入缺口：原代码没有明确选择思考模式；[百炼深度思考文档](https://help.aliyun.com/zh/model-studio/deep-thinking)列明当前Flash快照默认开启思考，[Anthropic兼容文档](https://help.aliyun.com/zh/model-studio/anthropic-api-messages)支持`thinking.type=disabled`。这与现有低延迟8秒／15秒截止不匹配，可能增加等待；**本轮未知结果没有原始响应，不能断言每次超时都由思考导致**。

新`core/provider_options.py`仅对官方北京固定Messages地址＋精确`qwen3.7-flash-2026-07-15`返回非思考参数，接入分类、Agent、Composer、检索改写／重排、记忆及既有Judge路径。其他模型或地址保持原参数；没有换模型、扩大截止、改温度／Token／工具权限或关闭三路融合。各实际SDK构造禁自动重试，避免一次请求隐性多发。Judge未执行。

实际安装锁定Anthropic SDK0.40.0不接受顶层Python关键字`thinking`。定向SDK测试发现这一点后，使用其支持的`extra_body`透传，实际HTTP仍是官方顶层`thinking`对象；MockTransport捕获完整native两轮请求验证，不升级依赖，也不把普通AsyncMock接受任意关键字当兼容证明。

模型身份文案及三个Skill的角色行同步为“智应 ServeFlow”；原作者归属、框架类名和历史输出不改。暂停后的这些改动**只经离线payload捕获／回归验证**，没有重判真实问候、重新发送拆单或冒称最终版本真实质量通过。旧冻结应正确拒绝当前改动，不更新旧哈希。

## 检查、浏览器与适用边界

正式frontend实际为Vue／Vite，不是Next.js。本项目没有lint／typecheck脚本，不虚构这两项检查。

- `cd EchoMindFrontend && npm test`：最终54项通过；`npm run build`生产构建通过。
- `cd EchoMind && <原客服venv>/bin/python -B -m pytest -q -p no:cacheprovider tests`：最终**315项通过、无跳过，6.22秒**，含独立合成MySQL的3项；测试库连接只在私有进程环境传入。较早275项为增加provider兼容检查之前的中间结果；312通过／3跳过是禁网分支，不能相加。未调用真实模型／业务上游。
- `cd backend && <原客服venv>/bin/python -B -m pytest -q -p no:cacheprovider tests/test_serveflow_live_ledger.py tests/test_serveflow_qa_transport.py`：37项通过，包括独立进程／线程竞争、重启、上限、fsync失败、版本／结构篡改、未知结果、非200及证据写入失败。
- 真实Chrome：本轮固定模型问候、拆单超时失败展示及400px（scrollWidth=400）。截图`greeting-desktop.png`、`split-order-desktop.png`、`split-order-400.png`逐张打开。
- **独立受控Chrome**：4个mock截图验证trace悬住仍显示success／partial、503无原始正文、新对话后晚响应不回写；400px无溢出。全部API被受控拦截，不能记作新增模型成功或真实故障。
- 五个原缓存哈希与既有值一致；数据库未迁移、未改用户／演示业务记录。MySQL演示测试在独立test库事务执行。旧证据、日志、评分与台账均保留，不公开原始浏览器日志、Cookie、凭据或媒体。

## 交付、复用与停止点

实际嵌套源码已在本机修改；主仓交付[14文件后端增量补丁](../patches/serveflow-qa-backend.patch)和[4文件前端增量补丁](../patches/serveflow-qa-frontend.patch)。后端在`serveflow-knowledge.patch`＋`serveflow-demo-backend.patch`之后，前端在品牌＋演示＋监控补丁之后应用。先在干净同基线副本`git apply --unidiff-zero --check`；当前已应用目录只做`--reverse --check`，不能重复应用。原作者两个Git目录／remote保留，不向其仓库推送。

本轮可确认的程序／接入问题已补修；**完整真实查单、退款、RAG语义验收仍未完成**。没有宣称任意输入可靠、企业生产接入或全国业务支持。停止本轮，不动TripPilot旅行／云端入口，不沿用暂停16次自动追加试验。

30秒口述：我先把错误按页面、协议、模型传输、业务数据分层，使用Mock确认展示与工具派发缺陷，再用独立计数做真实验证。实际看到分类在8秒截止，于是保留失败并暂停，而不是循环刷成功。随后补显式非思考接入和安全诊断，离线检查不伪装成真实模型提升。

可追问：

1. **如何证明不是数据库问题？** 真实失败发生在分类阶段，工具trace为空、后续发送被保护拒绝；没有执行查单。
2. **为什么先校验整个工具批次？** 后半个block非法时，不能让前半批工具已经执行；用handler零派发断言验证。
3. **取消为什么仍计费占用？** 本地取消不证明上游没收到／没处理；台账保留unknown，不退额或重试。
4. **结构通过就说明回答正确吗？** 不说明；退款金额null、拆单状态、政策适用性须独立核对，本轮相应真实题未通过／未执行。
5. **怎样证明改参有效？** 已有SDK MockTransport捕获确证payload与重试设置，尚无修复后真实延迟或语义证据，不能提前宣称解决全部超时。
