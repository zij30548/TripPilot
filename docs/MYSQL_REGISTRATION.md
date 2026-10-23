# 邀请码注册、密码可见性与 MySQL 全量迁移

> 后续产品变更（2026-10-22）：用户已要求移除登录／注册／邀请码，当前正式应用使用[匿名浏览器工作区](ANONYMOUS_WORKSPACE.md)，旧`/auth/*`不再挂载。本文保留原迁移、测试及交付历史；下方账号／邀请码管理不是当前使用步骤。原账号、邀请及记录不会被自动删除或交给匿名访问者。

2026-10-20；实际开发基线 `d847dd251638864f472a18c97ff959c906fc3b84`，main。用户明确选择把私有业务库和持久业务额度**全部迁入 MySQL**，不只是给新注册加一张孤立账号表。本轮不连接或修改云服务器，模型／高德等外部业务调用 0。

## 当前状态与范围

- 实现邀请码自助注册、登录／注册／确认密码的小眼睛，以及 MySQL 后端、全量复制迁移、一致性备份与隔离恢复演练。
- 本地真实 MySQL 8.4.11／InnoDB 验证；生产构建 Chrome 验证使用独立 MySQL、合成存档和受控本地路由，不是线上迁移或真实旅行数据重验。
- **线上切换尚未执行**。用户此前提供了 HTTPS 200、服务 active 和成功登录结果；这是用户提供的部署事实，不是本轮代理登录服务器验证。已有服务器 SQLite、会话、额度及配置不改动。
- 仍为邀请码内测，不做无条件公开注册、邮件／短信验证、找回密码、第三方登录或账号后台。知识库生成式问答继续停用，六城／七日／单住宿和排程含义不变。

## 存储边界：迁什么、不迁什么

同一个 MySQL 数据库包含：

| 表 | 职责 |
| --- | --- |
| `accounts` | 原账号 ID、唯一账号名、Argon2id 哈希、禁用状态、原创建时间 |
| `auth_sessions` | 不透明会话凭据摘要、用户、CSRF、原有效期及创建时间 |
| `login_buckets` | 登录和新增注册的持久预占限流 |
| `invitations` | 邀请码摘要、有效期、撤销、使用账号和时间；不保存邀请码明文 |
| `saved_trips` | 原 ID／归属／幂等操作／时间及规范化 JSON，v1/v2/v3 不变 |
| `storage_metadata` | 独立的 `private-mysql-v1` 存储版本，不是新行程快照版本 |
| `usage_attempts` | 每次保守预占、发送意图、完成／失败／取消状态和原时间 |
| `usage_metadata` | `business-usage-mysql-v1` 及原额度账本身份 |

原 `.identity` 文件仍作为库外身份锚复用，**不是第三份计数库，也不自动重建**。它用于拒绝缺失／错配账本，不证明相同身份的备份没有回滚。迁移保留全部额度尝试，包括未发、已发失败、取消和发送结果未知；没有退款、清零或新额度授权。公开额度响应仍为 `business-usage-v1`，不改变页面契约。

不迁移仓库知识语料、人工模型试验台账、旧证据、媒体或前端临时工作。它们不是业务 SQLite 数据表。旧无归属存档继续 `owner_id=NULL` 隔离，不分配给新用户。

`TRIPPILOT_MYSQL_URL` 未设置时，原 SQLite 模式兼容保留，但自助注册明确返回 503 `registration_unavailable`，不会向旧 identity-v1 自动加表。设置后账号／存档／业务额度全部选择 MySQL，连接、版本、计数异常都**不回退 SQLite**；运行时不建表、不导入、不初始化额度。

## 注册与并发原理

1. 前端输入账号、密码、确认密码及邀请码；确认密码仅用于本地一致性检查，不上传。仅主动提交一次，成功不自动登录。
2. 后端严格校验 JSON（重复键／额外字段拒绝，4 KiB／10 秒），校验可信 Origin 和预登录 CSRF。
3. 在密码计算前持久预占注册次数：全局每 15 分钟最多 10 次、同账号名每小时最多 3 次；正确、错误、busy 都保守计入。不是 IP 限流或完整抗 DDoS。
4. 与登录共享最多两个 Argon2 计算槽。12～128 字沿用现有策略，哈希在邀请码事务外计算，避免持锁做昂贵工作。
5. 开始短事务，按唯一 token 摘要 `SELECT … FOR UPDATE`，**重新**校验未过期、未撤销、未使用；创建账号、flush 唯一账号约束，再把邀请码关联到该账号，最后一起 COMMIT。
6. 同一码并发只成功一次；同账号名不同邀请码并发只有成功那枚被消费。异常则整个账号／消费事务回滚。响应丢失或取消不能承诺账号未创建，不自动重试，提示先尝试登录。

邀请码 256 位随机、默认 24 小时，可设 1～720 小时；数据库仅 SHA256 摘要，撤销使用公开记录 ID。创建 CLI 要求输入和输出均为交互终端，只显示一次秘密；不能开启终端录屏／会话记录，不能把邀请码发到公开日志。

密码小眼睛只在 Client Component 中切换 `type=password/text`，默认隐藏、`type=button`、ARIA／键盘可操作、保留 password manager autocomplete。不是数据库解密，不产生网络请求；切换表单、提交、取消和身份变化清理敏感状态。密码依旧由 HTTPS 传输、Argon2id 哈希存储。

## 额度与持久性

复用原分类、自然日、短窗口、状态机及调用边界；计数单位仍是尝试／任务，不是精确人民币。

MySQL 使用 READ COMMITTED；事务先取得账本身份行的 `FOR UPDATE` 锁，再读取时钟和账号／全局窗口、日内 attempt 数并写入预占。所有判断与预占在同一事务，多个线程／进程不能各自读旧余额后超发。锁等待最多 3 秒；失败不发送、不自动重试。当前小规模选择串行化全局准入来保证简单可验证，**不宣称无限吞吐**。

预占和 dispatching 必须先持久提交，随后才进入上游发送。COMMIT 前失败阻止发送；COMMIT 已完成但 ACK 丢失也阻止本次发送并保留占额。已发送取消／超时／解析错误不返额。每次额度操作核验 `innodb_flush_log_at_trx_commit=1`，否则安全拒绝，不替操作员改数据库设置；仍不能保证失效磁盘／主机恢复或供应商实际账单。

## 配置与迁移操作边界

新增锁定 `PyMySQL[rsa]==1.2.3`；RSA 依赖用于 MySQL 密码认证，不改变应用 Argon2id。SQLModel／SQLAlchemy 原有版本保持，使用 utf8mb4、InnoDB、显式字段容量及约束；快照 JSON 用 MEDIUMTEXT 承载，但业务原 2 MiB 上限不变。URL 用 SecretStr，仅可信服务端可配；异常不打印 URL／SQL 参数／凭据。

仅在后端私密配置增加（以下均占位，不可原样用于生产）：

```dotenv
TRIPPILOT_MYSQL_URL=mysql+pymysql://trippilot_app:REPLACE_URL_SAFE_PASSWORD@127.0.0.1:3306/trippilot?charset=utf8mb4
BUSINESS_USAGE_IDENTITY_PATH=/var/lib/trippilot/usage.sqlite3.identity
```

部署检查仅允许本机回环 3306、固定 `trippilot` 库、非root的受控账号格式和至少 16 字 URL-safe 口令（建议随机生成更长口令）；保留原 HTTPS Origin、Secure Cookie、密钥分离。两个新配置键可选，因此旧合法配置仍能验证；切换时必须显式填写两者。`TRIPPILOT_DATABASE_PATH`／`BUSINESS_USAGE_PATH` 的旧值保留作为来源说明和默认锚定位，不会在 MySQL 错误时拿来兜底。真实配置不重建模板、不进 Git。

**服务器待执行，不能把下列命令理解为已完成：**

1. 先只读检查 MySQL 安装版本、监听、剩余内存、磁盘与备份空间。已有主机约 1.6 GiB 内存；新增数据库有实际内存和磁盘成本，不能照搬本机配置或先启动再看是否 OOM。MySQL 只监听回环，不开放公网 3306。先核对 InnoDB、严格 SQL mode、`innodb_flush_log_at_trx_commit=1`；配置和资源必须在服务器实测。
2. 管理员创建空 `trippilot` 数据库（utf8mb4_bin），区分一次迁移 DDL 权限与应用仅 DML 权限；应用不能用 root。数据库凭据由管理员私密输入／编辑，不放命令参数、shell history、聊天或前端。初次 DDL 需要 CREATE／索引／外键等权限，运行时不要保留迁移权限；SHOW 元数据和实际应用账户需另行验证。
3. 准备新 Linux release／venv。前端有实际变化，**不能复用旧提交的 .next**；按 release-1F 新 SHA／新构建／receipt 发布，不覆盖旧 release。本轮没有改变服务器安装或服务。
4. 切换前停止 API 及所有 CLI 写入。在 MySQL 仍为空配置、旧 SQLite 仍为活动后端时，使用旧停写备份入口生成**新批次**：

   ```sh
   sudo systemctl stop trippilot-api
   sudo python3 -B /home/admin/apps/trippilot/deploy/backup.py backup \
     --batch before-mysql-UNIQUE --confirm-no-other-writers
   ```

   不删除／重建原库或锚。确认备份成功后，把一致、自包含、无 WAL/journal 的 `identity.sqlite3`、`usage.sqlite3`、`usage.sqlite3.identity` 放入同一私有 0700 目录，文件 0600。原始备份继续 root-only；迁移程序的操作身份只读自己的受保护副本。不要放宽整个备份树或用户家目录。
5. 在**单独操作员迁移环境**中装载目标 MySQL 迁移凭据，不先改活动服务配置。使用新 release 的 backend venv，从 backend 目录执行：

   ```sh
   .venv/bin/python -B -m tools.mysql_migration migrate \
     --source-directory /absolute/private/stopped-copy \
     --receipt /absolute/private/new-migration-receipt.json \
     --confirm-source-stopped
   ```

   预期输出 `migration_verified`、各表行数、`source_unchanged:true`、`usage_refunded:false`。私有账号、会话、存档和完整额度同一最终 DML 事务导入并核对逐表摘要；ready marker 最后同事务发布。**MySQL DDL 本身不可假称事务回滚**：失败目标／私有 receipt 保留，禁止删除后自动重跑或绕过校验。目标非空或 receipt 已有即停止；提交不确定也不覆盖、不补发。
6. 比对源文件哈希、表摘要／数量、ID／归属／时间、快照版本及全部 attempt 状态；保留原锚。以正式运行账号装载私密服务配置，先执行：

   ```sh
   .venv/bin/python -B -m tools.mysql_migration verify
   ```

   预期明确验证 MySQL 私有库、额度和锚。部署 `check-config`／`verify-data` 也已识别活动 MySQL；MySQL 下旧 `init-identity`／`init-usage` 明确拒绝，不初始化第二份额度。
7. 只有上述核对通过才由用户选择新 release、启动 API／web，复核 HTTPS 登录／CSRF、旧存档、自助注册、额度和重启持久性。本轮未代执行这些步骤，也未验证生产 DML 权限、Ubuntu 原生依赖或容量。

### 管理员生成邀请码

迁移并验证后，在新 release 的 backend 目录、已安全装载后端配置的**交互式管理终端**：

```sh
.venv/bin/python -B -m tools.invitation_admin create --valid-hours 24
.venv/bin/python -B -m tools.invitation_admin revoke PUBLIC_INVITATION_RECORD_ID
```

只将一次性邀请码私下交给受邀者。注册不是无限付费授权，原每账号及全局硬额度保持；邀请码仍可能被转发／泄漏，注册限流不是完整反机器人系统。管理员账号 CLI 和既有会话撤销／禁用继续可用。

## MySQL 备份、演练与回退

```sh
# 从 backend 目录，已安全装载 MySQL 配置；父目录必须私有，输出必须不存在。
.venv/bin/python -B -m tools.mysql_backup backup \
  --anchor /absolute/private/original-usage.identity \
  --output /absolute/private/new-mysql-backup.jsonl
```

八表同一只读 REPEATABLE READ 一致快照，默认上限 256 MiB／总10万行／单行16 MiB（含 JSON 转义），不截断；超限或失败保留 0600 partial，缺 footer 不能恢复。输出包含密码哈希、CSRF、会话摘要及行程，只能当管理员私密备份，不是公开验收证据。

恢复演练仅允许独立空库名 `trippilot_restore_<32位随机小写hex>`。操作员临时设置 `TRIPPILOT_MYSQL_RESTORE_URL`（SecretStr，不加入服务配置），并核对备份 SHA256：

```sh
.venv/bin/python -B -m tools.mysql_backup restore-drill \
  --input /absolute/private/mysql-backup.jsonl \
  --expected-sha256 BACKUP_SHA256 \
  --anchor-output /absolute/private/new-drill.identity
```

先全量校验版本／字段／引用／摘要，再导入新库；演练撤销全部会话，账号、邀请、存档及**全部额度尝试**保留。它不修改运行配置，不允许生产名或非空目标，不代表生产恢复获准。

代码回退与数据回退必须分开：**MySQL 启用后的任何新写入，都不能直接回旧 SQLite 或恢复落后备份**，否则可能丢账号／存档、复活旧会话或返还额度。已提交但响应丢失先只读核对目标，不能再导入。真实灾难恢复要停写、核对最新尝试／供应商账务及备份后增量；本轮没有提供盲目回退按钮、跨库双写或自动恢复平台。旧 SQLite 备份 CLI 在 MySQL 活动配置下明确拒绝新建备份，防止把旧库误称为当前数据。

## 验证、证据与限制

- 真实 MySQL 临时服务：Homebrew `mysql@8.4` 8.4.11_7，`/opt/homebrew/opt/mysql@8.4/bin/mysqld`；仅127.0.0.1:13306，独立随机临时data，无开机服务，验收后停止。不是云服务器的安装或验证。必要新依赖及锁文件已记录，原 SQLite／五缓存不变。
- 后端完整／前端完整、部署、转发及最终定向结果见最新 [PROGRESS](PROGRESS.md)，避免把开发中间次数重复相加。受控上游／故障与真实 MySQL 明确分开，任何受控 HTTP 不叫真实高德／千问。
- Chrome 155＋本轮生产构建：真实注册事务、密码显示／隐藏与Enter、成功后主动登录、新账号空历史、切回迁移账号的 v3 历史／费用／额度、退出和400px。为了保留原3000／8000服务，验收3103前端的固定 `/api` 通过**验收工具本地路由桥**连接8801独立后端；未伪造身份或存档响应。不是公网Nginx／HTTPS／同源转发重新验收，转发规则另用自动测试验证。
- 图中地点／路线／费用为合成存档，页面本身的“来源高德”是存档字段，不可当作真实高德样本。本轮没有搜索、生成路线、天气、保存用户记录或调用模型。迁移 fixture 五条额度是旧日期；当前日页面显示0不代表返额，同日保留由固定时钟测试及原始行对比证明。
- 稳定证据：`/Users/zijing/projects/trippilot-evidence/mysql-registration/batch-20261020/`。截图逐张检查，脱敏记录不含Cookie／密码／邀请码／URL凭据／原始数据库或网络日志。旧证据、成绩和模型台账不变。
- 工具接线验证中两次尝试在浏览器沙箱读取测试文件被拒绝，改用明确的可公开合成测试凭据；历史对话框打开时首次退出点击被遮挡，关闭后成功。不是产品认证失败，没有绕过门禁。密码眼睛初次跨focus/resize观察混入身份核查，随后在单一操作窗口核对无请求；不把身份生命周期检查误算成模型／业务发送。

## 面试怎么解释

> 原系统已具备邀请账号、会话、用户存档隔离和费用限额。我补的是用户自助兑换邀请码，不是无条件开放注册。用同一 InnoDB 事务完成账号创建与邀请码消费，行锁防并发兑换，唯一约束兜底。昂贵的 Argon2 哈希在短事务之外，并有持久限流与有界计算槽。密码小眼睛只控制浏览器展示，不解密数据库。
>
> 我没有把注册单独写进新库造成双身份，而是统一迁移账号、会话、存档和业务额度。保留用户 ID、快照和全部尝试，最后发布 ready marker；提交不确定保留现场，不盲目重试。SQLite 本来也有事务，选择 MySQL 是明确的产品选型和后续独立数据库运维方向，不是因为 SQLite 不能保证原子性。迁移能力在临时 MySQL 验证，不冒称线上已经切换。

参考： [MySQL InnoDB locking reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)、[SQLAlchemy MySQL 方言](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)、[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。这些解释设计依据，不替代本项目并发、故障和迁移测试。

停止在 MySQL 全迁实现／独立演练与邀请码注册交付。生产安装、资源评估、正式停写切换和灾难恢复均由用户另行分步执行；不开始邮箱注册、Agent新功能或真实业务调用。
