# 上线前依赖安全补修（2026-10-18）

## 范围和结论

基线 `0e0cad1b2433a2f2c9de4824e33274cbd10d84aa`，main；开工 fetch 后与 origin/main 同步。仅更新前端依赖、安装脚本策略、受控转发验证工具和文档。后端／排程／身份／代理规则／数据库均不变，没有修改云服务器。

本轮真实模型、高德及其他外部业务调用 **0**。npm registry、官方公告和构建字体下载属于依赖／构建访问，不是业务验收。完整审计由 **9项（8 high、1 critical）降至5项 high**；生产审计由 **3项（2 high、1 critical）降至0**。这只是本次 npm 公告库和锁文件的结果，不是完整安全审计或公网准入结论。

稳定证据：[本轮目录](../../trippilot-evidence/dependency-security/batch-20261018.zdwoSe/)。其中保留补修前后的完整 audit JSON、全依赖树、`npm explain` 路径、原 package/lock、干净安装输出及检查结果，不含密钥、认证头、数据库或原始网络日志。

## 版本与选择

| 项目 | 原版本 | 本轮版本 | 选择依据 |
| --- | --- | --- | --- |
| next / eslint-config-next | 16.3.3 | **16.3.8**，两者显式固定 | 官方已修复稳定补丁；保持16.3，不引入16.4功能变更 |
| sharp | 0.35.4 | **0.35.5** | 原父依赖范围允许；`@img/sharp-libvips-*`分发包1.3.4包含librsvg2.63.2 |
| source-map-js | 1.2.1 | **1.2.2** | 原`^1.2.1`范围内补丁 |
| brace-expansion | 1.1.18 / 5.0.9 | **1.1.21 / 5.0.12** | 各自原主版本范围内修复 |
| React / React DOM | 19.2.8 | 不变 | 不扩大修复范围 |
| ESLint | 9.39.5 | 不变 | 10.x与当前react/import/jsx-a11y插件peer尚不兼容，不能强行覆盖 |

Next配套`@next/env`、SWC、ESLint插件及Sharp各平台二进制随锁文件正常更新。没有增加Sharp直接依赖、overrides、忽略peer约束或使用`npm audit fix --force`；没有降级Next14。锁文件只变更上述包及其配套平台包。

生成过程：先修改两个直接依赖精确版本，再执行`npm install --package-lock-only --ignore-scripts`、`npm update sharp source-map-js brace-expansion --package-lock-only --ignore-scripts`，最后用本轮脚本策略执行普通`npm ci`。审计提示的强制降级只是npm建议，本轮没有执行。

## 公告、路径和实际触发条件

完整审计的9个包条目不是9个已证实可利用漏洞：Next单包含多条公告，braces则沿父依赖传播出5项。以下是代码路径核对，不等于证明在所有配置下不可利用。

| 公告 | 路径／触发条件与本项目范围 | 本轮处理 |
| --- | --- | --- |
| [Next next/og RCE](https://github.com/vercel/next.js/security/advisories/GHSA-vcvr-r3jv-pc5j) | `next/og ImageResponse`处理不可信SVG；项目未使用该API | 16.3.6修复，本轮16.3.8覆盖 |
| [use cache / Draft Mode泄露](https://github.com/vercel/next.js/security/advisories/GHSA-3w37-wq28-93x7) | CacheComponents/useCache与DraftMode；未启用 | 16.3.8 |
| [SSG/ISR缓存污染](https://github.com/vercel/next.js/security/advisories/GHSA-4jqv-mc3x-m676) | 自托管Pages Router；项目为App Router，无pages目录 | 16.3.8 |
| [开发MCP端点泄露](https://github.com/vercel/next.js/security/advisories/GHSA-39w2-rjm5-chcv) | `next dev`；正式使用`next start`，开发服务也不应公开 | 16.3.8 |
| [metadata图片dynamicParams绕过](https://github.com/vercel/next.js/security/advisories/GHSA-f87g-xv8r-7p7x) | Webpack动态metadata图片路由；项目无对应路由 | 16.3.8 |
| [SSG/ISR内容替换与DoS](https://github.com/vercel/next.js/security/advisories/GHSA-mcj8-r9mp-w47p) | root catch-all配合静态生成；项目无此路由 | 16.3.8 |
| [图片优化SSRF](https://github.com/vercel/next.js/security/advisories/GHSA-cjq9-62q9-8jv4) | 图片优化远程URL；项目无next/image或remotePatterns配置 | 16.3.8 |
| [Sharp/librsvg](https://github.com/lovell/sharp/security/advisories/GHSA-wq5f-xc86-pv6w) | `next → sharp`，glibc Linux在特定运行条件处理SVG可有RCE；Ubuntu服务器相关。业务照片由后端Pillow处理，不因此忽略生产依赖 | 0.35.5；本机实际加载rsvg2.63.2 |
| [source-map-js](https://github.com/7rulnik/source-map-js/releases/tag/v1.2.2) | `next → postcss → source-map-js`，另有开发路径；不可信indexed source map异常offset导致资源消耗，项目无用户source map入口 | 1.2.2 |
| [brace-expansion解析递归](https://github.com/juliangruber/brace-expansion/security/advisories/GHSA-6j4f-fj2g-mc7p)、[嵌套递归](https://github.com/juliangruber/brace-expansion/security/advisories/GHSA-qhr7-859c-m2p7)、[二次复杂度](https://github.com/juliangruber/brace-expansion/security/advisories/GHSA-q2hr-2g5m-vwhr) | ESLint/minimatch与typescript-estree/minimatch，均为开发依赖；不可信glob可造成递归栈／CPU问题 | 1.1.21与5.0.12覆盖三项 |

[Next16.3.8官方发布说明](https://github.com/vercel/next.js/releases/tag/v16.3.8)列出9月30日补丁。补修前后扫描使用同一机器、本轮npm公告查询；原始JSON与路径不覆盖。

### 剩余5项 high：同一条未修复的开发链

`eslint-config-next@16.3.8 → @next/eslint-plugin-next@16.3.8 → fast-glob@3.3.1 → micromatch@4.0.8 → braces@3.0.3`。

| 审计包条目 | 类型／原因 | 剩余风险与判断 |
| --- | --- | --- |
| braces | dev；[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)，3.0.3无已发布补丁 | 深层嵌套不可信glob可造成栈耗尽DoS；保留high，不宣称误报或修复 |
| micromatch | dev；依赖上述braces | 同一风险传播，不是另证实一处运行时漏洞 |
| fast-glob | dev；依赖micromatch | 同上 |
| @next/eslint-plugin-next | dev；依赖fast-glob | 同上 |
| eslint-config-next | dev；依赖上述插件 | 同上；npm建议降到14.2.35不适合当前Next16 |

现有网页请求、照片、视频不作为ESLint glob输入；仅在可信代码／配置上构建、lint，不向用户开放任意glob或构建执行。**该开发链告警不单独阻断当前受控部署准备，但必须保留并跟进上游；若引入不可信构建任务则需重新评估。** [维护者争议](https://github.com/micromatch/braces/issues/70)不是删除告警的依据。生产审计0也不涵盖其他语言、运行环境、配置、授权与完整供应链风险。

另有 **ESLint9已EOL** 的非CVE维护风险（[官方支持表](https://eslint.org/version-support/)）：2026-08-06停止支持。现有eslint-plugin-react7.37.5、eslint-plugin-import2.32.0、jsx-a11y6.10.2的peer上限为9，不能只根据config-next允许>=9就强升10。后续需专门验证完整ESLint10工具链；本轮保留弃用提示而非掩盖。

## npm11安装脚本策略

[npm官方脚本管理说明](https://docs.npmjs.com/cli/v11/commands/npm-install-scripts/)及本机npm11.19.0源码一并核对：该本地版本对未审批脚本默认仍可能执行并警告，不能把提示当作“已阻止”。在线v11文档现展示11.21.0，默认描述不能替代实际版本测试。

- `unrs-resolver@1.12.2`仅在开发链`eslint-config-next → eslint-import-resolver-typescript → unrs-resolver`。其postinstall经`napi-postinstall@0.3.4`先查平台绑定；缺失时会嵌套npm安装，再可能直接下载tgz。正常optional预编译绑定可用时不需要此恢复脚本。
- `package.json`显式`allowScripts: {"unrs-resolver":false,"fsevents":false}`；后者仅Darwin可选watcher，锁／registry元数据标记安装脚本，但本次实际发行包无install/postinstall或binding.gyp、已带预编译fsevents.node，不能仅凭元数据断言执行node-gyp。对其权限显式拒绝作防御，本次没有批准任何依赖脚本。
- 项目`frontend/.npmrc`设`strict-allow-scripts=true`，未来遇到未审查脚本应失败后人工判断，不使用全局允许、approve --all、强行开启脚本或省略optional依赖。
- 本轮**普通干净npm ci成功**；`npm install-scripts ls --json`无未审查条目；`ResolverFactory`与Sharp原生模块实际加载成功，完整lint／构建通过。Linux x64 glibc绑定存在于锁文件，但本轮没有在用户Ubuntu服务器安装／运行。
- 审查的旧安装脚本hash：unrs postinstall `446a0aeed55eeb28eadd9ac31f0b71654265aba8ca5a99dbc22dab0b26a02469`；napi lib/index `5c2547bb5ff29882a69737fe31509e32efc5a9cf0f4aa832e31e718403d70b04`；helpers `31740188ff37907a490c98d1d483a0814257c4a35a182c732468a8dd053ddf67`。不是对整个包作安全保证。

## 检查与受控验收

本地 **macOS arm64 / Node24.20.0 / npm11.19.0**；用户服务器为Ubuntu24.04 x64 / Node24.22.0 / npm11.19.1，本轮未登录或改服务器，不把本地原生绑定验证写成Linux实测。

实际命令（frontend目录；audit有告警时exit1为预期）：

```sh
npm audit --json
npm audit --omit=dev --json
npm ls --all --json
npm explain next sharp brace-expansion braces source-map-js unrs-resolver
npm ci
npm install-scripts ls --json
npm test
npm run lint
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:18081 npm run typecheck
node --test tools/verify-forwarding.node-test.mjs
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:18081 npm run build -- --webpack
TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:18081 npm run start -- --hostname 127.0.0.1 --port 3101
node tools/verify-forwarding.mjs verify --output /absolute/new/forwarding.json
```

- 前端完整：**70文件／1,346测试通过**；lint、typecheck、Webpack通过。Python→TypeScript子进程仍用-B，历史httpx弃用提醒保留。后端源码未改，未机械重跑完整后端测试。
- Node转发工具：**6测试通过**。补齐session/login/logout/usage，19业务资源对应当前22条rewrite；合成Cookie／Origin／CSRF只记录匹配布尔值，不记录头值。
- 真实TCP＋受控后端：**40项检查／40次受控dispatch通过**，覆盖22条rewrite、方法／重复query／状态422/429/503、302不跟随、固定路径拒绝、无缓存，照片5MiB／视频20MiB及各+1、响应5MiB哈希一致；31秒处理实际31,010ms返回。照片／视频各上传中及上传后取消，共4项均观察到后端连接恰好关闭一次、未完成响应，不是仅检查浏览器AbortError。保留75秒空闲超时／21MiB缓冲，没有新增业务重试或修改媒体限制。
- Chrome：生产Next16.3.8构建`4jccwg0Ujfa_UZ6U8lojf`，可信origin指向独立18081；复用既有`tools.usage_acceptance`，新临时身份／额度库，真实Argon2登录／Cookie／CSRF／持久计数，上游严格MockTransport且无真实fallback。主动确认合成住宿／必去并生成1/1：09:00–09:10交通、09:10–10:10停留、10:10–10:20返程；填写0/40/300/0估算得¥340。分区、费用编辑和需求编辑取消保留结果，不发新请求；缺CSRF直接POST403，退出后历史GET401、旧工作清除。400px无横溢，Enter登录和分区导航正常。**不是实时高德、模型、HTTPS Cookie或全旅行业务重新验收**，地图未初始化。
- [浏览器脱敏记录](../../trippilot-evidence/dependency-security/batch-20261018.zdwoSe/browser-receipt.json)保存8次同源fetch（含故意403／401）；独立受控发送4次（POI2／步行2），与前述40次转发探针分开统计，不用浏览器请求数推算上游。4张截图逐张打开：`01-workspace-desktop`、`02-budget-desktop`、`03-workspace-400px`、`04-logged-out-400px`。
- 首次UI精确文本定位与实际完整句不符，等待超时但生成已成功；修正定位后读取同一结果，没有重新生成。记录导出时Playwright动态import不可用，另行读回观察并写入脱敏收据；没有业务修补或隐藏失败。
- 验收后禁用临时账号、撤销会话、删除临时凭据并关闭专用服务／浏览器；普通后端8000未重启未写原库，前端另以8000目标重建为`hNQip7fE5CwPu1PIoL02h`并恢复3000，页面／健康200。两份构建仅可信后端目标不同，不冒称后者再完整Chrome重验。原五缓存及旧库／默认额度库／身份锚哈希核对不变。62个本地文档链接、9交付文件的敏感模式与锁文件registry检查、差异检查通过；模式扫描不代表完整秘密扫描。

## 服务器后续同步（本轮不执行）

1. 先检查服务器工作区和部署用户，不覆盖环境文件／数据库；fetch后正常fast-forward到本轮提交，若分歧则停止。不要reset或audit fix --force。
2. `cd frontend && npm ci`，保留optional依赖和项目脚本拒绝策略；检查`npm install-scripts ls --json`、`npm ls next eslint-config-next sharp source-map-js brace-expansion`，并用`node -e 'require("unrs-resolver"); console.log(require("sharp").versions)'`验证Linux原生包。失败时查锁文件、架构和optional安装，不先批准下载脚本。
3. 分别执行完整audit和`npm audit --omit=dev`；预期前者仍5项high、后者0，但以服务器当时公告库为准，有新告警则重新评估，不隐瞒exit1。
4. 明确服务器后端origin构建`TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build -- --webpack`，同值启动；构建需要devDependencies，不在构建前omit=dev。1.6GiB内存＋swap的实际构建能力本轮**未验证**，不能承诺不OOM；应监测并按部署方案处理，不在本轮修改服务器。
5. 业务服务／systemd、Nginx转发上传限额与超时、HTTPS、域名审核、生产安全Cookie与精确origin、备份和监控仍需部署验收。既有RELEASE_1C/1D/1E边界保留；这次补丁不等于全部上线门槛通过。

## 交付与停止点

仅提交package.json、package-lock.json、项目.npmrc、两份转发工具／测试、本报告、README、PROJECT_CONTEXT及PROGRESS。证据、缓存、数据库、环境文件与浏览器日志不提交。普通commit/push后停在依赖补修，不发布公网、不新增业务功能。
