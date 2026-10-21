# TripPilot 前端

项目定位、当前能力与停止点以[根 README](../README.md)、[PROJECT_CONTEXT](../docs/PROJECT_CONTEXT.md)及[PROGRESS](../docs/PROGRESS.md)为准。

- Next.js App Router、React、TypeScript、Tailwind；实际版本以[package.json](package.json)和[package-lock.json](package-lock.json)为准。
- 包管理统一使用 npm，安装执行 `npm ci`，不要混入其他包管理器锁文件。
- 本地启动、私密配置、同源后端及检查命令见[LOCAL_DEVELOPMENT](../docs/LOCAL_DEVELOPMENT.md)，不要照搬初始化模板或将服务端凭据放入浏览器变量。
- 默认 `/` 是迁移后的聊天助手，普通消息使用专用 `assistant-turns`，按需展示需求、真实地点、行程工具、执行记录和用户明确保存的偏好。旧 `/legacy` 仅保留兼容，不在主界面提供入口；`/demo` 仍是独立 Mock 地图示例。
- 现有 `EditablePlanningSession`／`PlanningWorkspace` 嵌入按需行程面板，沿用原时间轴、费用、地图、预览及保存；折叠不卸载控制器。它仍是前端临时工作副本，刷新会丢失未保存草案和估算；规划面板内编辑不会回写聊天意向或地点卡。
- 有界原生工具回路、明确偏好同意／撤回及任务收据为本次实现；固定真实复验5/8调用，首版失败保留、修复后3个剩余请求通过，仍非广泛质量认证或生产切换。日期／费用工具引导程序面板，不把模型建议当后端正式快照。见[ECHOMIND_MIGRATION](../docs/ECHOMIND_MIGRATION.md)，原[CHAT_1](../docs/CHAT_1.md)／[CHAT_2](../docs/CHAT_2.md)评分独立保留。
- 原3104仍为旧schema；3110先受控验证再固定真实复验，验收计划已封存。随后用户授权本机试用，8804改为普通真实产品传输，复用独立MySQL及原额度记录；一次性8810正常登录后跳3110，不关闭鉴权。天气／地图代理／视觉与媒体仍限额关闭，详见[试用边界](../docs/ECHOMIND_MIGRATION.md#75-独立本机手工试用入口)。自行启动须先按[本地开发](../docs/LOCAL_DEVELOPMENT.md)准备助手扩展，不能由前端启动替你改库。
- 修改前端代码前必须读[AGENTS.md](AGENTS.md)，并按其要求读取已安装版本 `node_modules/next/dist/docs/` 的相关文档；不凭早期Next经验修改。
- 服务端转发只接受可信配置目标；不以匿名测试、全局关闭鉴权或真实外部调用来代替受控验证。
