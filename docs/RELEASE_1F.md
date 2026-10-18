# release-1F：可重复的 Linux 部署包与分步手册

## 状态、来源与停止点

基线 `4e009851b6dee24e7192d60c5dc6ce5b9d3e2a25`，main，开工fetch同步。本轮只准备脚本／模板／离线验证；**没有登录、修改云服务器、创建真实账号、签发证书或完成公网部署**。真实千问／高德／其他业务API调用0，业务源码、数据库结构和已有用户库不变。

以下**由用户终端输出提供，非代理实测**：阿里云香港轻量服务器，Ubuntu24.04 x86_64/admin，约1.6GiB RAM＋2GiB Swap／40GB磁盘；Node24.22.0/npm11.19.1/uv0.13.1/Python3.13.16，Nginx active；代码`/home/admin/apps/trippilot`。此前服务器Webpack成功，backend origin为`http://127.0.0.1:8000`；完整audit仍5项开发链high、生产0。域名`trip.zijinglab.com`据用户已解析到`8.217.79.146`，HTTPS尚未配置。**Linux原生模块独立输出、真实命令路径、systemd/Nginx版本、权限与容量仍须下面检查**。

本地验证证据：[release-1f-batch-20261018.TcsRmO](../../trippilot-evidence/release-1f-batch-20261018.TcsRmO/)。无媒体／数据库／密钥／浏览器日志入库。该轮无浏览器验收，不能用Mac测试冒充Ubuntu运行。

## 布局与身份

| 路径／身份 | 职责与权限 |
| --- | --- |
| `/home/admin/apps/trippilot` | admin工作副本、Linux已有构建来源；不是生产运行目录 |
| `/opt/trippilot/releases/<40位commit>` | root拥有的完整发布目录，不覆盖重用；失败半成品保留调查 |
| `/opt/trippilot/current` | 明确选择的release链接；切换先停两服务，比较预期旧目标，无自动启动 |
| `/opt/trippilot/python` | uv在最终路径安装的独立CPython3.13.16，root拥有；不复用admin家中的解释器 |
| `<release>/backend/.venv` | 在最终绝对路径`uv sync --frozen --no-dev`新建；不搬旧venv、不放宽/home权限 |
| `/etc/trippilot/backend.env` | root:root0600，仅PID1读取并注入API；不供前端读取 |
| `/etc/trippilot/frontend.env` | root:trippilot-web0640，仅后端origin＋浏览器公开JS Key；没有Web Key、安全码、Qwen Key |
| `/var/lib/trippilot` | trippilot-api独占0700；identity.sqlite3、usage.sqlite3、usage.sqlite3.identity均0600；另有初始化防误用标记 |
| `/var/backups/trippilot` | root0700，备份含私人资料，不是公开证据；新批次／恢复演练不得覆盖 |
| `trippilot-api` / `trippilot-web` | 两个无home／nologin低权限system身份，非同UID/GID；不属于对方组 |
| `<release>/frontend/.next/cache` | 唯一web可写目录0700；新建空目录，不复制旧cache |
| `/var/www/trippilot-acme` | 只用于HTTP-01公开挑战文件，不存配置或业务数据 |

不引入Docker、Redis、数据库服务或队列。SQLite适用于当前单机低并发邀请内测；账号库／持久调用额度和旧v1/v2/v3语义不变，不进行旧无归属库迁移。

## 构建复用的可信边界

优先复用**同一台Linux**已有`.next`与`node_modules`，不复制Mac产物。`attest`要求当前HEAD、受控tracked clean（只排除已知两项旧tracked字节码）、锁文件和Next16.3.8、BUILD_ID、完整22条rewrite及精确backend origin；绑定源码、build/static、全部node_modules字节和内部相对链接的SHA256。大型文件以64KiB流读取；`.next/cache`明确不参与。源`.env.local`可以存在，但工具不读取／复制它。最终release不允许真实dotenv。

**既有构建原本没有编译时receipt，事后不能证明编译提交或内联公开Key。** `--confirm-existing-build`是操作员核对来源后的声明，不是签名或供应链认证。本轮部署文件不改frontend；可以先`git diff --exit-code 4e009851b6dee24e7192d60c5dc6ce5b9d3e2a25 HEAD -- frontend`核对与用户报告成功的构建源码相同。公开Key须与当时`.env.local`一致，工具只存其声明hash；不能靠运行时换变量修改已内联Key。

最终路径再以web身份加载Linux SWC／unrs／Sharp（含librsvg补丁）并启动临时127.0.0.1:3102，只GET页面壳和一个静态资源、比较哈希后回收进程。**不执行浏览器JS／登录或任何业务API**；不是公网验收。任一检查失败停止复用，不删receipt迁就差异。若来源无法确认、前端源码／公开Key／锁发生变化、缺产物或native失败，应先记录具体原因，再由操作者在Linux明确重建；不是默认机械重建。

## 服务器分步执行（本轮未执行）

每段确认预期后再执行下一段，不整页粘贴。异常／已有不同文件／半份发布目录时停止，不`reset/clean/rm -rf`，不要删除数据库、锚或receipt。`release.py`管理命令使用`/run/lock/trippilot-deploy.lock`互斥，另一操作进行时停止，不排队；备份另依赖停服务、无其他writer声明及前后摘要校验。不要另开终端并行手改服务／配置。脚本没有连接远端、自动重试、自动清理、自动创建账号或签证书的功能。分段操作若更换终端，先重新`cd /home/admin/apps/trippilot`并设置`COMMIT=$(git rev-parse HEAD)`，核对仍为同一提交。

### 1. 只读核对

```sh
cd /home/admin/apps/trippilot
git status --short
git fetch origin
git rev-list --left-right --count HEAD...origin/main
# 无冲突且已确认仅正常前进时：
git pull --ff-only
COMMIT=$(git rev-parse HEAD)
python3 -B deploy/release.py preflight
git diff --exit-code 4e009851b6dee24e7192d60c5dc6ce5b9d3e2a25 HEAD -- frontend
command -v node npm uv
ss -ltnp
```

预期：main正常前进、frontend差异为空，`Linux/tool versions checked`；Node实际须在`/usr/bin/node`，systemd255，3000/8000未被其他程序占用。版本／路径不同先报告，**不放宽家目录或擅停未知进程**。不把版本检查当Linux权限／资源验收。

### 2. 布局、解释器与配置安装（不启动）

```sh
sudo python3 -B deploy/release.py layout
sudo python3 -B deploy/release.py runtime --uv-source "$(command -v uv)"
sudo python3 -B deploy/release.py config-templates
sudoedit /etc/trippilot/frontend.env
sudoedit /etc/trippilot/backend.env
sudo python3 -B deploy/release.py check-config
```

预期：独立身份／目录通过；可信现有uv0.13.1只复制二进制到`/usr/local/bin/trippilot-uv`（不覆盖不同文件），其官方下载将Python安装在最终`/opt`目录，不动admin原解释器。既有runtime不完整时停止，不自动清理重下。安装需要网络与磁盘，**此处用户将来执行的依赖下载不是本轮已发生的业务API调用**。

配置模板不是实际.env副本。密钥由用户在服务器本机填写，不贴聊天／日志；公开JS Key两文件一致且与既有构建一致，服务端Web Key／安全码只写backend.env。空凭据允许其他功能启动，但相关查询未配置；不能把这种启动当功能可用。保留模型、费用限制的既有默认值，次数不是人民币上限，是否开启供应商调用由操作者单独审查。

**语法区别**：本机`backend/.env`由python-dotenv读；本包是systemd EnvironmentFile的受限子集。写`KEY=value`，JSON写`KEY='["https://trip.zijinglab.com"]'`（外层单引号由systemd移除）。不使用export、shell source、变量替换、反引号、转义、多行或行尾注释；不能直接复制dotenv中任意复杂写法。check-config只报安全原因，不输出值。编辑后的配置再次运行config-templates会明确停止（不同内容不会覆盖），应使用check-config验证。

### 3. 复用已有Linux前端构建，发布到最终目录

```sh
sudo python3 -B deploy/release.py attest --commit "$COMMIT" --confirm-existing-build
sudo python3 -B deploy/release.py stage --commit "$COMMIT" --receipt "/opt/trippilot/receipts/$COMMIT.json"
sudo python3 -B deploy/release.py backend-deps --commit "$COMMIT"
sudo python3 -B deploy/release.py verify-frontend --commit "$COMMIT"
```

预期：新receipt、`Release staged`、backend imports OK、Linux native／final-path startup与static hash通过。receipt只新建一次；重复attest拒绝。stage只从git archive取tracked内容并排除env／data／字节码／日志；只另拷同机`.next`和node_modules，不拷venv。原生模块检查失败必须保留失败，不批准unrs下载脚本或强行覆盖peer。完整audit的5项dev high及ESLint9 EOL仍按[补修报告](DEPENDENCY_SECURITY.md)保留，不用force清零。

若确需重建：先不要stage；保留旧构建、旧BUILD_ID、失败receipt与原因到服务器私有新目录，确认没有应用在该目录运行后，另行评审一次`npm ci`与`TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:8000 npm run build -- --webpack`。低内存服务器的重建容量未由本轮保证；重建后创建新有身份的receipt方案需先调查旧文件，不删旧记录重复冒充同次构建。**不能在发布失败后拷Mac产物兜底。**

### 4. 显式一次性初始化（不创建账号）

```sh
sudo python3 -B deploy/release.py init-identity --commit "$COMMIT"
sudo python3 -B deploy/release.py init-usage --commit "$COMMIT"
sudo python3 -B deploy/release.py verify-data --commit "$COMMIT"
```

预期：新私有库、独立额度库／锚和`.usage-initialized`标记完成，然后read-only verified。wrapper经systemd-run精确加载backend.env并以API身份运行，禁止直接root执行data_admin。已有库仅校验，不迁移／修表；孤立WAL/SHM/journal、半份额度库或锚、损坏、未知版本均停止。已有身份数据但缺额度库不当作首次新安装；标记存在却丢库也拒绝重建，不能借重装返额。此处不处理旧本机用户库。

### 5. 选择release，安装并验证两个unit

```sh
# 首次无current用none；升级先读link并把--expected-current设为准确旧SHA。
sudo python3 -B deploy/release.py select --commit "$COMMIT" --expected-current none
sudo python3 -B deploy/release.py install-units
sudo systemd-analyze verify /etc/systemd/system/trippilot-api.service /etc/systemd/system/trippilot-web.service
sudo systemd-analyze security trippilot-api.service trippilot-web.service
```

预期：current指向准确release，选择前核对backend lock及frontend native/static验证receipt内容与hash，而非仅检查文件存在；损坏／半写／不匹配先停止。安装unit只daemon-reload、不启动；verify无错误／未知指令。security评分不是安全合格证明。API单worker／8000、Next3000仅回环；CPUQuota每服务100%代表单CPU比例，不意味着服务器核数。API MemoryHigh512M/Max768M、web384M/512M（Node heap320MiB）、TasksMax128是**起始保护值**，需服务器观察OOM／RSS／swap与并发后才作容量判断。不能用增加worker绕过媒体容量。

ProtectSystem/ProtectHome/PrivateTmp/NoNewPrivileges等按Ubuntu24.04的systemd255文档准备；Mac只做文本和故障测试，实际沙箱／文件挂载／Python共享库／原生模块权限待该机验证。API启动前只做read-only数据检查，不自动初始化；web看不到backend.env、backend代码／数据和备份，只有cache可写。没有Node JIT不兼容的MemoryDenyWriteExecute。

### 6. 可选：FFmpeg兼容性，默认视频不可用

```sh
# 若未安装：先单独评估/安装受维护的可信FFmpeg版本，不能因依赖存在就判通过。
sudo python3 -B deploy/release.py enable-video --commit "$COMMIT"
```

该命令显式执行无外部请求的`check-video.py synthetic`：固定可信路径、版本／fd输入协议／MOV安全选项，12秒合成三色视频通过**原生产解码逻辑**验证2/6/10秒。通过后才记录本release兼容性并安装小drop-in，保留备份不可读；不会自动重启。失败exit3或依赖不支持时**保持base unit对所有可信ffprobe/ffmpeg路径的屏蔽**，既有接口返回`video_unavailable`，其他规划功能可部署。不能改为pipe／任意URL／不安全FFmpeg参数兜底。Ubuntu仓库版本是否支持fd不能凭版本名猜测。

可不执行这一步并明确首发视频不可用；基础照片能力不依赖FFmpeg。已有video-enabled drop-in时，新release选择前须重新完成此release兼容性检查，否则select停止。二进制升级后也须再检查；本轮没有真实Linux解码或HTTP上传验收，合成提取也不代替未来真实视频验收。

### 7. HTTP证书入口与内部启动（仍不公开HTTP应用）

```sh
sudo python3 -B deploy/release.py install-http
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl enable trippilot-api.service trippilot-web.service
sudo systemctl start trippilot-api.service trippilot-web.service
systemctl is-active trippilot-api.service trippilot-web.service
curl --fail --silent http://127.0.0.1:8000/health
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:3000/
ss -ltnp
```

预期：units active，health `{"status":"ok"}`、页面200、端口仅127.0.0.1；HTTP域名普通路径**503**，只ACME挑战目录开放，无任何缺失证书引用。Nginx现有不同站点文件／symlink停止不覆盖；本脚本不删除default或其他站点，若有重复server_name警告也须停止调查，不以nginx-t退出0掩盖冲突。

服务启动失败时停止推进：`systemctl status ... --no-pager`、本机有界`journalctl -u ... -n 50 --no-pager`自行检查；日志不是公开证据，分享前脱敏。StartLimit防止无限重启；修正原因后才能明确reset-failed／start，不自动重试排障。

### 8. 独立证书操作，随后才启用TLS

先由用户核对80/443的云防火墙与域名记录，保留SSH；不要公开3000/8000。此包不修改云防火墙／DNS。

```sh
certbot --version
# 仅缺失时，单独确认包管理计划后安装，不混在发布脚本：
# sudo apt-get update
# sudo apt-get install certbot
sudo certbot certonly --webroot -w /var/www/trippilot-acme \
  -d trip.zijinglab.com --cert-name trip.zijinglab.com
# 交互完成邮箱／条款，证书文件实际存在后再继续：
sudo python3 -B deploy/release.py enable-tls
sudo nginx -t
sudo systemctl reload nginx
curl --head https://trip.zijinglab.com/
```

预期：certonly仅取得证书，不让插件改Nginx；TLS安装检查精确fullchain/privkey后才从已知HTTP链接切换，语法失败恢复本次改动前的链接、不reload。HTTPS200，HTTP308到固定域名。没有现成证书时enable-tls明确停止。签发／续期会访问ACME，**须用户在服务器单独执行，本轮未执行**。

TLS站点固定127.0.0.1:3000，不新增上游参数；21MiB入口、90秒空闲超时，HTTP1.1、非缓冲上传／响应、不跟随重试、不忽略client abort。它们不代替原照片5MiB／视频20MiB、业务总截止或Next75秒空闲限制。实际HTTPS穿透上传／取消仍待服务器验收。关闭本vhost的access和error日志，避免URI查询／凭据落入默认格式；牺牲按请求诊断，并不代表系统所有stderr都已脱敏或监控完整。

续期另行核对：`systemctl list-timers --all certbot.timer`，成功签发后人工执行`sudo certbot renew --dry-run`；检查实际定时器及成功续期后的Nginx reload hook，否则Nginx可能仍持有旧证书。不要反复签发刷成功或在没有证书时预装TLS配置。

### 9. 邀请账号和最小验收

```sh
sudo python3 -B deploy/release.py invite --commit "$COMMIT" your_invited_username
```

预期：原account_admin的交互密码提示两次，TTY内输入12～128字，不出现在argv／env／日志。**本轮未执行、未创建真实账号。** 在HTTPS浏览器登录，检查__Host-/Secure/HttpOnly/SameSite=Lax、精确Origin/CSRF、退出及400px；未登录API401，不借验收随手调用付费功能。登录／身份本身会写生产库，须由用户明确进行，与本轮离线测试分开。

## 回退、备份和隔离恢复

代码回退只选择已经验证、与identity-v1/business-usage-v1兼容且保留权限／额度保护的旧release；**不能退到release-1D以前或未保护付费入口**。先停web/API、做一致备份，再`select --commit OLD_SHA --expected-current CURRENT_SHA`并验证／启动。配置／secret和持久目录不跟随current，也不随代码rollback。旧浏览器状态会丢失，应提示用户先保存。脚本不自动恢复旧数据。

```sh
sudo systemctl stop trippilot-web.service trippilot-api.service
# 确认没有仍运行的邀请CLI、备份外的writer或临时服务；批次名称须全新。
sudo python3 -B deploy/backup.py backup --batch before-release-example --confirm-no-other-writers
sudo python3 -B deploy/backup.py verify --batch before-release-example
sudo python3 -B deploy/backup.py restore-drill --batch before-release-example --drill rehearsal-example
# 验证后仅启动原服务，不将恢复演练目录当生产路径。
sudo systemctl start trippilot-api.service trippilot-web.service
```

预期：root0700新备份，两个SQLite.backup自包含副本（含已提交WAL）＋锚＋哈希manifest，schema／quick_check／引用约束／前后逻辑摘要验证；原库不checkpoint、不覆盖。**跨库一致依赖服务已inactive/dead且操作员确认无其他writer**，不是任意并发写下的跨数据库原子快照。重复批次／破损／不匹配／symlink／硬链接／宽权限停止。

恢复只创建`/var/backups/trippilot/restore-drills/<new-name>`，清除副本auth_sessions，保留账号／存档／登录限流；额度库／锚逐字节保持备份值。原会话、密码哈希和存档都属敏感数据，备份不进Git／公开证据，不新建公开下载入口。**离线恢复演练不批准生产切换**：旧备份可能早于已发送的调用，不能据此返额；真实灾难恢复须先保持收费功能阻断、对照最新台账／供应商记录保守核账，并保证会话撤销／密码策略。不得把旧会话库或落后台账直接放回live。没有自动定时备份／保留清理／异地加密备份／自动RPO/RTO保证，40GB磁盘必须监测。

## 实际验证、文件与未完成项

本轮仅部署工具改动，无业务前后端源码／依赖变化。执行命令与最终数量记录在最新PROGRESS；测试使用合成临时库／文件／进程替身，包含实际生产新库schema和真实临时Git archive，不读写原库。

```sh
backend/.venv/bin/python -B -Werror::ResourceWarning -m unittest discover -s deploy/tests -q
backend/.venv/bin/python -B -m unittest discover -s deploy -p test_check_video.py -q
node --test deploy/verify-frontend.test.mjs
node --check deploy/verify-frontend.mjs
python3 -B deploy/release.py --help
python3 -B deploy/backup.py --help
git diff --check
```

Linux/OS服务硬化、Nginx真实解析／证书、最终路径venv/native/static、1.6GiB资源、真实视频、外网Cookie／CSRF／上传取消／重启／DNS均**服务器待验**。本机无systemd-analyze／Nginx，未冒称命令通过；Mac CLI实际拒绝Linux预检。默认视频屏蔽与可选解锁也需实际确认503／沙箱行为。依赖审计仍5dev high、ESLint9 EOL、成都公交和旧六日未知根因、媒体限定质量等历史风险全部保留。准备部署包不是全部公开上线门槛通过。

交付：`deploy/`的release/config/data_admin/backup、前端产物与视频检查、模板及定向测试；本页、README、PROJECT_CONTEXT、PROGRESS。不提交生成receipt、媒体、真实配置、数据库、缓存、截图或日志。**停在release-1F部署包准备与提交，由用户按段执行服务器操作，不进入新功能。**

官方依据（2026-10-18读取，非服务器实际版本证明）：[Ubuntu noble systemd.exec](https://manpages.ubuntu.com/manpages/noble/man5/systemd.exec.5.html)、[systemd255资源控制](https://www.freedesktop.org/software/systemd/man/255/systemd.resource-control.html)、[service](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html)、[Nginx代理](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[HTTPS](https://nginx.org/en/docs/http/configuring_https_servers.html)、[uv Python路径变量](https://docs.astral.sh/uv/reference/environment/)、[venv不可搬移](https://docs.python.org/3.13/library/venv.html)、[SQLite backup](https://www.sqlite.org/backup.html)、[Certbot webroot/certonly](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot)。另完整阅读本地Next16.3.8 self-hosting文档，保留公开变量编译内联、单机cache和反向代理边界。
