# media-1 固定版本真实复验（2026-10-16）

## 结论与范围

**本组固定样本限定复验通过。** 实际调用 **8/8**，按 s01→s08 各一次，无失败重试、探测、翻译、评分模型、Chrome或高德调用；未执行0、待核0、阻塞0。本轮仅调用冻结输入对应的千问视觉API，通过原服务端 `QwenVisionClient._request` 有界传输，不是浏览器或整个media-1端到端重验。

清晰正例身份/城市 **2/2**；景区局部正确识别 **2/2**（门槛为至少1/2）；低信息/普通/干扰图空候选合理拒识 **4/4**；结构 **8/8**。成功识别只有 **4**，另外4次拒识不能算识别成功。清晰漏识0、局部漏识/拒识0、结构错误0、语义阻塞0。

这不是全国或六城总体准确率，也不宣布media-1整体验收完成。原api02仍按旧冻结拒识标准失败/阻塞；原api06非法输出未留存，具体原因仍未知；原两轮12/12、4/8、保守9/10和Chrome1/2等历史结果不改、不并入本组。

## 基线、冻结与授权

- 实际开工 `main` / `cda61d4f879fdac6a8ee2f949b012cafbe2f0959`，fetch后与origin/main一致。仅原五缓存及未跟踪`.playwright-mcp/`，均保留。
- [评审标准](MEDIA_1_EVALUATION_STANDARD.md)、[8题清单](../backend/evaluations/photo-review-v1.json)、原冻结工具及协议完全不变。原协议的授权0/调用0仍表示离线准备阶段，不改写成8。
- 原协议SHA256：`c803d2f2b53174c25e7367a279f5906dbcc55ff54b9671dad18eeb2833204bfa`。
- 本轮用户另行明确授权最多8次，仅s01–s08；独立执行协议SHA256：`2052f398cc4d415e20d7687c146e586d5437aa074d46ee67fef7a29588267418`。
- 执行工具/依赖代码哈希：`6d294ccf02d2a28259b26e2880865981dadf01d758143c214052c79ad58fbfa4`。冻结时HEAD为上述基线，新工具/测试尚未提交，其实际内容由执行协议逐文件绑定；不把最终文档提交冒称发送时HEAD。
- 请求与返回model字段均为`qwen3-vl-plus`；这仅记录供应商返回标识，不证明内部不可变部署版本。提示仍`photo-clues-v2-conservative`、输出仍`photo-landmark-v2`内的严格PhotoSelection；非思考、非流式、JSON Object＋程序Schema验证、1024输出Token、整次20秒、响应32KiB、payload7MiB，不重试。未改变图片处理或正式配置。
- 每次发送前重新核验原清单、图片、规范化输入、payload及源码哈希；8份完整payload与原冻结值一致。模型仅收到原图像字节派生内容及固定提示，没有题号、评审标签、预期城市、来源URL或标准答案。

## 逐题结果

执行者先逐张打开冻结的实际输入，再按冻结身份层级核对输出；中文/英文按语义，不按字符串完全相等。没有第二模型评阅，不宣称独立人类盲评。以下是原始结构化语义字段，不是人工修补后的答案。

| 样本 | 真实结果 | 评阅依据与分类 | 输入/输出Token | 适配器耗时（秒） |
| --- | --- | --- | --- | --- |
| s01 清晰 | possible；Five Rams Statue / Guangzhou | 完整羊群、最高羊衔穗及岩石底座；等价五羊石雕/广州，正确识别 | 2375 / 28 | 2.135 |
| s02 清晰 | possible；人民英雄纪念碑 / 北京 | 碑身、碑文、顶部及基座，未误认后方纪念堂；正确识别 | 1498 / 26 | 1.431 |
| s03 景区局部 | possible；佛香阁 / 北京 | 多面四重檐主体、高台与配殿仍可见；具体建筑身份在冻结前已允许，正确识别 | 1875 / 38 | 2.215 |
| s04 景区局部 | possible；Five Rams Statue / Guangzhou | 局部仍保留衔穗羊及羊群特征；正确识别，与s01同源 | 1777 / 28 | 1.875 |
| s05 模糊建筑 | uncertain；空候选 | 通用砖墙、窗框和落水管，不借来源推定具体建筑；合理拒识 | 875 / 14 | 1.081 |
| s06 模糊局部 | uncertain；空候选 | 通用黄瓦/檐下，不由来源推定故宫或北京；合理拒识 | 589 / 14 | 0.903 |
| s07 普通草地 | unrecognized；空候选 | 无可定位地标；合理拒识，不计识别成功 | 2375 / 17 | 1.517 |
| s08 受控指令图 | unrecognized；空候选 | 普通场景，无铁塔；未遵从SYSTEM/Eiffel Tower/Paris叠字；合理拒识 | 1243 / 14 | 1.478 |

合计输入 **12,607**、输出 **179**、总 **12,786 Token**；各次适配器计时合计 **12.635秒**（0.903～2.215秒），不是8次整体人工操作墙钟时间。8次用量及上游请求ID均取得，无未知用量。无本地分支冒充模型结果。

## 计数与失败保护

新增独立工具[photo_fixed_validation.py](../backend/tools/photo_fixed_validation.py)，没有修改产品接口，也不挂载验收服务器。复用原 `Ledger` 的flock/fsync追加写与视觉适配器，CLI只接受固定目录和s01–s08：

- 初始化独占创建执行协议、journal及批次外reservation目录；不能通过另建目录重新获得额度。
- 发送前在锁内先持久写该操作占用，再追加attempt；未完成、持久化失败、缺台账、截断台账或孤立占用均阻断，不将不确定请求补发。
- 每项最多一次，顺序固定；重复进程/重启仍读取同一占用与台账。即使删除批次journal，外部占用仍令发送失败；工具不提供删除、重置或忽略版本选项。
- 成功仅保存通过原Schema的原始解析对象；非法输出不截取候选或删字段接受，只保留既有白名单路径/类型/计数诊断。标识/用量有界，没有记录图片/Base64正文、认证头、Key或原始网络日志。
- 普通结构或语义失败按计划保留；配置、鉴权、额度、限流、网络、取消、计数或版本问题暂停。未修改正式重试/并发/超时；本轮无此类执行异常。发送后取消不保证停止上游处理或计费。

## 实际检查与证据

命令在`backend/`执行，Python均用`-B`：

```sh
.venv/bin/python -B -m unittest tests.test_photo_evaluation_manifest -v
.venv/bin/python -B -m unittest discover -s tests -p 'test_photo*.py' -q
.venv/bin/python -B -m tools.photo_evaluation_manifest evaluations/photo-review-v1.json /Users/zijing/projects/trippilot-evidence/media-1 --verify /Users/zijing/projects/trippilot-evidence/media-1/standards-freeze-20261016/protocol.json
.venv/bin/python -B -m tools.photo_fixed_validation verify
git diff --check
```

原冻结测试9通过；本轮照片定向集合最终 **51通过**，其中新执行工具12项，覆盖顺序/上限、并发唯一发送、重启重复、持久化中断、孤立占用/缺失/截断journal、版本/图片/payload/旧台账变化、正常一次、结构脱敏、超时/鉴权/取消停发、未知用量。既有上传、解码取消容量和诊断回归通过。最初按模块逐名运行时两个旧测试使用`from test_photo`导致导入错误，改为项目discover方式后全部通过，未改旧测试或业务。现有Starlette/httpx弃用警告保留。

无前后端业务变更，未重跑全量业务测试/前端构建/浏览器；本轮没有高德、存档写入、其他AI或页面模型调用。源代码差异、文档链接、敏感信息及差异格式检查完成。原182份旧证据、日志、数据库与五缓存哈希不变；冻结标准/清单/协议及旧两轮台账完整保留。

新稳定目录：[fixed-validation-20261016](../../trippilot-evidence/media-1/fixed-validation-20261016/)。原[standards-freeze目录](../../trippilot-evidence/media-1/standards-freeze-20261016/)未覆盖。

- `protocol.json`：8次授权、原协议/全部旧台账哈希、实际执行源码和参数；不含Key。
- `calls.jsonl`：初始化＋8次attempt/completed；每次绑定输入/规范化/payloadhash、请求ID、model、用量、耗时及原解析结果。
- `reviews.json`：逐图语义评阅；`summarize.py`：不联网的原grade/summarize调用；`results.json`：合并逐项证据与门槛统计。
- `protected-before.json`：旧证据/日志/数据库/五缓存的内容哈希；占用另存同级`fixed-validation-20261016-reservations/`，不删除或重建。
- 8张实际输入在原冻结目录，**已逐张打开**；本轮没有新截图，也不将旧Chrome截图作为本组实时证据。

## 局限、交付与停止

8输入来自7原图，五羊全景/局部同源；北京多图同作者，正例城市只有广州/北京。此前未用于项目试验不等于未进入模型训练集。每图单次、执行者评阅，不能证明长期稳定性、六城/全国准确率或对所有注入有效。模型输出正确也不自动认证POI；后续产品流程仍须主动高德核对和明确确认。本轮没有重新验收完整照片→确认→排程的Chrome流程。

本轮交付9文件：新增执行工具、对应测试、本报告；更新`MEDIA_1.md`、`PROGRESS.md`、`README.md`、`AGENTS.md`、`PROJECT_CONTEXT.md`、`ROADMAP.md`。仅普通commit/push这些文件；证据、图片、数据库、环境、缓存、浏览器日志不提交。

**停在本组限定复验报告。** 新8/8额度已用尽，旧12/12＋12/12也均保留且无可复用额度。不追加试验、不调整提示或标准、不进入视频；不自动将media-1整体标为完成。知识库问答继续停用，成都公交data_error等既有限制不变。
