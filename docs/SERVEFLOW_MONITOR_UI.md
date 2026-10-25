# ServeFlow：对话与监控

2026-10-25，基线`0114c3fa4de437729656c9147db7399e85252c3a`。用户明确选择收敛独立客服前端：去掉Java/Python选择，不保留独立知识页，将评测入口改成监控。只修改`EchoMindFrontend/`；TripPilot旅行站点、后端方法、数据和旧评分不改。

## 当前使用

- **对话**：发问题后，原Agent根据需要选择RAG或业务工具；不强制每条消息先检索，不自动全网搜索。已导入的知识资料和MySQL演示数据均保留。数据区依旧默认折叠。
- **监控**：进入或主动点“刷新指标”才读取本机后端`GET /monitor`；不启动评测、不调用Judge或其他模型、不自动轮询。
- 当前前端固定Python服务，无Java/Python按钮；旧localStorage保存的Java选择会被忽略并在保存时规范为Python。后台兼容函数保留，不意味着页面仍可切换。
- 独立知识查询／导入／Skills重载及评测操作界面已移除。资料维护使用现有JSON文件和受控导入接口；后台评测方法及工具供后续研究，不新增隐藏管理页或占位按钮。

打开 **http://127.0.0.1:5173/** 并刷新即可。前端Vite复用原进程，未重启后端、数据库或云端。

## 指标是什么

| 页面指标 | 现有字段／范围 | 不代表什么 |
| --- | --- | --- |
| Agent执行次数 | `agent_stats.*.total`，并行角色分别计数 | 不是HTTP用户请求数或模型物理发送数 |
| 执行成功率 | `success_rate`；后端定义的执行完成情况 | 不是答案正确率或用户问题解决率 |
| 平均执行耗时 | Agent的`avg_ms`，工具的`avg_latency_ms` | 不是全部聊天端到端耗时 |
| 恢复／熔断状态 | 固定`recovery_state`／`circuit_state`标签 | 正常只表示该机制状态，不保证业务回答正确 |
| 已注册工具 | 当前MCPToolManager统计，含缓存命中 | 不覆盖全部native Agent工具，不是外部请求或人民币费用 |
| 阈值提醒 | 现有`active_alerts`的已知指标及数值 | 不执行告警正文、模型建议或自动修复 |

0样本显示“暂无样本”，不能将后端默认1.0画成100%成功；未知／非法数值显示—。聚合成功率／耗时从后端已取整的角色值加权，明确标约数，不反推精确成功次数。界面不显示原始客户消息、角色Prompt、任意错误正文、Token成本或未取得的长期趋势图。

这些是**本进程统计**，服务重启可能清零；本轮没有新增长期监控库或审计恢复能力。截图中的既有执行／失败来自当时进程，不是本轮新跑的验收题，不用于重新评分历史模型。

## 状态与取消

对话与监控使用`v-show`，切换不卸载关键控制器。消息、未发送草稿、演示查询结果和内存CSRF保留；切页不自动发送聊天、检索资料、生成行程或保存数据。

监控有独立请求槽、单调版本、身份与Abort检查；离开页面取消当前读取，旧success/error/finally不能覆盖新请求。20秒截止与调用方取消信号组合使用，避免传入AbortController后丢失原超时。刷新失败保留旧快照和原读取时间，显示失败提示，不更新成现在；成功才记录新的“读取于”。未知错误内容不直接显示。

## 文件与验证

前端9文件：`src/App.vue`、`src/styles.css`、`src/lib/backends.js`、`src/components/RequestInspector.vue`、`tests/requestState.test.js`，新增`MonitorDashboard.vue`、`src/lib/monitorStats.js`、`tests/monitorStats.test.js`、`tests/frontendSettings.test.js`。后端源码／模型提示／检索策略不变。

- 最终`npm test` **39项通过**，覆盖0样本、非有限数、字段口径、固定Python、取消＋超时、ABA、旧响应与失败保留时间等；`npm run build` Vite7.3.5通过，diff检查通过。项目没有lint/typecheck脚本，本轮不加依赖。
- 明确移除页面后，旧源码抽取测试对loadStats／loadSkills／runEvaluation的7处失败按实际功能迁移为health／monitor等价隔离检查；底层评测轮询测试保留。原后端及旅行测试未改，不机械重跑Python全套。
- 真实Chromium验证两导航、旧Java存储不影响Python请求、草稿和演示订单结果切页保留、400px、键盘、真实监控读取和刷新。仅6次真实监控GET；另一次**受控503**检查错误提示、旧时间保留、恢复刷新及不展示原错误，不冒称自然故障。
- 打开聊天时监控GET0；浏览器POST、知识搜索、评测请求0；另1次原演示订单GET只读MySQL。当前收到的非零进程指标不属于新模型试验，**本轮发起千问／高德／其他业务外部调用0**。
- 五缓存、原数据库与知识资料保持；没有改凭据、代理、CORS、数据库或运行配置，不重启8816、MySQL、3110／8804或云端。

证据：`/Users/zijing/projects/trippilot-evidence/serveflow-monitor-ui-20261025.YCzZcw/`。`01-monitor-desktop.png`、`02-chat-400.png`、`03-monitor-400.png`及最终表头说明的`04-monitor-final-desktop.png`、`05-controlled-failure.png`、`06-monitor-final-400.png`均逐张打开；第5张明确为受控故障。截图和原始浏览器日志不提交。

## 交付与解释

主仓保存[9文件增量补丁](../patches/serveflow-monitor-ui.patch)，在既有品牌和MySQL演示前端补丁之后应用。只对相同基线干净副本先`git apply --unidiff-zero --check`；当前已应用目录只用`--reverse --check`核对，不重复写入。嵌套Git及原作者remote保留、不向原作者推送；主仓仅普通提交／推送审查过的补丁和文档。

口头说明：把日常入口简化成对话，把已有运行状态集中到只读监控。保留原检索和评测算法，避免把每次打开监控变成付费评测；指标名称按实际采集对象定义，用失败和未知状态保留事实，而不补造准确率、费用或趋势。

可追问：为什么并行Agent次数不等于请求数；为什么0样本不显示100%；监控为何不等于评测；刷新失败怎样避免旧数据看起来刚更新；取消与超时为何需要同时保留。对应代码、测试及浏览器证据可核对，不声称完整APM或公开上线准入。

停止在本次独立客服界面收敛；不改变TripPilot专用知识问答停用状态，不追加模型试验或自动进入下一功能阶段。
