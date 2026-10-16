# media-2A：短视频抽帧预览

2026-10-16，实际基线 `e2f3d32042edbca05b15903d9eb80ffaff50ad6b`，main；开工 fetch 后与 origin/main 同步。**短视频抽帧预览已实现并完成本轮限定验收；尚未识别视频地点。** 模型、高德及其他外部业务 API 调用均为 **0**。没有修改 media-1 提示、契约、标准、旧评分或台账。

## 用户路径与契约

正式首页新建／修改需求表单中的“短视频画面准备”是独立入口：选本地文件仅创建浏览器 Object URL → 本地播放器 → 主动“提取画面” → 带目标时间的缩略图 → 点击或 Enter 放大。没有“识别画面”“加入必去”占位操作，也不会调用照片接口。

新增 `POST /media/video-frames?request_id=...`：请求为原始二进制，不用 multipart，不传文件名、来源 URL、城市、POI 或行程；query 只接受一次有界 request_id。响应独立版本 `video-frames-v1`，包含 request_id、视频时长、转正尺寸、旋转角、计划采样数及实际 frames（序号、目标秒数、尺寸、JPEG Base64）。前后端 exact 校验、顺序／公式交叉校验和大小限制；不改 Place、ScheduleResponse 或 saved-trip-v1/v2/v3。

照片入口、地点确认及取消编辑规则保持。视频数据仅在该入口的临时状态中，不并入需求、草案、费用、候选或保存数据。确认需求／切城／进入照片或普通地点选择器会关闭视频入口；取消编辑保留原工作。输入变化只取消，不自动重新提取。

## 输入、采样和资源边界

| 项目 | 本轮固定规则 |
| --- | --- |
| 输入 | 一次本地 MP4，H.264；非空且 ≤20 MiB；视频流和容器时长均有效、>0且≤30秒；单个非封面视频流，最多8个总流；音轨不提取、不用于结果 |
| 尺寸 | 编码尺寸长边≤1920、像素≤2,073,600；方形像素／未声明SAR，拒绝非方形比例；仅接受可解释的0/90/180/270度旋转，FFmpeg转正；不猜缺失尺寸或时长 |
| 容器 | 不信任扩展名/MIME；核对 MP4 ftyp 品牌、边界完整的 box 和 ffprobe 实际流／编码；box ≤4,096、嵌套≤8；拒绝外部 dref、播放列表、伪格式、损坏、多／无视频流 |
| 采样 | `n=min(6,ceil(视频流时长/5))`，第i目标为`(i−0.5)×时长/n`。逐目标从视频首帧PTS归零后解码，取达到目标的第一帧；最多6张、保留目标序号和分辨率 |
| 时间含义 | UI“约xx秒”是相对首视频画面的**目标采样时间**，不是精确帧PTS；不声称智能镜头选择或找全地点 |
| 缺帧/重复 | 没有达到目标的帧则不补帧；解码RGB完全相同按hash去重，不做视觉相似判断。数量不足明确显示，不复制凑数；全无画面返回`no_frames`。极低帧率短片可能无目标后帧，明确失败而不伪造 |
| 输出 | JPEG长边≤1280；每张≤512 KiB，重新编码去除EXIF/comment等不必要元数据；整批JSON≤5 MiB；超限报错，不截断后当成功。前端流式接收也受5 MiB约束 |
| 截止 | 上传30秒；上传完成后校验/落盘、ffprobe及全部抽帧共享20秒；前端等待60秒，无自动重试。取消清理可能增加服务端返回时间，不声称精确20秒返回 |
| 容量 | 单后端进程最多1个视频任务，包括上传和清理，忙时429，不排队；不是账号／多实例限流。FFmpeg CPU/解码/滤镜/编码线程限1，单次分配上限64 MiB（**不是整个进程内存硬上限**），probe输出≤64 KiB，stderr丢弃 |

## 依赖、临时文件和取消

开工时没有 FFmpeg/ffprobe。经环境权限流程安装官方 Homebrew `ffmpeg` formula **9.0.1_1**（二进制报9.0.1）及其依赖，不增加 Python/npm 包或更新锁文件。实际执行路径：

```text
/opt/homebrew/bin/ffmpeg
/opt/homebrew/bin/ffprobe
/opt/homebrew/Cellar/ffmpeg/9.0.1_1/
```

本机复现安装：`HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 brew install ffmpeg`；再用两个绝对路径执行 `-version` 核对。本轮测试版本固定记录为9.0.1；未来 Homebrew可能提供新版，需重新验证兼容性，不能把此命令当作永久版本锁。来源：[Homebrew formula](https://formulae.brew.sh/formula/ffmpeg)、[FFmpeg fd协议](https://ffmpeg.org/ffmpeg-protocols.html#fd)、[MOV/MP4选项](https://ffmpeg.org/ffmpeg-formats.html#mov_002fmp4_002f3gp)。

只从固定可信目录 `/opt/homebrew/bin`、`/usr/local/bin`、`/usr/bin` 选择成对可执行文件；不接收客户端或环境任意可执行路径，不使用shell。查找是懒加载，缺依赖只令本视频接口503，其他功能仍可启动。当前验收平台是macOS；其他平台／FFmpeg版本未实测，须具备`fd`协议及本轮选项支持。

每请求创建随机私有 `trippilot-video-*` 临时目录（0700），服务端固定名 `input.mp4`（0600），不使用用户文件名。输入临时落盘，输出走有界stdout／内存，不放公开静态目录或数据库。输入只能从服务端私有文件继承的stdin读取；解码器 `protocol_whitelist=fd`、`format_whitelist=mov`，并关闭`enable_drefs/use_absolute_path`，容器解析先拒绝外部数据引用。因此不是仅靠前端禁止URL；不启用file/http/https协议，不传上游密钥环境。此约束不是完整OS沙箱或公开多用户上传安全准入。

上传期间由`Request.stream()`独占ASGI receive；上传完成后才交给唯一断开监听器，涵盖校验/写入、探测和抽帧。中断时取消处理，TERM子进程组，0.3秒未退出则KILL并等待回收／排空管道；重复取消通过shielded清理不会跳过回收。写文件／JPEG线程真正完成前同样不释放容量。TemporaryDirectory清理后才退出忙状态；不留下永久busy。服务器被SIGKILL或主机崩溃的遗留文件清扫不在本轮保证内，公开部署需补隔离／运维策略。

前端同时使用AbortController、单调版本及会话身份，隔离旧success/error/finally；同文件再选、A→B→A、切城、关闭、取消编辑、卸载均失效。视频／图片Object URL在替换、关闭、取消编辑及卸载时释放。无模型调用，所以不存在本阶段模型取消计费承诺。

## 本轮检查

在实际仓库运行；Python均`-B`，磁盘测试只用独立临时目录／既有临时数据库测试，不写用户库：

- 后端：`.venv/bin/python -B -m unittest discover -s tests -p test_video.py -v`：**15通过**；完整`.venv/bin/python -B -m unittest discover -s tests -q`：**530通过**。
- 前端：视频／照片／需求表单定向 **73通过**；新增正式工作区视频取消组合回归所在文件 **29通过**；最终 `npm test -- --reporter=dot`：**62文件／1,259通过**。
- `npm run lint`、`npm run typecheck`、`npm run build -- --webpack`、`git diff --check`通过。生产构建后仅补组合测试，没有变更生产代码；本轮重启了核对过工作目录的前后端，未改配置。
- 真实FFmpeg测试覆盖横／竖／90度旋转（额外用非对称色块核对方向）、RGB时间区间、短／重复／30秒/1280缩小、31秒／过分辨率／mpeg4／双流／仅音频／假文件／损坏／外部dref拒绝。ASGI受控测试覆盖上传截止／断开、验证线程断开、处理超时／取消、重复取消、spawn未完成就取消、忽略TERM的子进程KILL／回收、输出超限、临时目录删除和容量不提前释放；同一receive并发读者≤1。
- 前端覆盖主动单次请求、流式响应上限、60秒取消、非法结构、不修补数据、URL释放、关闭重开／文件／城市ABA晚三分支、仅编辑与取消不改正式timeline/cost/map；Python实际抽帧输出→TypeScript解析通过。原七日、地图、费用、保存v1/v2/v3与照片回归保留。
- 首轮发现测试复用了已消费的Starlette Request；修正为每次独立Request。旋转样本改用FFmpeg9的`-display_rotation`；极短空输出需要明确JPEG像素格式，修为`yuvj420p`后无帧返回受控错误。保留有效短片验证，未删断言绕过。既有Starlette/httpx弃用及Turbopack限制未顺手处理。

## 真实Chrome与证据

稳定目录：[batch-20261016](../../trippilot-evidence/media-2a/batch-20261016/)。[样本来源与hash](../../trippilot-evidence/media-2a/batch-20261016/sample-manifest.json)、[脱敏操作和请求计数](../../trippilot-evidence/media-2a/batch-20261016/browser-receipt.json)。截图均逐张打开检查；没有模型、抽帧假响应或高德查询。

| 操作 | 实际结果与证据 |
| --- | --- |
| 选文件／本地播放 | 本轮生产首页，12秒640×360 H.264自制视频，本地Blob播放器；选择后后端请求0。[01](../../trippilot-evidence/media-2a/batch-20261016/01-local-preview-no-upload.png) |
| 主动抽帧／Enter放大 | 真实后端FFmpeg，3张分别红/绿/蓝，画面文字2.0/6.0/10.0秒与目标一致；Enter提取并放大，焦点进入预览。[02](../../trippilot-evidence/media-2a/batch-20261016/02-controlled-2-6-10-seconds.png) |
| 真实摄影短片 | MDN CC0 flower.mp4原文件，非合成：视频5.005秒/960×540，2张目标1.25125与3.75375秒，JPEG42,014/43,473字节；旧缩略图已清空，换文件不上传，点击才解码。[03](../../trippilot-evidence/media-2a/batch-20261016/03-real-CC0-flower.png) |
| 400px／关闭／重开 | 页面scrollWidth400，Enter选择第二帧，图片无溢出；关闭7个URL全部释放，重开无文件／旧结果；同文件再选和切城后9创建/9释放，无追加抽帧。[04](../../trippilot-evidence/media-2a/batch-20261016/04-real-video-400px.png) |
| 需求编辑／取消 | 只读一次已有杭州历史副本，400px下同一真实花朵片重新主动解码；取消后页面正文与编辑前一致，3个URL全释放。[05](../../trippilot-evidence/media-2a/batch-20261016/05-edit-real-decode-400px.png)、[06](../../trippilot-evidence/media-2a/batch-20261016/06-edit-cancel-retains-work.png)。该副本没有新正式草案，不冒称真实重生成／地图回归；正式草案+费用+地图取消保留由上述组合测试证明 |

真实摄影来源：[MDN video示例](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/video)、[原片](https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4)、[仓库CC0许可](https://github.com/mdn/interactive-examples/blob/main/LICENSE)。发布者MDN，未查得单独摄影者署名，不编造；原片不改动。自制片由Pillow绘制60帧、5fps、12秒RGB时间字卡再用FFmpeg/libx264编码，来源／处理与真实花朵片分开。都不包含私人内容，不提交视频、图片或证据。

浏览器计数：**3次视频POST成功＋1次已有存档GET**；选帧／放大／关闭／重开／切城不新增业务请求。未触发照片、模型、POI、候选、排程、路线、天气或存档写入。验收页有只允许本地前端、视频接口和存档读取的防误触护栏，实际被阻断请求0；没有用拦截响应冒充抽帧。原生响应监听未取得全部正文，只对第二次安全克隆提取所需元数据，其余按真实页面与严格解析核对，没有为补元数据重发。无原始网络日志、图片Base64或用户视频进入证据记录。

公开文档／依赖和许可样本下载属于开发素材获取，不是业务外部API或付费模型调用。本轮没有浏览器自然断网／极限上传故障；超时、竞态、进程忽略TERM为明确受控测试。实体手机、Windows/Linux、各设备拍摄格式、恶意媒体全覆盖及公开上传平台未验收；保留成都公交等历史限制。

## 文件清单与停止点

仅本轮17个交付文件：

- `backend/main.py`、`backend/app/api/video.py`、`backend/app/schemas/video.py`、`backend/app/services/video.py`、`backend/tests/test_video.py`。
- `frontend/src/lib/video-api.ts`、`video-api.test.ts`；`frontend/src/components/places/video-frame-picker.tsx`、`video-frame-picker.test.tsx`；`frontend/src/components/trip-request-form.tsx`、`frontend/src/components/trip/formal-planning.integration.test.tsx`。
- `AGENTS.md`、`README.md`、`docs/PROJECT_CONTEXT.md`、`docs/ROADMAP.md`、`docs/PROGRESS.md`、本文件。

原五缓存、数据库及开工记录的581个旧证据／评测／保护文件hash不变；浏览器日志仅新增工具自己的记录，不提交，也不删除旧日志。普通commit/push仅本清单，不包含依赖二进制、媒体、环境、证据或存档。

**停在media-2A。** 可演示“本地选择→主动抽帧→约时间→Enter放大→关闭”，不展示景点识别或加入按钮；抽帧可能遗漏短暂场景。media-1保持限定收口和全部旧失败／独立台账；下一阶段的视频识别、多地点发现及任何真实模型调用均未授权或实施。没有数据库迁移。
