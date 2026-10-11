# 千问行程解读：本机配置与验收边界

仅用于 `POST /trips/schedule-explanation`。模型选择解释重点，程序核对事实并渲染内容；不是 Agent、聊天或排程服务。其他功能不依赖千问配置。

## 本机配置

编辑 **`/Users/zijing/projects/trippilot/backend/.env`**，在现有高德配置之后添加下列变量，不覆盖原文件：

```dotenv
QWEN_API_KEY=
QWEN_BASE_URL=
QWEN_MODEL=qwen3.7-flash-2026-07-15
```

- `QWEN_API_KEY`：在本机填写北京地域密钥，以 `SecretStr` 保存；不要发送到聊天、前端或 Git。
- `QWEN_BASE_URL`：从北京地域控制台复制“OpenAI 兼容地址”。只允许 HTTPS，域名为 `dashscope.aliyuncs.com` 或单个业务空间标签下的 `*.cn-beijing.maas.aliyuncs.com`；路径只能是 `/compatible-mode/v1`（末尾可带 `/`）。不接受认证信息、端口、查询参数、片段或其他路径，不跟随重定向。后台追加固定的 `/chat/completions`。
- `QWEN_MODEL`：默认固定快照 `qwen3.7-flash-2026-07-15`。白名单只另允许同系列 `qwen3.7-flash`，必须由维护者明确更改；程序从不因失败自动换型号或供应商。本轮真实验收优先固定快照。

环境变量优先于 `.env`，包括显式空值。缺失或不受支持的配置仅使解释接口返回脱敏 `503`，不阻止 FastAPI 启动，不影响高德、排程等功能。需要重启服务时使用项目现有虚拟环境并禁用缓存写入：

```sh
cd /Users/zijing/projects/trippilot/backend
.venv/bin/python -B -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

先检查并停止需要替换的现有后端进程，不重复占用端口。不要把 Key 放在命令参数中。

## 固定请求边界

- 非思考 `enable_thinking=false`、非流式 `stream=false`、严格 `response_format.type=json_schema`；没有工具、联网搜索、修复循环或自动重试。
- 一次主动操作最多一次模型 POST。模型输出上限 **1,024 Token**，整次模型操作截止 **20 秒**，前端等待 **25 秒**。取消清理可能延长响应返回；取消不能保证上游停止处理或计费。
- 输入请求体最多 64 KiB；名称最多 160 字符、最多 6 必去／3 可选、3 天／29 条边。仅发送经程序整理的事实目录给模型；坐标只用于本机后端核对，不发送给模型。地图几何、公交分段原文、天气、预算、任意自由说明及历史聊天均不进入模型。
- 模型最多选择 6 条重点，每条最多 3 个事实 ID；字段、类型、重复引用及结论前提均检查。无效内容失败，不显示固定解读冒充模型成功。
- 生成 Schema 按 `message_code` 使用 `anyOf` 限定对应类型的事实 ID；事实本身也带允许的 code。输出后仍独立核对引用，不因 Schema 正确就直接展示。官方不支持的 `uniqueItems` 不发送，全局重复引用由程序拒绝。
- 必去未排、交通参考估时、营业预约未核验由程序固定展示。快照内部一致不等于后端重新认证客户端数据来源。

## 配置后的真实验收

实施轮先运行无凭据测试。用户回复“本机配置已填写”后才开始真实千问调用，**本轮合计最多 20 次**，包含真实千问处理受控行程样本的次数。不循环调用直到碰巧通过。

证据目录：`/Users/zijing/projects/trippilot-evidence/6a-1/`（仓库外）。截图逐张打开检查；记录必要的模型名、契约版本、耗时、结果类别、Token 用量和调用次数；用量缺失记未知。不保存完整提示词、原始模型响应、认证信息或完整行程日志。

真实账号权限、JSON Schema 支持、解释重点是否有用，以及浏览器联动必须单独验收。Mock 通过或 HTTP 200 不代表这些已通过。存在配置或账号阻塞时，不标记阶段完成、不提交或推送。

## 官方依据（2026-10-11 核对）

- [Qwen3.7-Flash 模型及固定快照](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)
- [严格 JSON Schema 输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [思考模式控制](https://help.aliyun.com/zh/model-studio/deep-thinking)
- [北京地域与官方接入域名](https://help.aliyun.com/zh/model-studio/regions)

官方为避免结构化输出被截断，建议不限制 `max_tokens`；本轮按产品的有界调用要求保留 1,024 Token 上限。若截断，明确失败而不修复重试。这是成本／完整性取舍，不承诺所有合法输入都能得到可用解读。
