# 智应 ServeFlow：本机客服界面调整

2026-10-25，用户指定名称：**智应 ServeFlow：面向复杂客服任务的多Agent协作系统**。这是上传的原EchoMind客服项目，独立于TripPilot旅行助手。

打开 **http://127.0.0.1:5173/** 并刷新。前端仍为Vue／Vite，沿用Python8816和原[启动方式](ECHOMIND_ORIGINAL_LOCAL.md)。本轮复用已运行服务，不切换3110或云端。

## 改了什么

| 文件（相对`EchoMindFrontend/`） | 用途 |
| --- | --- |
| `index.html` | 页面标题、描述、本地图标 |
| `src/App.vue` | 侧栏／手机页头、欢迎语、助手及加载头像；只新增展示组件导入 |
| `src/styles.css` | 浅色背景、克制的紫色强调、留白、边框、焦点与窄屏 |
| `src/components/BrandMark.vue` | 复用的代码SVG标识，无外部资源 |
| `public/serveflow.svg` | 浏览器图标 |

原对话／知识库／评测、运行详情、连接切换、快捷键和状态逻辑保留。`echomind.frontend.settings`、评测会话键和`__ECHOMIND_CONFIG__`等内部名称保持，避免更名丢设置。原作者信息和后端返回的Skill说明不作全局替换；知识页可能仍显示原EchoMind来源说明。这不是新的Agent、RAG或模型质量交付。

## 源码与版本管理

本机修改源码不会影响原作者。只有主动向相应远端推送且有权限，才会修改远端；本轮没有向原作者推送、没有删除嵌套`.git`或更换remote。

主仓基线`1292f0611251df9b39a4e7a5927657097937ef23`；嵌套前端原基线`2ec9d14ed094203d455ceae311ebb5fc52911674`。前端本地修改已生效，主仓另保存[5文件补丁](../patches/serveflow-ui.patch)。该补丁用于用户自己的相同基线副本；不包含依赖、配置、数据或原作者整仓，不代表已将整个独立仓库纳入主仓。

以下只供未来恢复用；**当前已应用的目录不要重复应用**：

```sh
# 在原基线的干净前端副本中先检查，不自动覆盖其他修改。
cd /Users/zijing/projects/trippilot/EchoMindFrontend
git apply --unidiff-zero --check ../patches/serveflow-ui.patch
git apply --unidiff-zero ../patches/serveflow-ui.patch
npm test
npm run build
```

补丁采用零上下文并要求`--unidiff-zero`。当前已修改目录可用`git apply --unidiff-zero --reverse --check ../patches/serveflow-ui.patch`只读确认对应关系；不要省略`--check`执行反向恢复。锁文件、请求库、原后端和模型提示均未改。

## 检查与证据

- `npm test`：现有14项通过，覆盖请求版本、晚成功／错误、评测轮询等。
- `npm run build`：Vite7.3.5生产构建通过；原工程没有lint／typecheck脚本，本轮未添加依赖或伪造检查。
- `git diff --check`、补丁对应关系、源码修改范围、链接和五缓存哈希核对。
- 真实Chromium在本机页面核对三个导航页、桌面1360px、平板900px和400px；未发送的输入切页后保留，Enter换行，连接配置可键盘打开及Escape关闭。没有执行模型对话、知识检索、导入或评测；不宣称完整无障碍或后端业务重新验收。
- 浏览器仅观察`GET /api/python/health`、`knowledge/stats`、`monitor`、`skills`四类初始化请求；保护性拦截禁止非GET和外部主机，实际拦截请求0、页面错误0。真实模型、高德及其他业务外部调用**0**。

证据目录：`/Users/zijing/projects/trippilot-evidence/serveflow-ui-20261025.4Qs827/`；`final-chat-desktop.png`、`final-knowledge-desktop.png`、`final-evaluation-desktop.png`、`final-chat-tablet.png`、`final-chat-400.png`、`final-knowledge-400.png`均逐张打开检查。截图和浏览器日志不提交。

## 解释与停止点

口头说明：在原客服前端的展示层替换品牌，并用现有CSS整理视觉层次，业务处理链和模型保持原样。避免通过全局替换改坏存储键、服务地址或来源记录；用原异步测试、实际构建和只读浏览器检查验证。

面试追问可围绕实际证据回答：

1. **为什么不重写前端？** 本轮只需更名和视觉调整，复用既有Vue状态及组件，避免引入第二套行为。
2. **如何确认没改变模型逻辑？** 脚本只增加展示组件导入，请求库和后端源码diff为空；浏览器没有发送模型请求。
3. **为什么保留EchoMind内部键和来源？** 键是兼容契约，来源是归属信息；品牌替换与协议迁移是不同工作。
4. **测试通过说明什么？** 说明原状态测试和构建通过、检查过所列布局与键盘操作；不证明回答准确率或公网就绪。

停止在本次界面调整；原客服真实语义质量、公开部署和旅行适配不在本轮验收内。
