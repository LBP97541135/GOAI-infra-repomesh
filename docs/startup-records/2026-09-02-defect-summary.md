# 2026-09-02 从零启动与 issue→提交链实走：缺陷汇总与设计级判定

本文汇总当天两份实走记录里暴露的全部问题，按「设计问题 / 实现 bug / 平台支持缺口 / 环境与文档」分级，
并给每个设计问题写清现象、为什么是设计问题、怎么解。原始证据不再重复，按编号回两份记录查：

- `#1`–`#10`：`2026-09-02-from-zero-windows.md`（从零启动，Windows 11 + PowerShell 5.1 + Git Bash）
- `V-1`–`V-8`：`2026-09-02-issue-to-commit-chain.md`（组织 → 四仓 → issue → 计划 → 开工 → 房间 → 提交链）

术语按 `CONTEXT.md`：**Manager** = 组织唯一的编排 Agent；**仓库团队** = 一名队长加至少一名 worker，共享该仓两间房；
**开工（materialize）** = 把已批准的计划变成活的编制与任务；**测试团队** = 锚定在**测试资产仓**上的一支仓库团队，
由 catalog 仓库行上的**档案开关**决定建不建。

## 0. 当天发生了什么（给没看过记录的人）

1. 03:25 把本机所有 RepoMesh / AgentTeams 的容器、卷、镜像、库全删，宿主生成态搬去备份。
2. 03:33–04:34 按 README 从零起平台。三条 Windows 启动命令（`start.ps1`、`start.sh`、`start-platform.ps1`）
   没有一条能原样跑通；把各自能走的段落拼起来、外加两处手工补救，约一小时到达「平台全接线、只差建管理员」。
3. 06:40–06:57 用用户 GitHub 上的四个公开仓（三个业务仓 + 一个测试资产仓）走产品流程：建组织、接入仓库、
   新建 issue、发现链、开工。开工真的把编制造出来了：四支仓库团队、八个 copaw worker 容器、八间房、四个任务。
   但 worker 拿到任务后一步没干成，**没有走到改代码、提交、开 PR 那一段**。

## 1. 分级总表

| 编号 | 缺陷 | 级别 | 一句话理由 |
|---|---|---|---|
| #2 | api 容器硬依赖 AgentTeams 的外部网络/卷，「先起 setup 页」的路径必挂 | **设计** | 启动顺序是环：api 等 AgentTeams，AgentTeams 等网页填密钥，网页等 api |
| V-5 | 产品部署里没有任何进程执行 runner dispatch | **设计** | 架构写「Worker 里跑 runner」，实际默认 worker 是 copaw，runner 镜像不存在 |
| V-1 | worker 回调平台的 MCP 接口全 401 | **设计** | 两个控制面各有门禁，平台从没给 worker 发过自己认的钥匙 |
| #6 / V-6 / 回执 | 写完运行时配置不重启 api；`/health/ready` 假绿；worker 的 BLOCKED 回执无人消费 | **设计** | 配置靠文件读一次、就绪没有单一真相、运行时反馈没有入口 |
| V-3 | 服务端拆解模式下队长找不到任务包与仓库，绕圈无人拉回 | **设计** | 平台假设队长「不必做事」，copaw 队长假设「有任务必有包」 |
| V-8 | 建组织用共享令牌即可，接入仓库团队与翻档案开关却要管理员会话 | **设计** | 共享令牌无主体，代码注释自认「主体化凭据是待办」 |
| V-2 | Manager 的 manager 容器 18888 端口与安装器的 manager 冲突，停在 Created | 设计味的实现问题 | 每个组织再起一个 manager 且固定端口，这个决定本身可疑 |
| #1 | `start.ps1` 在 PS 5.1 把 Docker 的 stderr 警告当致命错误，2 秒即死 | 实现 bug | `$ErrorActionPreference=Stop` + 原生命令 stderr |
| #4 | `start-platform.ps1` 写运行时配置的函数被管道调用，写出 0 字节文件 | 实现 bug | 修 PS 5.1 兼容时引入 |
| V-4 | `start_assigned_task` 副作用成功却回 500 | 实现 bug | 按错误形状读返回值 |
| #3 | Git Bash 下 `start-platform.sh` 调只能在 Linux 跑的安装器，拉完 12GB 后炸 | 平台支持缺口 | 有 `.ps1` 安装器可用 |
| #5 | 安装器按电脑时区猜镜像仓库地域，Pacific 时区去了匿名不可拉的仓库 | 平台支持缺口 + vendored | `.env` 钉 `AGENTTEAMS_REGISTRY` 即可 |
| #7 | Docker Desktop 四个镜像加速器全死，每个基础镜像白等 1–5 分钟 | 环境 | README 未提示基础镜像不可换源 |
| #8 | `.env.example` 全文重复一遍 | 文档 | — |
| #9 | `.sh` 与 `.ps1` 安装器行为不一致（装不装 dashboard、拉几种 worker 镜像、进度被吞、乱码） | vendored | — |
| #10 | README 说 `start-platform.ps1` 要 PS 7+，实际 5.1 可跑；「cold path 未端到端跑过」 | 文档 | 本次即该实走 |
| V-7 | `GET /issues/{id}/discovery` 不再回传 plan 正文，旧驱动脚本读 `plan.task_dag` 得 null | 接口/脚本漂移 | — |

## 2. 六个设计问题：现象、为什么、怎么解

### 2.1 启动顺序是个死循环（#2）

**现象**：白机上第一次启动，api 容器要求 AgentTeams 的网络 `agentteams-net` 与数据卷 `agentteams-data` 已经存在
（`compose.yaml:256-263` 的 `external: true`）。可 AgentTeams 是等用户在网页上填了模型密钥之后才安装的，
填密钥的网页又由 api 提供。`start-platform.*` 的「Model credentials are not configured; starting the setup plane first」
分支正是为白机设计的，它接着起 api，必然撞上不存在的外部对象。两条 Docker-first 启动命令在建完三张镜像
（20 分钟）之后死在同一句报错上。

**为什么是设计问题**：编排文件把「api 依赖 AgentTeams」写死，同时又设计了「先起网页再装 AgentTeams」的流程，
两个决定互相打架。不是哪一行写错。

**怎么解**（推荐第一个）：
1. 启动脚本先把网络和卷空建出来（`docker network create` / `docker volume create`，幂等），api 就能先起；
   安装器复用已存在的网络。或者去掉 `external: true` 让 compose 自建，确认安装器对已存在对象是复用而非报错。
2. 拆两个 profile：`setup`（postgres + api + web）与 `platform`；bootstrap 装完 AgentTeams 后
   `docker network connect` 再重启 api。
3. 不推荐让 api 启动时容忍网络缺失、运行中再动态加入——那会把「接没接上」变成运行时状态，回到 2.4 的假绿问题。

### 2.2 产品部署里没有人真正干活（V-5）

**现象**：worker 收到任务后（本轮由 AI 代 worker 调了「开始任务」接口），平台把仓库镜像克隆好、工作目录检出好，
把一条 runner dispatch 排进队列（`agent_runtime.runner_dispatches`，`status=queued`，`adapterId=claude-code`），
然后没有下文。30 分钟访问日志里 `GET /runtime/runner-tasks/next` 零次。

**为什么是设计问题**：`docs/architecture/runtime-planes.md` 写的是「AgentTeams 管理的 Worker 启动 RepoMesh Runner」
（runner = 真正调 Claude Code / Codex 这类命令行工具去改代码、跑测试、冻结提交的程序）。实际安装出来的
`AGENTTEAMS_DEFAULT_WORKER_RUNTIME=copaw`，开工造出来的八个 worker 全是 copaw 聊天型 agent：容器里没有
`repomesh_runner`，没有任何编码命令行工具；api 容器里也没有。文档里那张「带 runner 的 worker 镜像」
（`agentteams/agentteams-repomesh-worker`）仓库里没有 Dockerfile、公共仓库里也没有。以往所有跑到 PR 的活体
（08-07 plan-loop、09-01 W4），都靠有人在宿主上手工起 bridge/runner 进程，不在 README 的路径里。

**怎么解**：先拍板 runner 放哪，三个选项平台里都已有雏形：
1. **Runner 进 Worker 镜像**：把 `repomesh_runner` + 常用编码 CLI 打进 copaw 基础镜像，仓库团队默认 runtime 改成
   `repomesh-runner`；同一容器里 copaw 收房间消息、runner 轮询 dispatch。最贴合架构文档；代价是维护一张自有镜像，
   并解决编码 CLI 的登录凭据怎么进容器。
2. **Runner 作为平台 sidecar**：compose 加一个 `runner` 服务，与 api 共享 `/runner-workspaces`，用 runner control token
   领所有 worker 的 dispatch（`_authorize_runner` 已支持 control token 领任意 worker）。从零最简单，只多一张镜像；
   代价是编码 CLI 凭据集中在平台侧。
3. **本地 CLI 外部成员**：这条线已验收（`scripts/start-local-cli.ps1`、
   `ExternalMemberReadinessGate`、`docs/development/local-cli-readiness-live-acceptance-20260830.md`）。
   把仓库团队默认建成外部成员，开工前的就绪门禁直接告诉用户「起你本地的 CLI」。最符合「用开发者自己的编码工具」的定位。

无论选哪个，「dispatch 无人领」必须变成显式状态：超过租约仍 `queued`，issue 页就报「执行面没有 runner」，
而不是永远「进行中」。

### 2.3 两边的门禁互不认账（V-1）

**现象**：copaw worker 回调 `POST /api/v1/mcp/worker`（MCP = worker 用来告诉平台「开始我的任务」的工具接口）
12 次全 401。它带的令牌是 AgentTeams 控制器写进 `mcporter.json` 的 64 位串，与平台三枚令牌
（action / gateway / runner-control）逐一比对都不相等。test-assets 的 worker 自己在房间里推理出了这个结论，
写回 `result.md` `STATUS: BLOCKED`。

**为什么是设计问题**：RepoMesh 和 AgentTeams 是两个各有门禁的系统。平台把 worker 登记进 AgentTeams 时
（`principal_registration.with_task_control()`）只给了接口地址，没有发一把平台认的钥匙；
`worker_mcp._authorize` 只认平台自己的两种令牌。开发环境本有「免门禁直连」兜底
（`compose.yaml:64` 默认 `REPOMESH_DIRECT_WORKER_MCP_ENABLED=true`），但 `.env.example:21,105` 写成了 `false`，
照 README 复制 `.env.example` 的人一定 401。

**怎么解**：平台登记 worker 时就给它签一枚专属令牌，随接口地址一起投影进 worker 配置的 header；
平台校验时按 worker 身份对钥匙。runner 协议已经是这么做的（`REPOMESH_RUNNER_WORKER_TOKENS`，
`src/repomesh/modules/agent_runtime/api/router.py:333`），把同样机制复用到 MCP 即可。生产路径走 Higress 网关
注入 gateway token。dev 直连开关只是止血，`.env.example` 至少不该把它关掉。

### 2.4 平台不知道自己接没接上，也听不见 worker 说话（#6、V-6、回执）

**现象**（三件）：
- `start-platform.ps1` 装完 AgentTeams 后写出的 `platform-runtime.env` 是 0 字节，脚本照样宣布「ready」；
  健康检查 `/health/ready` 200。
- 后来文件写对了（`start.sh`），api 容器没被重建，仍用旧配置；compose 看不到 `.secrets` 里的文件变化。
  手动 `docker restart` 才接上。
- worker 写回「我被卡住了」的回执（MinIO 里 `meta.json.status=submitted`、`result.md` BLOCKED），
  平台上那个任务一直 `assigned`；预留租约到期后也无人回收（`.env.example:52` `REPOMESH_WORKER_RECOVERY_ENABLED=false`）。

**为什么是设计问题**：接线靠「写一个文件，进程启动时读一次」，谁写完谁负责重启，编排工具又看不到文件变化；
「就绪」的定义是「按当前配置能跑」而不是「平台完整」；worker 往回说的话没有任何入口进平台。
三件事的共同根源是没有一个「执行面到底什么状态」的单一真相。

**怎么解**：
- 运行时配置改成落库，api 启动时读库；写完配置就走已有的「进程主动退出、Docker 自动拉起」模式
  （`src/repomesh/api/platform_credentials.py:114` `_exit_after_response` 已经这么做），启动脚本不再靠 compose 判断要不要重建。
- `/health/ready` 在 `REPOMESH_AGENTTEAMS_REQUIRED=false` 时返回 `degraded` 而不是 `ready`；启动脚本末尾以
  `setup/status` 的 `agentteams` / `matrix` 字段断言，README 的检查口径同步改。
- AgentTeams 任务目录里的 `result.md` / `meta.json.status` 要有入口进平台，至少把 `submitted` + `BLOCKED`
  映射成任务的 blocked 状态并在房间读模型里显示；recovery 默认开。

### 2.5 队长收到一条像任务的消息，却没有任何可做的东西（V-3）

**现象**：本轮是服务端拆解（`decomposition_mode=server`）：任务由平台拆好直接派给 worker，队长不需要拆解。
但平台还是给队长发了一条自然语言任务描述。copaw 队长按 AgentTeams 自带的任务技能去找「我的任务包」和
「本地仓库」，两样都没有，就反复自查身份绕了三分钟，没人拉它回来。

**为什么是设计问题**：平台假设「队长收到消息什么都不用做」，队长这边的 agent 假设「收到任务就一定有任务包」，
两边协议没对上。这不是 prompt 问题。

**怎么解**：服务端拆解时要么根本不给队长发消息，要么发一条明确标记「仅知会、不需要动作」的结构化消息；
队长房只用来回放进度。

### 2.6 谁有权做什么，规则不统一（V-8）

**现象**：建组织（`POST /console/organizations`）、跑发现链、开工，用平台那枚共享 action token 就能做；
但把仓库接成仓库团队（`/repositories/{id}/agent-team`）、翻测试资产仓的档案开关，必须用管理员登录会话。
用户以为在控制台建过组织，库里其实是空的，也跟这个混乱有关。

**为什么是设计问题**：共享令牌不代表任何人，`identity_access/api.py` 的注释自己承认
「subject-carrying credentials are the adopted backlog item; this is the pre-push floor」。

**怎么解**：引入代表具体主体的服务凭据（组织级 service account），把「谁能建组织、谁能接团队、谁能翻开关」
放进同一张策略表；共享令牌只留给机器到机器的读模型调用。

## 3. 非设计级问题的修法（各一行）

| 编号 | 修法 |
|---|---|
| #1 | `scripts/start.ps1` 改 `$ErrorActionPreference = "Continue"` 并显式查 `$LASTEXITCODE`（与 `start-platform.ps1:7-10` 一致），或用 `cmd /c "docker info >nul 2>&1"` 探测 |
| #4 | `scripts/start-platform.ps1:295` 改位置调用 `Set-Utf8NoBom $RuntimeTemporary @(...)`，`Move-Item` 前断言文件非空 |
| V-4 | `src/repomesh/api/worker_mcp.py` 按 `McpCallResult` 的真实形状取返回值，或让 `call_gated` 透传原对象 |
| V-2 | 组织 Manager 的 manager 容器不发布固定主机端口（或动态分配），或直接绑定安装器已起的 manager |
| #3 | `start-platform.sh` 在 MSYS/MINGW 下改调 `.ps1` 安装器，并把 socket 探测提前到任何镜像拉取之前 |
| #5 | `.env.example` 钉 `AGENTTEAMS_REGISTRY=higress-registry.cn-hangzhou.cr.aliyuncs.com`；上游安装器拉取失败时回退默认地域 |
| #7 | README 说明基础镜像走 docker.io、国内需配置可用加速器；`Dockerfile` 加 `BASE_IMAGE` build-arg |
| #8 #10 | 去重 `.env.example`；README 把 PS 7+ 改成 5.1+，并把「cold path 未跑过」换成指向本目录的记录 |
| V-6 | `.env.example` 的 `REPOMESH_WORKER_RECOVERY_ENABLED` 默认 `true` |
| V-7 | 读模型形状变更进 `docs/contracts`，`scripts/module-test-team/w4_chain.py` 跟上 |

## 4. 一个横切建议：把启动逻辑收进 bootstrap 容器

#1、#3、#4、#5 表面是 Windows 兼容 bug，根子是同一套启动逻辑在 bash 和 PowerShell 里各写了一遍，
改一边忘另一边（#4 就是修 PS 5.1 兼容时改坏的）。平台里的 bootstrap 容器（`Dockerfile.bootstrap`）本来就带着
Docker 命令行和安装器，并且已经在容器里跑 `agentteams-install.sh`
（`src/repomesh/integrations/bootstrap/executor.py:69`），是天然的 Linux 执行环境。
把生成密钥、装 AgentTeams、取令牌、写运行时配置、重启 api 全部搬进这个容器，宿主脚本退化成一行
`docker compose --profile platform up`。这样 Windows 上的 socket 探测、MSYS 路径改写、时区猜仓库、PowerShell
版本差异一起消失；2.1 的解法也顺手落在同一个地方。

## 5. 建议的修复顺序

1. 先修让 README 一条命令能到底的三处一行改动：#1、#4、#2 的幂等建网络/卷。修完从零再跑一次，新开一个记录文件。
2. 拍板 2.2 的 runner 放哪。这是「issue 能不能自己走到提交」的唯一门槛，其他都可以绕。
3. 2.3 的 worker 凭据与 2.4 的就绪真相一起做，它们决定「走到了」能不能被看见。
4. 2.5、2.6 与横切建议排在后面，属于让产品可维护而不是能跑。
