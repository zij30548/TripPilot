# TripPilot 前端

项目定位、当前能力与停止点以[根 README](../README.md)、[PROJECT_CONTEXT](../docs/PROJECT_CONTEXT.md)及[PROGRESS](../docs/PROGRESS.md)为准。

- Next.js App Router、React、TypeScript、Tailwind；实际版本以[package.json](package.json)和[package-lock.json](package-lock.json)为准。
- 包管理统一使用 npm，安装执行 `npm ci`，不要混入其他包管理器锁文件。
- 本地启动、私密配置、同源后端及检查命令见[LOCAL_DEVELOPMENT](../docs/LOCAL_DEVELOPMENT.md)，不要照搬初始化模板或将服务端凭据放入浏览器变量。
- 默认 `/` 是聊天／需求意向确认；旧真实规划独立在 `/legacy`，旧 Mock 地图示例在 `/demo`。
- chat-1实现、受控验证和未完成的真实语义／生产备份门槛见[CHAT_1](../docs/CHAT_1.md)，不是已完成的旅行Agent loop。
- 修改前端代码前必须读[AGENTS.md](AGENTS.md)，并按其要求读取已安装版本 `node_modules/next/dist/docs/` 的相关文档；不凭早期Next经验修改。
- 服务端转发只接受可信配置目标；不以匿名测试、全局关闭鉴权或真实外部调用来代替受控验证。
