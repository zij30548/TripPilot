# 聊天城市目录：离线参考，不扩展旧排程

2026-10-21。chat-2使用独立城市目录选择搜索范围；旧[六城表](../shared/destinations.json)、`DestinationId`、排程、天气、地图代理及saved-trip-v1/v2/v3均不因此开放全国或跨城。当前实现与停止点看[PROGRESS](PROGRESS.md)。

## 来源、版本与使用限制

- 当日实际读取[高德官方相关下载页](https://lbs.amap.com/api/webservice/download)和[官方城市编码ZIP](https://a.amap.com/lbs/static/code_resource/AMap_adcode_citycode.zip)，未携带凭据、未调用业务API。页面将城市表标为**2026-01-07**版；获取日不是发布日期。
- ZIP SHA256：`a5f190378c7dd5e592b0e95ca81ae7d454116293d5ac77472f96c20d2fa184e3`，与destination-1/2历史核对相同。XLSX和目录哈希、获取日、来源行数及逐行排除原因见[来源清单](../shared/chat-cities-source.json)。原ZIP／XLSX不提交。
- **官方明确提醒**：编码会不定期更新，表格仅供线下查询，请勿作为缓存文件使用，建议由地址解析等在线服务获取最新编码；台湾行政区查询不支持。本轮只是版本化离线受控选择／归属参考，不是在线行政服务缓存、现行区划认证或全面商用授权证明。生产用途／条款适用性及更新策略仍待核；不得把静态表说成已获当前有效性保证。
- 未增加在线行政查询来绕开这个边界。区划变动、未知或不一致的上游归属明确失败；后续确需实时解析时须单独审查权限、费用和请求预算。

## 目录范围与确定性生成

[chat-cities.json](../shared/chat-cities.json)为`chat-city-catalog-v1`：大陆369个目录单元（4直辖市、333地级单元，含自治州／地区／盟、32特殊省直辖单元），3182条明确成员编码。它只说明目录／搜索边界，不证明所有地点、公交、天气或排程可用。
官方3240行中58行明确排除：港澳台范围29行；中国、省级汇总及澳门大学横琴校区等缺少citycode的29行。不把省级汇总伪装成单城，无法分组则停止生成。

生成时按官方citycode完整分组，选明确本级行，特殊省直辖一行独立成组；再把每条成员adcode写入清单。归属核验使用清单成员，**不以adcode前缀推断实际POI**。编码可能为`1433`、`1558`等，并非都以0开头，不能套用六城电话区号假设。
名称解析只接受目录的精确中文／明确别名，允许首尾空白及英文大小写，不模糊翻译、猜城市或混写回退。只为原六城保留明确英文名及旧slug映射；本地目录搜索可筛选文字，但搜索结果不是用户已选择城市。

离线复现（backend目录，原始ZIP由维护者从上述官方地址取得并保留）：

```sh
.venv/bin/python -B -m tools.chat_city_catalog verify \
  --source-zip /absolute/path/AMap_adcode_citycode.zip --retrieved-at 2026-10-21
```

预期`verified / cities:369 / business_calls:0`。`render`只向stdout输出候选JSON，不写文件；维护者审查后明确应用。源ZIP哈希或成员不符即停止，不能更新期望哈希来迁就数据；以后新版另做来源核对与版本审查。

## 独立地点搜索适配

- [chat_cities.py](../backend/app/chat_cities.py)校验目录、来源哈希、ID／citycode／别名重复、层级及成员矛盾；`city_for`严格取ID，`resolve_city`精确解析，`search_cities`只检索本地且最多20条。
- [chat_places.py](../backend/app/integrations/chat_places.py)固定高德v5文本搜索、显式6位city-id、city_limit、最多20结果；只允许原有一次连接失败重试，实际物理尝试由现有MeteredAsyncClient逐次持久计数。未知城市和非法关键词在发送前拒绝。
- 响应最多256KiB；重复JSON键／非有限数／超过20条／非法必要字段／相同ID矛盾快照／缺失或跨城归属明确报错，不截断、猜补、换城或伪装成空结果。只有真实`pois:[]`才无结果；同ID完全相同快照可去重。
- 保留原始城市归属，按目录citycode、城市名称和明确adcode成员共同核验；来源`amap`是链路标记，不是实地真实性或照片匹配认证。结果仍是待确认候选，不自动写入需求／存档。

## 本轮验证及未验范围

本轮定向14项受控测试通过，含六城成员等价、哈尔滨／特殊省直辖／非0开头citycode、混写／未知／跨城、数量／正文／重复键、连接重试逐次真实临时额度存储及额度失败阻断。官方ZIP离线verify通过；无真实POI、模型或浏览器验收，也没有全国服务可用性结论。主阶段更大范围检查见chat-2报告，不把历史成绩算成本轮结果。
