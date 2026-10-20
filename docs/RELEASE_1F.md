# release-1F：可重复的 Linux 部署包与分步手册

## 2026-10-20：MySQL与注册增量的部署边界

用户在旧版本上已报告服务active、HTTPS200及成功登录；不是本轮代理远程验收。最新实现新增邀请码注册和MySQL全迁能力，**线上库仍未由本轮切换**，先读[MYSQL_REGISTRATION](MYSQL_REGISTRATION.md)再决定升级。

部署配置支持仅后端两个可选键，旧配置不被自动重建；MySQL模式`verify-data`验证实际MySQL+原额度锚，旧SQLite初始化明确拒绝。创建新的SQLite备份会先读取受保护活动配置，MySQL模式拒绝误备旧库，须改用新MySQL一致性备份工具。旧备份验证／隔离演练仍可使用，不授权回滚额度。业务源码和前端改变须新SHA／新构建／新receipt，旧19资源产物和新增注册后的20资源均有明确白名单兼容，不能把旧构建当本轮新功能。

本轮只在本地独立MySQL和受控浏览器验证，不执行服务器安装／迁移／新账号创建或服务切换。下面旧失败、续行步骤及当时未部署的结论均保留原文，不误读为当前服务器重新失败。

## 2026-10-19：发布产物读取权限补修（当前续行入口）

实际基线 `4b07a68559e8aaa911494b36fd3172358a89f622`，main，fetch与origin/main同步。本轮**用户提供的服务器证据**：更新控制器后Node验证器已可启动；verifier_load／receipt_read通过，artifact_hashes报EACCES。定点诊断为open `.next/BUILD_ID`；namei显示所有祖先目录755，目标`.next`为root:root 700、BUILD_ID为root:root 600；工作副本同目录／文件分别为admin:admin 700／600。没有证据表明原生模块检查已经执行或失败；应用、生产库初始化与HTTPS仍未完成。本轮没有登录服务器。

**首因与取舍**：原扫描正确拒绝共享写位、异常归属／链接等，但允许仅owner可读的合法构建；`shutil.copytree`保留700／600，发布副本归root，导致低权限web服务无法读取。这是发布权限衔接缺陷，不是原构建内容错误，也不要求重建前端。新stage仅对**新复制的node_modules／.next公开代码产物**补齐读／穿越权限：目录`mode | 0555`，普通文件`mode | 0444`，原有任一执行位则补齐111，无执行位文件不添加执行；不增加共享写位、不chown、不修改工作副本。文件444仍是444。源码归档原本已采用644／755，此规则与root拥有代码、web只读运行的原架构一致；不是对配置／私密数据开放读取。

### 已暂存目标的显式审计修正

新增`release-permissions --commit FULL_SHA`，默认只检查一个已stage发布目标的node_modules及`.next`，需要修改时报告`needs_changes=N`并以`release_permissions_need_explicit_apply`非零停止；不会创建清单或修改权限。不是原`dependency-permissions`的扩展：后者仍只收敛工作副本依赖的022，原语义与清单独立保留。

仅显式`--apply`才修改，且须先通过目标root目录／元数据、release.json与原frontend-reuse.json的commit/hash绑定、**完整双树**归属／特殊位／写权限／链接／普通文件硬链接检查，以及目标实际9字段内容指纹对原receipt的核对。使用目标目录自身的原Node校验模块和服务器frontend.env的公开配置，不拿工作副本构建代替。原receipt不重写、不改分；源码、锁、BUILD_ID、build／static、全部依赖字节均沿用原校验。

首chmod前在root0700的`/opt/trippilot/receipts/`独占保存root0600 `release-permissions-<随机ID>.json`并fsync，记录原相对路径、身份、mode、元数据、目标SHA、两个元数据hash和内容指纹；这是prepared原权限清单，**不是操作成功收据**。通过逐级dir_fd／O_NOFOLLOW核对后fchmod，只改普通文件／目录mode，不修改／跟随链接、不改属主、内容或mtime。完成后两树复核且内容指纹仍须与原receipt一致，才输出`content fingerprints unchanged`。预检／清单写失败不改权限；中途失败保留私有清单及已修改现场，不自动回滚或清理。重复完成的操作为no-op，不新建清单。

`.next/cache`必须仍是web身份、0700、非链接的私有目录，先核对其身份／mode，再排除修改与遍历，前后检查不变。不触及其他发布版本、backend-ready／frontend-ready、原receipt、实际环境、数据库、源码工作副本、旧f6b7ca5失败目录或旧权限清单。发布内容仍不含真实dotenv／用户数据；该命令不是ACL／LSM通用修复工具。额外ACL／平台限制如仍拒绝，保留失败定位，不通过扩大私密路径权限放行。root指纹成功也不能代替web身份native／启动／静态检查。

### 服务器下一步：只检查，再单独批准apply（仅供用户执行）

继续使用已成功stage及安装后端依赖的目标`3a41ffdcb3f58be609cbcb0c4c2a822ab023b9c8`；新控制器版本另行记录，**不要将COMMIT改为新HEAD、覆盖旧发布或重做构建／attest／stage／backend-deps**。本次不需要重装Python、重建配置或安装前端依赖。先停止对目标发布的其他操作，逐段执行，异常不继续：

```sh
cd /home/admin/apps/trippilot
git status --short
git pull --ff-only
COMMIT=3a41ffdcb3f58be609cbcb0c4c2a822ab023b9c8
git diff --exit-code "$COMMIT" HEAD -- frontend deploy/verify-frontend.mjs deploy/systemd
sudo python3 -B deploy/release.py release-permissions --commit "$COMMIT"
```

前端／验证器／unit差异应为空。默认检查若唯一结果是`needs_changes=N`＋`release_permissions_need_explicit_apply`，属于预期停止，尚未改权限；若归属、链接、元数据、缓存等其他错误，先调查，不能跳过。确认完整检查正常后才单独执行：

```sh
sudo python3 -B deploy/release.py release-permissions --commit "$COMMIT" --apply
```

预期先输出私有原权限清单路径，之后`Release artifact permissions applied: changed=N; content fingerprints unchanged`。N以实际扫描为准，不凭BUILD_ID一项推断总数。此成功输出尚未取得，不能记作服务器已通过。保留清单，不贴其完整内容到聊天；异常保留现场停止。取得成功后分别执行：

```sh
sudo python3 -B deploy/release.py release-permissions --commit "$COMMIT"
sudo python3 -B deploy/release.py verify-frontend --commit "$COMMIT"
```

前者应为0项＋内容匹配，后者仍必须实际以web身份完成原生模块、低权限临时启动与静态hash。未通过前不初始化生产数据、选择发布、启动正式服务或签证书。

### 本次实际验证、交付与停止

部署Python **100通过**（原71＋发布权限核心13＋controller16），Node产物 **14通过**。真实临时700／600及执行位、内部链接、inode／mtime／owner／内容不变、cache私有排除、硬链接／越界／特殊／写位／陈旧plan、私有清单写失败／部分修改、默认只查／no-op和CLI显式目标均覆盖；旧stage真实git archive测试补上私有mode的发布复制与源不变。另实际执行Python控制器→真实Node→实际目标校验模块，核对9字段指纹修正前后一致，实际校验失败先于清单／chmod。原依赖收敛与跨进程回归仍全部通过，没有删除断言。初次新Node测试夹具误将链接放入禁止链接的`.next`，保留原拒绝并修正夹具；链接防护另有测试。不是Linux用户切换、ACL／LSM、原生模块或Next启动已实测成功。

CLI帮助、语法／文档链接／敏感信息及`git diff --check`通过。前端、Node验证器、锁文件及实际产物无变更，无需重复业务全套、前端构建或浏览器。仅7文件：`deploy/artifact_permissions.py`、`deploy/release.py`、`deploy/tests/test_release.py`、新增`deploy/tests/test_publication_permissions.py`及`deploy/tests/test_release_readability.py`、本页、`PROGRESS.md`。原五缓存、数据库、实际环境及浏览器日志共69份文件开工／收口SHA256一致，旧证据／评分／台账不改。真实模型及外部业务API调用 **0**；**停在发布可读权限补修，服务器apply／完整前端验证仍待用户执行，未部署公网**。以下历史记录和当时续行顺序保留；当前操作以本节为准。

## 2026-10-19：服务身份切换命令路径补修（当前续行入口）

实际基线 `3a41ffdcb3f58be609cbcb0c4c2a822ab023b9c8`，main，fetch与origin/main同步。**用户终端输出**确认：依赖权限收敛27035项且内容指纹不变，随后复查`needs_changes=0`、check-config、attest、stage及最终路径backend-deps成功。`verify-frontend`只返回通用失败，尚无Node检查输出。进一步只读诊断显示受控PATH查找`runuser`为None，`/usr/sbin/runuser`存在，绝对路径执行`id`成功切换为trippilot-web。以上来自用户提供的服务器输出，不是代理登录验证；应用服务、生产库初始化及HTTPS仍未完成。

**原因与最小修复**：`clean_environment()`有意只允许`/usr/local/bin:/usr/bin:/bin`，但`frontend_verification()`和`enable_video()`调用裸命令`runuser`，Ubuntu的`/usr/sbin`不在其搜索范围。前端检查因此在启动子进程时失败，尚不能据此判断SWC／unrs／Sharp或Next启动有问题。本次仅把两处命令固定为`/usr/sbin/runuser`；**不扩大PATH、不继承管理员环境、不放宽身份／产物检查，不改Node验证器、构建、依赖或真实配置**。这也修复视频兼容性命令的同类入口，但不是视频兼容性已通过。

新增4项离线回归：在隔离PATH外的临时可执行文件上真实验证裸名失败／绝对路径可启动；受控身份替身实际运行子进程，检查前端`verify --native`和`smoke`均采用固定路径、web身份及原环境，任一步失败不发布ready且不改原receipt；视频检查同样使用api身份，失败不生成ready或安装访问drop-in。替身不切真实身份、不运行Next或FFmpeg，不能冒充Linux原生／权限验收。原生产PATH另行断言不变。部署Python **71项通过**（原67＋4），原Node产物 **14项通过**，CLI帮助、语法／文档链接／敏感信息及差异检查通过。前端源码和产物未改，未重建或重跑业务全套／浏览器。

### 保留已暂存发布目录的服务器下一步（仅供用户执行）

这次只修正工作副本中的部署控制器。已经成功stage及安装后端依赖的发布目标仍是`3a41ffdcb3f58be609cbcb0c4c2a822ab023b9c8`；新控制器可以对该显式SHA执行验证，**不要把COMMIT改成新HEAD后直接验证不存在的新release**，也不需要重跑attest／stage／backend-deps。目标内的Node校验器、receipt及前端内容与本次补修前一致，验证仍核对receipt并执行原生检查／低权限启动／静态哈希，不跳过任何检查。

```sh
cd /home/admin/apps/trippilot
git status --short
git pull --ff-only
# 使用更新后的部署脚本，继续验证已经暂存的发布目标，不是新HEAD。
COMMIT=3a41ffdcb3f58be609cbcb0c4c2a822ab023b9c8
# 应无输出；若相关产物／unit源码已变，停止核对，不跳过此检查。
git diff --exit-code "$COMMIT" HEAD -- frontend deploy/verify-frontend.mjs deploy/systemd
sudo python3 -B deploy/release.py verify-frontend --commit "$COMMIT"
```

仅在成功时预期输出`Final-path native modules, low-privilege startup and static hash verified; no business API requested.`；这不是本轮已取得的服务器结果。若报新的错误，停下保留输出，不删除目录、修改receipt、重复装依赖或绕过校验。验证通过后再按原手册逐步进行数据初始化、选择发布、服务及HTTPS，不在本次补修自动执行；旧f6b7ca5失败目录和旧receipt继续保留。

仅交付4文件：`deploy/release.py`、`deploy/tests/test_release.py`、本页及`PROGRESS.md`。原五缓存、数据库、真实环境及浏览器日志SHA256复核不变；未修改旧证据、账号或数据。本轮没有连接服务器，真实模型／外部业务API调用 **0**。**停在命令路径补修；Linux前端原生与静态验证仍待用户执行，未宣称公网部署完成。** 以下历次命令和停止点保留当时语境，当前续行以本节为准。

## 2026-10-19：权限修正的Node导入入口补修

实际基线 `b1fd2228a257c9c2ed69deb1d5b79aa025832635`，main，fetch同步。用户执行默认检查得到27065项／待收022为27035项，随后显式apply报`STOP: absolute_paths_required`。这是新增部署工具的Python→Node参数衔接缺陷，不是合法权限被再次拒绝；旧65项Python／14项Node测试的成绩保留，其当时未覆盖真实跨进程组合的缺口在本轮补齐。

`dependency_fingerprint`使用`node -e`动态导入实际`verify-frontend.mjs`，却把该模块路径放在`process.argv[1]`。模块的独立CLI守卫据此认为自己是主程序，错误进入CLI参数校验并非零退出。本次只在调用方增加固定`trippilot-dependency-fingerprint`参数占据argv[1]，模块／前端路径移至argv[2]／argv[3]；**不修改校验模块的主入口、安全检查、权限算法或任何产物／receipt规则，也不忽略子进程错误**。

该错误发生在第一次内容指纹检查，按代码顺序早于`_save_manifest`和首个fchmod；用户输出也没有`Original dependency permissions saved privately`提示。本次服务器命令未进入权限修改阶段，不需要回滚、重装或清理文件。保留旧失败目录、receipt及错误输出；本轮没有连接服务器或独立复核其文件状态。

新增2项真正跨边界的离线测试：实际Python调用方→测试机真实Node→复制实际校验模块字节的临时项目→inspectBuild，对临时依赖执行权限收敛并核对前后9字段指纹；仅映射Linux固定Node可执行路径到测试机安装路径，不伪造JSON返回。新成功测试在修正前真实非零失败，修正后通过。另以非法BUILD_ID让实际指纹检查失败，核对无清单／无chmod，确保仍正确保留错误；原独立CLI帮助与14项Node测试继续通过。

本轮部署Python **67项通过**（原65＋跨进程2）、Node **14项通过**、CLI帮助／语法／文档链接／敏感信息及差异检查通过。前端、原校验模块、权限模块、锁文件和构建未改，无需机械重建；没有业务全套／浏览器或真实Linux发布验收。缓存、数据库、实际环境、日志及旧证据不改；真实模型／外部业务API调用 **0**。

服务器下一步仅更新后再执行显式apply，保持npm／构建等写入停止：

```sh
cd /home/admin/apps/trippilot
git status --short
git pull --ff-only
COMMIT=$(git rev-parse HEAD)
sudo python3 -B deploy/release.py dependency-permissions --apply
```

预期先输出私有原权限清单路径，成功再输出修改数及`content fingerprints unchanged`；异常则停止并保留现场。取得成功输出后，才按下文原清单复查`needs_changes=0`、check-config、新SHA的attest／stage；不得覆盖或删除旧f6b7ca5失败目录／receipt，也不需要重新执行layout／runtime／config-templates。本轮只提交`deploy/release.py`、`deploy/tests/test_artifact_permissions.py`、本页及`PROGRESS.md`四文件，**停在此次调用衔接补修，真实服务器apply／stage仍待用户验证**。

## 2026-10-19：Linux依赖权限补修

实际基线 `f6b7ca597299c47d4fae207b7b5b09f04958a30a`，main，与origin/main同步。**用户报告**：服务器layout／runtime／配置检查通过，attest已成功，stage在verify-source通过后报`artifact_group_or_world_writable`；工作副本node_modules有27034项带group/world写位，前20项为0775目录，.next未发现此类项。旧失败发布目录尚无node_modules／.next／release.json。以上不是代理登录实测，本轮没有操作服务器。

原因是原stage先建目标目录、解包源码，再在复制产物时检查权限；内容receipt校验不含POSIX mode，因此verify-source成功不能证明权限可发布。本次保留安全拒绝，新增标准库[权限模块](../deploy/artifact_permissions.py)和固定命令，并把**两棵实际复制产物的完整权限／链接预检提前到创建新release目录之前**，复制时仍复检。`.next/cache`继续按原规则不复制、不纳入检查，不能据此修改其他产物。后续磁盘／复制故障仍可能留下半份目录，须保留调查，不承诺所有故障都不留目录。

### 固定范围、审计与失败语义

- `dependency-permissions`默认只检查，固定作用于`/home/admin/apps/trippilot/frontend/node_modules`，不接受路径或owner参数；需要收敛时显示`needs_changes=N`并非零退出，提示`dependency_permissions_need_explicit_apply`。不修改目录树、不生成权限清单；部署管理锁沿用原机制。
- 仅显式`--apply`才修改：完整预检后，先在既有root0700的`/opt/trippilot/receipts/`独占创建`dependency-permissions-<随机ID>.json`，root0600并fsync文件／目录，保存原相对路径、类型、dev/inode、uid/gid、mode、nlink、mtime、链接文本、提交和内容指纹。清单是**原权限记录／prepared状态，不是完成收据或新的frontend receipt**；不包含文件正文、密钥或真实.env，不进入Git或公开证据。
- 只对普通文件／目录执行`原mode & ~0o022`，含node_modules根目录；例如0775→0755、0664→0644，0751、0640等原样保留。属主／属组、读取与执行位均保留；不使用递归755、不chown。预期每项为admin的UID和主GID；遇其他归属先拒绝调查，不自动改归属。
- 扫描不遍历符号链接，修正不打开／chmod链接；仅验证内部相对链接的文本和目标归属，链接须存在且留在本树。绝对／越界／悬空／循环链接、普通文件硬链接、非普通文件／目录／符号链接以及setuid／setgid／sticky特殊位均拒绝。修改通过逐级`dir_fd`＋`O_NOFOLLOW`打开并fstat核对，前后复核条目集合、身份与元数据。
- 修改前后复用[已有inspectBuild](../deploy/verify-frontend.mjs)核对源码、锁、BUILD_ID、build、static和全部依赖内容哈希；可变cache仍按原receipt排除。只有mode改变不改变这些指纹，可继续复用经核对的同机Linux产物，不重装依赖或机械重建。工具不读取真实.env或调用业务接口。
- 任一预检失败不开始chmod；清单无法可靠保存也不修改。中途失败保留原清单与已经收紧的现场，不自动回滚可写位、不删除重做。清单路径先输出，只有完整复验通过才输出`content fingerprints unchanged`。异常后应调查，不把再次权限检查通过当作上次内容复验成功。

**操作前停止npm安装、构建及其他工作副本写入，并保持其停止直到stage完成。** 部署锁只能互斥本项目脚本，不能阻止admin另开终端修改文件；描述符和版本检查不是对有目录写权限的恶意并发操作者的完整隔离。POSIX权限收敛也不是完整Linux安全审计。

### 保留旧失败记录，使用新提交续行（仅供用户执行）

旧`/opt/trippilot/releases/f6b7ca597299c47d4fae207b7b5b09f04958a30a`失败目录及同SHA旧receipt必须保留，不删、不覆盖、不重新attest旧SHA。补修普通拉取后设置**新COMMIT**，新receipt／新release按原独占创建规则生成；已有Python、布局和配置不重装／重建。

```sh
cd /home/admin/apps/trippilot
git status --short
git fetch origin
# 确认没有冲突或不明改动后正常前进。
git pull --ff-only
COMMIT=$(git rev-parse HEAD)
git diff --exit-code f6b7ca597299c47d4fae207b7b5b09f04958a30a HEAD -- frontend

# 先只检查；若唯一原因是需要收敛022，此步非零退出是预期。
sudo python3 -B deploy/release.py dependency-permissions
```

仅在完整预检未发现异常归属、链接或其他安全问题时，单独执行：

```sh
sudo python3 -B deploy/release.py dependency-permissions --apply
sudo python3 -B deploy/release.py dependency-permissions
sudo python3 -B deploy/release.py check-config
sudo python3 -B deploy/release.py attest --commit "$COMMIT" --confirm-existing-build
sudo python3 -B deploy/release.py stage --commit "$COMMIT" --receipt "/opt/trippilot/receipts/$COMMIT.json"
```

预期：前端源码差异为空；apply先输出私有清单路径，再输出实际修改数和`content fingerprints unchanged`（数量以完整扫描为准，不固定为27034）；复查`needs_changes=0`；配置检查通过；新receipt生成，stage在新目录输出`Release staged`。如果已无需修改，apply明确no-op且不再建清单。之后按原手册逐条`backend-deps`、`verify-frontend`，再单独做初始化／服务／HTTPS；本次补修没有执行这些服务器动作。

### 本次定向验证与文件

部署Python **65项通过**（此前53＋权限核心10＋stage前置2），Node产物验证 **14项通过**（此前13＋mode不改内容指纹1）。含实际临时文件／目录的022收敛、执行位与uid/gid、无关文件／链接不变、硬链接／特殊文件／owner／特殊位拒绝、换链接／陈旧plan拒绝、清单持久化失败不修改、中途失败留证与第二次no-op；stage两树任一失败不建新目录，旧失败目录／receipt保留。Node使用受控产物核对完整inspectBuild结果不变。首次新竞态测试漏传原plan造成夹具KeyError，修正夹具后安全拒绝及外部文件不变断言通过，没有放宽生产规则。

```sh
backend/.venv/bin/python -B -Werror::ResourceWarning -m unittest discover -s deploy/tests -q
node --test deploy/verify-frontend.test.mjs
python3 -B deploy/release.py dependency-permissions --help
git diff --check
```

语法、文档链接、敏感信息及差异检查通过；原五缓存、数据库、环境及浏览器日志哈希复核不变。未运行业务全套验收／前端构建或真实Linux权限操作；服务器实际执行结果仍待用户提供，不能用Mac临时文件测试冒充Ubuntu已发布成功。真实模型和外部业务API调用 **0**。

仅交付7文件：`deploy/artifact_permissions.py`、`deploy/release.py`、`deploy/tests/test_artifact_permissions.py`、`deploy/tests/test_release.py`、`deploy/verify-frontend.test.mjs`、本页及`PROGRESS.md`。业务／前端源码、实际配置、数据库和旧证据不改。**停在本次依赖权限补修，未启动应用、初始化生产库或签发证书。**

## 2026-10-19：千问北京业务空间地址兼容补修

实际基线 `0db7a8aeb5240e7d067e856b7429128f1b5f7429`，main，开工fetch后与origin/main同步。用户报告服务器已完成 `layout`、`runtime`、`config-templates`，尚未填正式凭据、attest、stage或启动服务；这是用户提供的执行状态，本轮没有连接服务器核验。以下原部署包记录保留。

**原因与最小变更**：[后端 `beijing_endpoint`](../backend/app/integrations/qwen.py)已接受北京公共地址与北京业务空间专属地址；[部署检查](../deploy/config.py)此前仅接受空值或公共地址的精确字符串，导致合法workspace地址在安装后端依赖前被误拒为 `beijing_model_origin_required`。本次只扩展部署层官方域名／固定路径兼容，不改后端、模型、请求协议或调用逻辑。

非空 `QWEN_BASE_URL` 现允许：

- `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `https://<workspace>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`；workspace为单个1～63字符ASCII标签，首尾字母或数字，中间可含连字符。

两者可带一个尾斜线，scheme／host允许大小写差异，路径仍严格区分大小写。以控制台提供的北京业务空间Base URL为准，不把示例workspace当实际配置；不能附加 `/chat/completions`、端口、用户名密码、query、fragment或其他地域。空配置继续表示未配置，不代表可发起模型请求。模板默认公共地址与两个模型值不变，仅增加说明。

部署检查仍只用标准库，**不从后端导入Pydantic/httpx或读取本机.env**。8个合法、31个共同非法地址与实际后端校验逐项对照；部署另保留5项严格语法拒绝（空端口、空query／fragment、内嵌tab／换行），不因后端URL解析器会忽略这些符号而放行。不是宣称任意字符串的解析行为完全相同。错误仅返回固定安全原因，不输出提交URL或凭据。

### 已完成布局／解释器后的服务器续行清单（仅供用户执行）

不重跑 `layout`、`runtime` 或 `config-templates`，不重装已有Python，不覆盖重建配置。模板注释已变化，原模板文件再次安装可能因字节不同而停止；这是保护行为，应原地编辑既有文件再验证。

```sh
cd /home/admin/apps/trippilot
git status --short
git fetch origin
# 仅在确认没有冲突或不明改动后正常前进；有分歧就停下。
git pull --ff-only
COMMIT=$(git rev-parse HEAD)
git diff --exit-code 4e009851b6dee24e7192d60c5dc6ce5b9d3e2a25 HEAD -- frontend

# 在服务器本机填写／核对既有配置，不打印、不贴聊天，不复制真实.env。
sudoedit /etc/trippilot/backend.env /etc/trippilot/frontend.env
sudo python3 -B deploy/release.py check-config
```

预期：前端差异为空，可继续核对复用现有Linux构建；配置检查输出 `Exact production config, permissions and separated key allowlists verified.`。仍需填写正确凭据与公开JS Key一致性，地址校验通过不证明账号权限或模型可用，也不发送连通性探测。

仅在检查通过且确认原构建来源后，按原第3步逐条继续：

```sh
sudo python3 -B deploy/release.py attest --commit "$COMMIT" --confirm-existing-build
sudo python3 -B deploy/release.py stage --commit "$COMMIT" --receipt "/opt/trippilot/receipts/$COMMIT.json"
sudo python3 -B deploy/release.py backend-deps --commit "$COMMIT"
sudo python3 -B deploy/release.py verify-frontend --commit "$COMMIT"
```

预期依次为新receipt、`Release staged`、后端依赖／导入验证、最终路径Linux原生模块与静态资源验证；任一步异常按原停止规则处理，不删receipt／半份目录，不在本次地址补修中启动服务、初始化数据或签发证书。后续仍按原手册单独分步执行。

### 本次离线验证与交付

```sh
# 仓库根目录；全部为离线／受控检查，无外部业务调用。
backend/.venv/bin/python -B -Werror::ResourceWarning -m unittest discover -s deploy/tests -q
python3 -B deploy/release.py --help
git diff --check
```

```sh
# backend目录的现有千问适配／解读回归：
.venv/bin/python -B -m unittest discover -s tests -p test_explanation.py -q
```

部署测试 **53项通过**（原48＋新增5）；包含地址矩阵、实际后端对照、空配置、安全错误和 `-I -S -B` 隔离子进程标准库验证，原文件不覆盖／发布故障停止等受控回归保留。现有后端解读测试 **14项通过**，保留Starlette/httpx弃用提示。开发首轮矩阵抓到全局忽略大小写误放行路径的问题，已限定为scheme／host忽略大小写，保留路径拒绝断言后通过。CLI帮助／差异／文档链接及敏感信息检查通过；前端和业务后端未改，不重复整套构建或浏览器验收。

交付仅5文件：`deploy/config.py`、`deploy/env/backend.env.example`、`deploy/tests/test_qwen_config.py`、本页及 `PROGRESS.md`。既有环境、数据库、五缓存、旧证据及浏览器日志保持；真实模型与外部业务API调用 **0**。**停在本次兼容补修；部署、账号权限、HTTPS与真实Linux运行仍待用户执行验证。**

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
