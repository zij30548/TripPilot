# 目的地扩展第 1 阶段

实现基线：`7401083204a3161878af83b95384d7aa17bd229c`，main。本文件区分离线验证与真实验收，不代表全国开放。

## 契约与边界

- `shared/destinations.json` 是前后端共同使用的受控城市表。上海 `310000 / 021`、杭州 `330100 / 0571`、北京 `110000 / 010`；区县必须在所属城市的明确列表中。依据[高德行政区编码下载页](https://lbs.amap.com/api/webservice/download)的 2026-01-07 表核对；公开 ZIP SHA256：`a5f190378c7dd5e592b0e95ca81ae7d454116293d5ac77472f96c20d2fa184e3`。未来区划变更需重新核对，不做前缀猜测。
- 正式需求携带 `destination_id`，确认地点额外保留 `region: { citycode, cityname, adcode }`。搜索依据[POI 2.0 文档](https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch)，使用选定 adcode 与 city_limit，核对实际返回字段；查询参数本身不是归属证明。有效坐标 POI 若归属缺失、跨城或矛盾，整次逻辑搜索明确失败，不补字段、不追加详情请求。客户端后续提交只是快照结构／归属一致性检查，不是重新认证上游真实性。
- 原 URL 及旧上海契约保留供旧调用方和 `/demo`；现代请求中的非法城市／缺失归属不能降级为旧请求。正式表单必须明确选城，新工作区不接受无城市输入。
- 公交复用 v3 适配器，仅按已校验目的地设置 city/cityd；纯排程仍消费原规范化分钟，不改变策略或调用预算。天气一次按城市 adcode 获取整批，并逐日期匹配；身份不符失败、无覆盖留空。Asia/Shanghai 是三城共同的时区，不是目的地。
- 地图只增加这三城的行政中心查询白名单；固定上游、方法、路径、层级、Key／安全码及其他参数约束不放宽。`/demo` 仍为上海。资料检索仍仅覆盖上海三馆区，知识库 AI 问答继续停用。
- 新存档 `saved-trip-v2` 增加明确目的地和地点归属，使用原 JSON 存储表、2 MiB 限制及幂等机制。显式投影到不变的 v1 核心进行同算法重放，公交数据保留、步行附件仍不入库。v1 读取与内容不改写；继续规划时依据旧产品语境标为上海，但缺少归属的旧住宿／必去需重新搜索确认，旧可选不作为有效排程输入。
- 城市在表单编辑副本中变化即清空该副本地点与搜索会话；取消保留原工作。确认后同步失效并重建独立工作区，清空候选、排除、草案、天气、预览、AI、地图和费用。日期、人数、预算、兴趣等保留。地点搜索与天气均保留 Abort＋版本隔离；本轮模型调用为 0。

## 真实验收计划（在首次业务查询前固定）

每城以 1 个住宿参考地标＋1 个不同必去 POI 起步；名称不是预置坐标，均从真实搜索明确确认。预计上海静安寺／武康大楼，杭州武林广场／浙江省博物馆， 北京北京饭店／天坛公园；实际候选与入口以页面地址核对，不把错误候选当成功。

| 场景 | 计划与上限 | 观察 |
| --- | --- | --- |
| 三城地点、步行、公交、天气、地图 | 每城 2 次 POI 搜索、1 次步行生成、1 次公交生成、1 次天气查询；关键词歧义最多每城另 1 次搜索 | 三城各 2 搜索＋步行／公交各 1，通过；杭州首次天气错误及修复后额外 1 查询单独保留 |
| 杭州候选、调整、保存与副本 | 1 次摄影候选获取；最多 4 次生成（含预览／重生成），1 次保存，历史副本最多 1 次生成 | 1 候选、候选草案＋2 预览；取消／采用无请求；从 v2 副本主动生成公交；为重建前保留输入额外保存 1 次，共 2 新记录 |
| 编辑取消／确认、日期与空日、400px、Marker | 本地操作不增加业务查询；必要重新确认复用上述预算或明确记录 | 杭州→北京取消保留、确认清空；跨日清标记、空日 0 Marker、双向定位及 400px／Enter 全貌通过 |
| 旧 v1 只读与继续规划、/demo | 读取不写旧记录；仅确认旧输入被阻止，地图回归最多 1 次旧上海搜索，无模型请求 | 旧 v1 只读 200／原时间不变，副本缺归属禁生成；/demo 1 搜索＋主动 Mock plan，上海真实底图通过 |

地图 SDK／行政中心／底图请求与业务请求分别统计；浏览器本地数量不能证明上游每次尝试。每次排程仍最多 17／29 逻辑边、34／58 HTTP 尝试，20 秒搜索截止共享两阶段，取消清理可增加返回时间；候选最多 6 逻辑搜索、12 秒截止。遇到限流／服务故障保留结果，不无限增加请求。天气远期未知、公交无路线或异常如实记，不替换步行冒充通过。

## 本轮结果

### 自动检查（本轮实际执行）

- `cd backend && .venv/bin/python -B -m unittest discover -s tests -q`：**455 通过**。三城上游参数／归属、天气身份、受限代理、临时磁盘 SQLite v1/v2 及旧行为回归；未写用户库。保留既有测试，不修改纯排程算法或旧评分。
- `cd frontend && npm test -- --run --reporter=dot`：最终 **56 文件／1,135 通过**。首轮 1,134，真实天气发现的请求快照回归新增 1 项；不是把历史成绩记成本轮。Python→TypeScript 以实际临时数据库 API 导出 12 份三城、模式、部分／空日样本，验证严格读取、恢复、再次投影和 AI 精简快照。
- `npm run lint`、`npm run typecheck`、`npm run build -- --webpack`、`git diff --check`：通过。最后历史城市说明／切城通知的针对性 7 项通过，并再跑完整前端。测试环境已有 Starlette/httpx 弃用提示，本轮不升级依赖。生产启动无启动错误；Turbopack 原限制保留。
- 城市编辑 ABA 的旧成功／错误、费用清理、预览取消／采用、旧保存幂等及模型接口兼容为受控验证；不冒称真实模型或真实高德故障。千问真实调用 **0**，知识库问答仍停用。

### 真实 Chrome 与证据

2026-10-15 使用本轮 Webpack 生产服务，真实搜索后明确确认，非 Mock 响应。住宿均是地标参考，不是预订：

| 城市／样本 | 实际往返步行 | 实际往返公交参考 | 天气 |
| --- | --- | --- | --- |
| 杭州：武林广场 ↔ 浙江省博物馆(孤山馆区) | 2715 秒→46 分钟，各向 | 首批 3872／3356 秒→65／56 分钟；各 ¥2；实际公交车 | 330100，两日覆盖，来源发布时间 19:35:43 |
| 北京：北京饭店 ↔ 天坛公园 | 2262 秒→38 分钟，各向 | 3496／3126 秒→59／53 分钟；各 ¥2；2 路 | 110000，两日覆盖，19:39:35 |
| 上海：静安寺 ↔ 武康大楼 | 2156／2153 秒→各 36 分钟 | 2370／2536 秒→40／43 分钟；各 ¥2；548 路 | 310000，两日覆盖，19:33:09 |

地点 citycode／cityname／adcode 和地址可见，三城底图、编号及往返方向匹配。杭州摄影候选 1 逻辑搜索，返回 18 可选；第一份草案两日均有实际访问，预览取消保留原方案、第二次预览采用同步排除；编辑取消保留费用 25／草案／地图，确认切北京清除旧依赖，再主动生成。新 v2 已保存、列表／只读详情读取、刷新恢复；旧 v1 原日期及生成时间仍保留，副本必须重新确认地点才可生成。保存只新增 2 个验收记录，无更新／删除接口、无改表或重写旧记录。

稳定目录：`/Users/zijing/projects/trippilot-evidence/destination-1/`。**16 张截图均逐张打开检查**，脱敏操作／计数记录见 [receipt](../../trippilot-evidence/destination-1/receipt.md)。主要证据：

- [杭州真实底图](../../trippilot-evidence/destination-1/hangzhou-map-before-weather-fix.png)、[公交分段](../../trippilot-evidence/destination-1/hangzhou-transit.png)、[第二日选中](../../trippilot-evidence/destination-1/hangzhou-day2-selected.png)。
- [北京底图](../../trippilot-evidence/destination-1/beijing-walking-map.png)、[400px 公交及键盘全貌](../../trippilot-evidence/destination-1/beijing-transit-route-400.png)。
- [上海步行地点图](../../trippilot-evidence/destination-1/shanghai-walking-map.png)、[公交分段](../../trippilot-evidence/destination-1/shanghai-transit-map.png)。
- [v2 最终详情](../../trippilot-evidence/destination-1/hangzhou-history-final-build.png)、[刷新恢复](../../trippilot-evidence/destination-1/hangzhou-v2-restored-refresh.png)、[旧 v1 只读](../../trippilot-evidence/destination-1/legacy-v1-history.png)、[缺归属重新确认](../../trippilot-evidence/destination-1/legacy-v1-reconfirm.png)、[/demo 底图](../../trippilot-evidence/destination-1/demo-shanghai-basemap.png)。

浏览器本地业务请求共 **35**：搜索 7、候选 1、排程 9、天气 4（含失败样本）、仅 /demo 的 Mock plan 1、新保存 2、存档读取 11。页面监听捕获 28 个响应，另外 7 个为副本初次读取／刷新及最终历史复核，按操作核对；不是全程原始网络日志。地图 SDK／行政中心／瓦片不计业务查询，未逐次观测上游 HTTP 尝试，不能把本地数当成高德总数。地图全览／选中／切日、取消／采用、编辑确认都没有隐式业务查询。

### 本轮修复与未验收边界

1. 首次杭州天气发现发送层冻结快照漏 `destination_id`，返回旧上海 200；判定失败。补真实 fetch 层单测并修复，重建后杭州及另两城正确。保留 [正确杭州天气](../../trippilot-evidence/destination-1/hangzhou-weather-fixed.png)，不把旧截图当修复后证据。
2. 杭州部分可选公交边确实返回 `data_error`，保留逐日失败和未采用原因；必去及可用可选仍成立，没有改查步行、增加重试或放宽校验。未保存原始上游敏感响应，因此不推断具体高德异常字段。
3. 构建切换过程中旧服务引用 chunk 404；重启本轮前端生产进程后恢复。最终 /demo 无 Console error；没有用此错误追加业务请求。服务只重启已核对的本项目进程。
4. 没有穷尽三城区县／地点、真实地铁换乘专项、每城所有调整和保存组合、实体手机或 6＋3 满载。旧 v1 实测到只读与副本禁止不安全生成，完整重新确认路径有受控覆盖，不冒称本轮重新搜索并生成了全部旧样本。/demo 仅旧搜索、Mock plan、底图，旧绑定／路线完整组合由自动回归覆盖。
5. v1 纯排程重放和历史规则文字保持；“上海本地时间”是 `Asia/Shanghai` 时区，不意味着杭州／北京路线查上海。旧客户端明确的上海接口形状保留兼容；新正式 UI 必须选城、保留归属，不能因非法 destination 降级。

### 演示与停止点

首页选三城之一 → 明确确认本城住宿／必去 → 主动生成步行或公交 → 地点／单段地图、天气、费用按需核对 → 可选调整先预览再采用 → 保存 v2 → 历史新副本再主动生成。切城演示先取消保留，再确认清空；打开 v1 解释重新确认要求。知识库仍是上海三馆区资料检索，AI 不参与本轮实测。

停在三城目的地扩展第 1 阶段，不扩天数或媒体能力。下面交付清单用于逐项审查／暂存；原五缓存 SHA256 与启动前一致，环境／数据库／截图／浏览器日志不提交。

## 交付文件清单（70 个）

新增 `shared/destinations.json`、后端城市定义／现代契约／契约导出与测试、前端城市定义／受控夹具与测试、阶段报告；其余为现有业务链的小范围适配、兼容测试及四份产品文档。未新增依赖，不改业务库结构、环境配置或模型额度。

```text
README.md
backend/app/api/candidates.py
backend/app/api/places.py
backend/app/api/schedule.py
backend/app/api/weather.py
backend/app/destinations.py
backend/app/integrations/amap.py
backend/app/integrations/amap_proxy.py
backend/app/integrations/amap_transit.py
backend/app/integrations/amap_weather.py
backend/app/saved_trips.py
backend/app/schemas/destination.py
backend/app/schemas/explanation.py
backend/app/schemas/saved_trip.py
backend/app/schemas/weather.py
backend/app/services/candidates.py
backend/app/services/explanation.py
backend/app/services/schedule_drawing.py
backend/tests/export_destination_contract.py
backend/tests/test_amap_proxy.py
backend/tests/test_destinations.py
docs/DESTINATION_1.md
docs/PROGRESS.md
docs/PROJECT_CONTEXT.md
docs/ROADMAP.md
frontend/src/app/layout.tsx
frontend/src/app/page.tsx
frontend/src/components/places/place-details.tsx
frontend/src/components/places/place-map.tsx
frontend/src/components/places/place-search.tsx
frontend/src/components/places/requirement-place-picker.tsx
frontend/src/components/trip-request-form.test.tsx
frontend/src/components/trip-request-form.tsx
frontend/src/components/trip/candidate-preparation.tsx
frontend/src/components/trip/confirmed-places.tsx
frontend/src/components/trip/editable-planning-session.tsx
frontend/src/components/trip/formal-planning.integration.test.tsx
frontend/src/components/trip/planning-workspace.tsx
frontend/src/components/trip/restored-trip.integration.test.tsx
frontend/src/components/trip/save-trip.tsx
frontend/src/components/trip/saved-history.integration.test.tsx
frontend/src/components/trip/saved-history.tsx
frontend/src/components/trip/schedule-place-map.tsx
frontend/src/components/trip/schedule-preview.tsx
frontend/src/components/trip/weather-forecast.tsx
frontend/src/components/trip/weather-schedule-check.tsx
frontend/src/lib/amap-loader.ts
frontend/src/lib/api.ts
frontend/src/lib/destination.test-fixtures.ts
frontend/src/lib/destinations.test.tsx
frontend/src/lib/destinations.ts
frontend/src/lib/explanation-api.ts
frontend/src/lib/places-api.ts
frontend/src/lib/restore-trip.test-fixtures.ts
frontend/src/lib/restore-trip.test.ts
frontend/src/lib/restore-trip.ts
frontend/src/lib/restored-candidates.test.tsx
frontend/src/lib/saved-trips.ts
frontend/src/lib/schedule-input.ts
frontend/src/lib/schedule-map.ts
frontend/src/lib/use-candidate-pool.ts
frontend/src/lib/use-weather-forecast.ts
frontend/src/lib/weather-api.ts
frontend/src/lib/weather-schedule-check.ts
frontend/src/types/candidates.ts
frontend/src/types/place.ts
frontend/src/types/schedule.ts
frontend/src/types/trip.ts
frontend/src/types/weather.ts
shared/destinations.json
```
