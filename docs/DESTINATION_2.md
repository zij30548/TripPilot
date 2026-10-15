# 目的地扩展第 2 阶段：广州、成都、西安

基线 `646f069a25b342981c86bb9f9bd74711bcef2483`，main，开始时与 origin/main 同步。2026-10-15 在实际仓库实施；不扩天数、容量、路线预算或 Agent 职责，真实模型调用为 0。原五缓存及浏览器日志保留，不纳入交付。此文先固定计划，再追加实际结果，不抹掉失败。

## 城市资料与接入边界

2026-10-15 实际读取[高德官方城市编码下载页](https://lbs.amap.com/api/webservice/download)及其[公开 ZIP](https://a.amap.com/lbs/static/code_resource/AMap_adcode_citycode.zip)，使用页面标注 **2026-01-07** 版本。ZIP SHA256 `a5f190378c7dd5e592b0e95ca81ae7d454116293d5ac77472f96c20d2fa184e3`，内含 `AMap_adcode_citycode.xlsx`；不是凭城市名猜码，获取日期不当成数据发布日期。网页工具下载超时后用 curl 读取同一官方文件成功，未访问带凭据接口。

| ID | 展示／官方名 | adcode | citycode | 行政查询层级 | 下属区县（不含本级） |
| --- | --- | --- | --- | --- | --- |
| guangzhou | 广州／广州市 | 440100 | 020 | city | 11 |
| chengdu | 成都／成都市 | 510100 | 028 | city | 20 |
| xian | 西安／西安市 | 610100 | 029 | city | 13 |

- 广州：荔湾 440103、越秀 440104、海珠 440105、天河 440106、白云 440111、黄埔 440112、番禺 440113、花都 440114、南沙 440115、从化 440117、增城 440118。
- 成都：锦江 510104、青羊 510105、金牛 510106、武侯 510107、成华 510108、龙泉驿 510112、青白江 510113、新都 510114、温江 510115、双流 510116、郫都 510117、新津 510118、金堂 510121、大邑 510129、蒲江 510131、都江堰 510181、彭州 510182、邛崃 510183、崇州 510184、简阳 510185。
- 西安：新城 610102、碑林 610103、莲湖 610104、灞桥 610111、未央 610112、雁塔 610113、阎良 610114、临潼 610115、长安 610116、高陵 610117、鄠邑 610118、蓝田 610122、周至 610124。

官方说明编码会更新，下载表用于线下核对；本项目把核对后的六城作为明确产品白名单，不作全国动态地址缓存。实际 POI 仍保留并检查上游 citycode/cityname/adcode 三字段，区县须在清单中。过时或未知归属失败而非猜测补齐，后续区划维护需重新核对。枚举／结构检查不能证明 POI 的实地真实性。

## 可复用的城市接入步骤

1. 明确单轮城市与有限真实样本，读取官方表，记录来源版本、获取日期、哈希及区县对应关系。
2. 更新 `shared/destinations.json` 和后端 `DestinationId`、前端 `DESTINATION_IDS`（由闭合 tuple 导出 TS union），不接受任意字符串。后端加载时检测漏项、未知、重复 ID／名字／城市码／行政码、跨城重叠及明显层级错误；先检查再建字典，避免重复项被覆盖。
3. 在 backend 运行 `.venv/bin/python -B -m unittest tests.test_destinations -q`，在 frontend 运行 `npm test -- src/lib/destinations.test.tsx src/lib/amap-loader.test.ts`。TS 测试实际导入 Python 允许值与表做一致性比较；临时磁盘 API 导出六城×四种样本，共 24 份 v2，校验读取、恢复、费用投影与精简 AI 契约。自动测试不写正式库、不查询外网。
4. 搜索／候选沿用行政归属；公交沿用 v3 city/cityd，天气沿用城市 adcode／身份校验，地图中心沿用城市名＋层级＋返回 adcode。代理只允许表内本级查询，参数、上游、Key 与 jscode 限制不变。首页及元信息从表生成，不全局替换 Asia/Shanghai、知识库或 /demo。
5. 验证事务式切城、取消、费用与依赖失效、ABA 和提示生命周期。新城沿用 saved-trip-v2，不改版本／数据库表；v1 严格读取及重新确认规则保留，旧三城记录不重写。
6. 离线通过后进行有限 Chrome 验收；按实际结果记录成功、无数据、data_error 或其他阻塞，不因代码参数可传就宣称覆盖全部区县。普通提交仅包含审查文件，证据在仓库外。

## 发送前固定的最小验收矩阵

每城 2 日样本（2026-10-16–17），选一个住宿参考点和一个必去，地点均以实时搜索结果和地址明确确认；参考地标不是酒店预订。

| 城市／操作 | 原定样本／请求预算 | 实际结果 |
| --- | --- | --- |
| 广州 | 广东迎宾馆／陈家祠；2 搜索、步行 1、公交 1、天气 1；地图与联动 | 全部得到真实成功结果；公交为夜79／527路，不代表旅行时刻可乘 |
| 成都 | 天府广场／武侯祠；2 搜索、步行 1、公交 1、天气 1；地图与联动 | POI、步行、天气、地图通过；公交出程 data_error，完整公交未通过，不重试换点 |
| 西安 | 西安钟楼／大雁塔；2 搜索、步行 1、公交 1、天气 1；地图与联动 | 全部成功；公交606路/K606路，非地铁专项样本 |
| 成都扩展 | 原计划摄影候选 1 批，步行含可选 1 次，预览 2 次（取消／采用），最多新增 2 条明确验收存档；读取／刷新／副本步行再生 | 实际表单未选兴趣，因此执行默认“旅游景点”1次，而非摄影；得到18可选，其他操作完成，未追加兴趣请求 |
| 城市编辑 | 成都副本切旧城先取消，再重新搜索 2 个点并确认、主动生成 1 次；400px | 北京饭店／王府井步行街；取消保留，确认清空，生成成功提示正确，400px通过 |
| 旧城兼容 | 三城契约／代理／恢复自动回归；旧 v1/v2 少量只读 Chrome 检查，不重做全量真实请求 | 旧上海v1仍须确认，旧杭州v2恢复；北京真实步行；其余及/demo为自动回归 |

三城基础为 15 个本地业务请求，不含 SDK／底图；成都扩展及读存档另记。若关键词歧义，每城最多 1 次补充搜索；其他失败不反复换样本，先保留并说明原因。浏览器计数不是后端上游尝试数。模型请求 0，不触发其他 AI 功能。

## 检查、证据和限制

### 本轮实际检查

- backend：`.venv/bin/python -B -m unittest discover -s tests -q`，**456 通过**。前端调用 Python 同样使用 `-B`，数据库样本在临时目录。
- frontend：`npm test -- --reporter=dot`，**56 文件／1,160 通过**；`npm run lint`、`npm run typecheck`、`npm run build -- --webpack` 通过。生产构建用于下列 Chrome 实测，不是开发服务器。
- `git diff --check`、链接／敏感信息／范围检查通过；五缓存 SHA256 与开工记录逐一一致。
- 首轮测试暴露两条旧测试假设：候选必去角色断言误写为 required（实际 must_visit）；原代理拒绝样本用广州（已开放），改为深圳。保留拒绝断言后完整重跑通过，没有放宽业务校验。Starlette/httpx 旧弃用提醒保留。

### 真实结果（2026-10-15 22:40–23:00，Asia/Shanghai）

- 广州：广东迎宾馆 `440104`、陈家祠 `440103`，归属广州市／020；陈家祠这次确认的是高德“热点地名”候选，不冒称馆方入口或售票信息。步行往返各1489秒→25分钟，10:50返程；公交1773／1501秒→30／26分钟，参考4／3元，10:56返程。
- 成都：天府广场 `510105`、成都武侯祠博物馆 `510107`，成都市／028。步行往返1730秒→29分钟，10:58返程。公交出程 `data_error`，返程1路2865秒／2元有效；整体未安排，保留原因，不拿单边成功充作完整方案。未追加真实诊断请求，因此没有进一步断言具体哪个原始字段损坏。
- 西安：西安钟楼 `610104`、大雁塔 `610113`，西安市／029。步行5306／5298秒→89分钟；公交4613／4648秒→77／78分钟，606路/K606路各2元，含午餐避让，14:18返程。
- 三城天气均返回正确城市身份 `440100 / 510100 / 610100`，实际覆盖10月16、17日。发布时间分别为10月15日22:30:54、22:36:28、22:37:20。广州多云，成都／西安有小雨；这是市级昼夜预报，不保证旅行实际天气。
- 三城真实道路／地名底图可见，卡片定位居中高亮、Marker回定位、全览不新增业务请求。成都第二天交子公园与第一天独立；广州空日清除标记。成都Marker→交子公园卡片实际 `data-map-selected=true`、边框高亮；键盘Enter打开地图。
- 成都默认候选：1个逻辑关键词“旅游景点”，上游规范化结果20条，18可选；原正式草案为必去1/1、可选2/3。预览排除成都金融城双子塔后，交子公园／桂溪生态公园入选，必去保持；第一次取消原时间轴与候选不变，第二次采用同步排除，不产生额外请求。两次预览结果为合法partial，不称全部可选已排入。
- 仅新增两条明确命名存档：`9929b2237c314591be493cf3220ebff0`（采用后）、`8fb094ee0c5044b881ecc31fa28ab877`（副本重生成）。刷新读取及新标签副本没有自动生成；主动生成后另存，原保存时间22:50:17和生成时间22:49:43保留，数据库结构及旧记录不改。
- 成都副本切北京后取消：原时间轴文字相同、3个Marker保留；再次确认新地点后清空依赖，不自动请求，主动生成后只剩有效草案提示与有意义的清空说明。400px地图／提示无重叠、未见横向溢出。旧上海v1读取仍要求重新确认；旧杭州v2恢复正确，本轮未重复旧v1地点重搜或/demo真实全链路。

### 证据与请求口径

稳定目录：[destination-2](../../trippilot-evidence/destination-2/)。20张截图均保存后逐张打开检查；[脱敏请求记录](../../trippilot-evidence/destination-2/requests-sanitized.json)和[操作说明](../../trippilot-evidence/destination-2/receipt.md)不含认证头、Key或完整网络日志。

- 自动记录30个本地业务响应：POI搜索8、排程11、天气3、候选1、保存2、列表1、详情4，均HTTP200；**HTTP200不代表所有公交边成功**。历史副本首次打开的一次详情读取在安装监听前完成，另计1次可确认读取，共31次已知业务操作；不声称30条监听覆盖了全部页面资源。
- 地图行政中心／SDK／瓦片为必要地图资源，单独识别，不计作业务查询；未完整记录瓦片数，不把浏览器业务计数当作高德HTTP尝试数。无额外单段路线查询、Mock计划、天气刷新或AI请求；**真实模型0**。
- 核心图：[广州地图](../../trippilot-evidence/destination-2/01-guangzhou-map.png)、[广州公交](../../trippilot-evidence/destination-2/17-guangzhou-transit.png)、[成都公交未安排](../../trippilot-evidence/destination-2/05-chengdu-transit-data-error.png)、[成都次日地图](../../trippilot-evidence/destination-2/07-chengdu-day2-map.png)、[采用结果](../../trippilot-evidence/destination-2/09-chengdu-adopted.png)、[v2历史](../../trippilot-evidence/destination-2/10-chengdu-history.png)、[400px地图](../../trippilot-evidence/destination-2/11-chengdu-map-400.png)、[切城生成成功提示](../../trippilot-evidence/destination-2/12-city-change-success-400.png)、[西安地图](../../trippilot-evidence/destination-2/13-xian-walking-map.png)、[西安公交](../../trippilot-evidence/destination-2/15-xian-transit.png)、[旧v1](../../trippilot-evidence/destination-2/16-old-v1.png)、[Marker回定位](../../trippilot-evidence/destination-2/20-marker-to-visit.png)。天气完整图14／18／19。08仅截到预览入口，不能作为完整对比截图；取消／采用依据另有页面断言和请求记录，不追加路线调用只为补图。

### 取舍、未通过与停止点

共享定义仅新增枚举和官方归属行，不复制城市业务模块；调用方已有参数化，所以无需修改排程算法、费用、AI传输或存档版本。新v2复用原严格重放／白名单与2MiB限制，v1及其重新确认规则不变。只把地图代理本级允许值扩到六城，区县与任意URL仍拒绝。

**成都完整公交真实验收未通过，保持公开限制；不宣布六城所有能力全通过。** 成功公交样本都是公交车，未做新增三城地铁／换乘专项、区县穷尽、满载、实体手机和新城公交候选采用／保存实测；这些模式契约及异常用受控测试。广州夜班方案不代表白天可乘，未来具体运营时刻验证不在本轮范围。候选选择不代表最优或室内替代。原 destination-1 的data_error、Turbopack、AI及历史限制不变。

完成限定实现、离线检查与上述有限实测后，仅提交本轮18文件，正常推送；停在destination-2，不追加样本寻找成功、不扩天数或影像。后续若要解决成都数据样本，应另行明确诊断／验收范围，不放松解析规则。

## 交付文件（18）

- `shared/destinations.json`
- `backend/app/destinations.py`
- `backend/app/integrations/amap_proxy.py`
- `backend/tests/test_destinations.py`
- `backend/tests/test_amap_proxy.py`
- `backend/tests/export_destination_contract.py`
- `frontend/src/lib/destinations.ts`
- `frontend/src/lib/destinations.test.tsx`
- `frontend/src/lib/amap-loader.test.ts`
- `frontend/src/components/trip/formal-planning.integration.test.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/app/layout.tsx`
- `AGENTS.md`
- `README.md`
- `docs/PROJECT_CONTEXT.md`
- `docs/ROADMAP.md`
- `docs/DESTINATION_2.md`
- `docs/PROGRESS.md`
