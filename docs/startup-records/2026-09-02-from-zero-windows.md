# 2026-09-02 从零启动实走（Windows 11 / Git Bash + PowerShell 5.1）

## 0. 基线

**目的**：验证 README「Open the console」段落承诺的「fresh clone → 控制台，零手工配置」在真实
Windows 机器上是否成立。

**起点状态（03:25 前完成整拆）**：
- 本机所有 RepoMesh / AgentTeams 容器（50 个）、命名卷（8 个）、网络（7 个）、项目镜像（含自建
  `agentteams-embedded:v1.2.0-rm*`、`repomesh-runner:*`、`goai-infra-repomesh-api`）、
  16.7GB 构建缓存全部删除。
- 仓库内 `.env`、`.secrets/`、`.repomesh-dev/` 与家目录 `agentteams-manager*`、`.agentteams/`
  全部搬到 `D:\Project4work\repomesh-wipe-backup-20260902\`，**工作树里没有任何本机生成态**。
- 保留了 `postgres:17-alpine` 等基础镜像（它们不是本项目产物），所以本次**测不到 postgres 拉取**。
- 分支 `feat/module-test-team-v1`，HEAD = `fd26f09f`（与 GitHub main 同头）。

**宿主工具版本**：

| 工具 | 版本 |
|---|---|
| Docker Desktop engine | 28.0.4，Compose v2.34.0 |
| Windows PowerShell | 5.1.26100（**没有 pwsh 7**） |
| Git Bash | D:\Git（MSYS） |
| uv | 0.9.0 |
| Node / npm | 22.22.1 / 11.13.0 |
| Python（宿主） | 3.11.7（README 要求 3.12+，但 Docker-first 路径不需要宿主 Python） |

**环境噪音（非本项目，但会影响端口）**：
- `cumora-postgres`（已停）映射 `0.0.0.0:5432`；`multica-*` 占 3000/8080；`coagenthub-smoke-pg`
  在崩溃重启循环。

**README 给出的路径**（按优先级）：
1. Docker-first 启动器：`.\scripts\start.ps1`（Windows）/ `./scripts/start.sh`（bash）。
2. 开发启动器：`.\scripts\dev-up.ps1` / `./scripts/dev-up.sh`。
3. 独立 console compose：`docker compose --profile console up -d --build`。

本次先走路径 1 的 Windows 形态。

## 1. README 路径 1（Windows 形态）：`.\scripts\start.ps1`

- **时间**：03:33:57 → 03:33:59（2 秒）
- **命令**（Git Bash 里调 Windows PowerShell 5.1，等价于用户在 PowerShell 里直接跑 `.\scripts\start.ps1`）：
  ```
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -NoBrowser
  ```
- **预期**：README「Open the console」：选端口、生成内部凭据、装/复用 AgentTeams、起 postgres + API + nginx 控制台，打印 `Open RepoMesh: http://127.0.0.1:5280`。
- **实际**：退出码 1。全文见 `2026-09-02-step1-start-ps1.log`，关键行：
  ```
  docker : WARNING: No blkio throttle.read_bps_device support
  所在位置 D:\Project4work\GOAI-infra-repomesh\scripts\start.ps1:45 字符: 1
  + docker info *> $null
  + CategoryInfo : NotSpecified: (WARNING: No blk..._device support:String) [], RemoteException
  + FullyQualifiedErrorId : NativeCommandError
  ```
  副作用：只建了空的 `.secrets/` 目录，`startup.env` 没写出来，没有任何容器被创建。
- **状态**：**BLOCKED**
- **原因分析**：`scripts/start.ps1:6` 设了 `$ErrorActionPreference = "Stop"`，`:45` 跑 `docker info *> $null`。
  本机 Docker Desktop 28.0.4 的 `docker info` 会往 stderr 写一行 `WARNING: No blkio throttle...`；
  Windows PowerShell 5.1 在重定向 stderr 时会把原生命令的每一行 stderr 包成 ErrorRecord，
  EAP=Stop 下直接终止脚本。`*>` 重定向挡不住这个行为。
  讽刺的是 `scripts/start-platform.ps1:7-10` 的注释已经写明了这个坑并因此用 `Continue`，
  但外层 `start.ps1` 没同步。README 头部还明确写了 `#Requires -Version 5.1`，即声称支持 5.1。
- **影响面**：任何 Docker Desktop 会打 WARNING 的 Windows 机器（常见：没开 WSL2 cgroup 特性、
  或引擎在 Hyper-V 后端）+ 只有 PS 5.1 的用户，README 第一条命令必挂。
- **建议修法**：`start.ps1` 把 `$ErrorActionPreference` 改为 `Continue` 并显式检查 `$LASTEXITCODE`
  （与 `start-platform.ps1` 一致），或用 `cmd /c "docker info >nul 2>&1"` 探测。

## 2. README 路径 1（bash 形态）：`./scripts/start.sh`

- **时间**：03:34:46 开始
- **命令**（Git Bash，仓库根）：
  ```
  bash scripts/start.sh
  ```
- **预期**：同步骤 1。
- **实际进度（边跑边记）**：
  1. 03:34:46 端口探测 OK，写出 `.secrets/startup.env`（5432 / 8000 / 5280）、`platform.env`（三个内部
     token）、`platform-credentials.key`、`browser-action-token`。
  2. 03:34:5x `docker compose up -d postgres`：新建网络 `goai-infra-repomesh_default`、卷
     `goai-infra-repomesh_repomesh-postgres`，容器 22 秒内 healthy。**PASS**（镜像是本地缓存的，
     没测到拉取）。
  3. 因为没有 `.env`，脚本打印 `Model credentials are not configured; starting the setup plane first.`，
     跳过 AgentTeams 安装，直接 `docker compose --profile platform up -d --build api web bootstrap`。
  4. **03:34:5x → 03:38:5x 卡在 `load metadata for docker.io/library/python:3.12-slim` 约 4 分钟**。
- **卡点诊断（环境问题，非项目缺陷）**：本机 Docker Desktop 配置了四个 registry mirror，
  从宿主逐个探测全部已死：

  | mirror | 探测结果 |
  |---|---|
  | https://registry.docker-cn.com | TLS 握手失败 |
  | http://hub-mirror.c.163.com | HTTP 503 |
  | https://dockerhub.azk8s.cn | HTTP 403 |
  | https://mirror.ccs.tencentyun.com | TLS 握手失败（腾讯云内网专用） |

  而 `registry-1.docker.io` 从宿主直连 1.4 秒可达（401 正常），`docker.m.daocloud.io`、
  `docker.1ms.run` 均可达。对照实验：
  - `docker pull alpine:3.20`（走 mirror 列表）：**60 秒内没有任何输出**（被 timeout 杀掉）。
  - `docker pull docker.m.daocloud.io/library/alpine:3.20`（显式活 mirror）：**12.9 秒完成**。

  结论：buildkit 对每个 `FROM` 都要把四个死 mirror 逐个超时后才回落 docker.io，每个基础镜像
  白等约 4 分钟；本项目要拉 `python:3.12-slim`、`node:22-alpine`、`nginx:1.27-alpine` 三个。
  **本次不改 Docker Desktop 设置**（属于用户的系统配置），让 README 路径自己跑到底。
- **与项目相关的观察**：`Dockerfile` / `frontend/Dockerfile` 已给 pip、npm 配了国内镜像，
  但基础镜像仍写死 `docker.io`，没有 `BASE_IMAGE` 之类的 build-arg 可换源。在国内网络下这是
  从零构建最慢、最容易失败的一环，README 只字未提。

- **构建阶段结果（03:34:46 → 03:54:42，共 20 分钟）**：三张镜像最终都建出来了：

  | 镜像 | 基础镜像元数据解析 | 基础镜像拉取 | 项目层 | 小计 |
  |---|---|---|---|---|
  | api | 276.7s（死 mirror 逐个超时） | 322.3s | apt+pip 约 70s | 约 11 分钟 |
  | bootstrap | 复用 python:3.12-slim（已本地） | 0 | apt+pip 约 66s | 约 1.5 分钟 |
  | web | node:22-alpine + nginx:1.27-alpine 各等数分钟 | nginx 一个 15.5MB 层停在 0B 达 230s 后才开始传 | npm ci + build 正常 | 约 7 分钟 |

  在有正常镜像源的机器上，这 20 分钟里约 17 分钟是纯等待。
- **最终结果**：退出码 1。最后三行：
  ```
   web  Built
  network agentteams-net declared as external, but could not be found
  EXIT=1
  ```
  没有任何 api / web / bootstrap 容器被创建。postgres 仍在跑。
- **状态**：**BLOCKED**（项目缺陷，与环境无关）
- **原因分析**：`compose.yaml:256-263` 把卷 `agentteams-storage`（实名 `agentteams-data`）和网络
  `agentteams`（实名 `agentteams-net`）声明为 `external: true`，`api` 服务（`:93`、`:97-99`）
  两者都挂。这两个对象只有 AgentTeams 安装器跑过之后才存在。而 `start-platform.sh:116-124`
  的「Model credentials are not configured; starting the setup plane first」分支**正是为
  AgentTeams 尚未安装的白机设计的**，它接着在 `:226` 起 `api`，必然撞上不存在的外部对象。
  `start-platform.ps1` 是同一套编排文件，走到这一步同样会挂。
  换句话说：README「no manual configuration」承诺的路径在结构上不可能走通——api 依赖 AgentTeams
  的网络/卷，AgentTeams 又要等 api 起来后通过 setup plane 拿到模型凭据才装。
- **影响面**：所有没装过 AgentTeams 的机器（= 所有新用户），且 README 的 Docker-first 两条命令都受影响。
- **建议修法**（任选其一）：
  1. `start-platform.*` 在 setup-plane 分支里先 `docker network create agentteams-net` /
     `docker volume create agentteams-data`（幂等，`|| true`），让外部对象先于安装器存在；
  2. 或把 `external: true` 去掉，由 compose 自己创建同名对象（安装器侧要确认它对已存在的
     网络/卷是复用而不是报错）。
- **本次绕法**：手工补建两个外部对象后重跑 `start.sh`（见步骤 3）。

## 3. 绕过后重跑：`docker network create agentteams-net && docker volume create agentteams-data && bash scripts/start.sh`

- **时间**：03:58:34 → 04:00:56（2 分 22 秒）
- **预期**：外部对象存在后，setup-plane 分支应能把 api / web / bootstrap 起到 healthy。
- **实际**：退出码 0，打印
  ```
  RepoMesh is ready at http://127.0.0.1:8000/docs
  RepoMesh console is ready at http://127.0.0.1:5280
  Open RepoMesh: http://127.0.0.1:5280
  ```
  容器：postgres / api / web / bootstrap 全部 healthy。HTTP 探测：`/health/live`、`/health/ready`、
  `/docs`、`5280/`、`/api/v1/setup/status` 全 200。`setup/status` 摘要：
  `database=ready, internal_auth=ready, agentteams=missing, matrix=missing,
  model=waiting_for_user, administrator=waiting_for_user`。
- **状态**：**WORKAROUND**（靠手工补建两个外部对象才过）
- **附带观察**：
  - 即便三张镜像已在本地，`--build` 仍让 buildkit 重新解析三个基础镜像的元数据，
    本次又各花 59.6s / 116.0s / 56.6s（死 mirror 的税）。真正的 compose up 只用了约 30 秒。
  - 日志里出现一行 `http2: server: error reading preface from client //./pipe/dockerDesktopLinuxEngine:
    file has already been closed`，是 Docker Desktop 管道噪音，不影响结果。
  - 注意：`.secrets/` 下的四个文件是步骤 2 首跑时生成的，`ensure_secret` 在重跑时正确复用了旧值。

## 4. 控制台首屏 + README「full platform」路径：`.env` 里给模型密钥后再跑 `start.sh`

### 4a. 控制台首屏（只看不动）

- 04:02 在浏览器打开 `http://127.0.0.1:5280`：登录门正常渲染，标题「登录控制平面」，
  用户名 / 密码（至少 12 位）/ 登录按钮，底部「首次部署？初始化管理员」。
- **状态**：**PASS**（渲染层面）。
- **留给操作者的两步**（账号创建与在页面里填 API 密钥，本记录不代做）：
  1. 点「初始化管理员」建本地管理员并登录。
  2. 进入设置页填模型 API key（DeepSeek 兼容接口），保存后由 `bootstrap` 容器在**容器内**跑
     `agentteams-install.sh`（`src/repomesh/integrations/bootstrap/executor.py:69`），
     用挂进去的 `/var/run/docker.sock` 建 AgentTeams，然后 `docker restart` api。这是为 Windows
     设计的正道，因为安装器在 Linux 容器里跑，绕开了 4b 要暴露的宿主侧 socket 问题。
     进度看 `GET /api/v1/setup/bootstrap`（需要管理员会话）。

### 4b. README「Run locally → full platform」路径：`.env` + `./scripts/start.sh`

- **准备**：`cp .env.example .env`，把备份里用户自己的 `REPOMESH_MODEL_API_KEY` 值填回去
  （BASE_URL / MODEL 用 example 的 DeepSeek 默认值）。
  - **观察**：`.env.example` 内容整体重复了一遍（第 7-9 行与第 91-93 行、第 59-67 行与第 143-151 行
    完全相同），像是合并残留；不影响功能但会让新用户困惑。
- **时间**：04:02:xx 开始
- **命令**：`bash scripts/start.sh`（Git Bash）
- **预期**：README：脚本读 `.env` → 起 postgres → 因为没有 controller 且有模型密钥，自动
  `bash components/agentteams/install/agentteams-install.sh` 装 AgentTeams → 取 controller token /
  Matrix token / MinIO 凭据 → 起 api/web/bootstrap。
- **实际进度（边跑边记）**：
  1. postgres 复用。打印 `AgentTeams Controller is missing; installing it automatically.`
  2. 安装器以 non-interactive 模式跑（`.env.example` 里的 `AGENTTEAMS_NON_INTERACTIVE=1` 生效），
     所有配置项取默认：端口 18080/18001/18088/18888、dashboard 13000、admin 用户 `admin`、
     E2EE 关、Worker 空闲 720 分钟、host_share = `/c/Users/18092`。
  3. 生成密钥，写出 `C:\Users\18092\agentteams-manager.env`。
  4. **打出 `未找到容器运行时 socket（Manager 无法直接创建 Worker 容器…）`**：这是
     `agentteams-install.sh:1527 detect_socket()` 用 `[ -S /var/run/docker.sock ]` 探测 POSIX
     socket 文件，Windows 上必然为空。已知后果（见 2026-08-15 记录）：后面 `docker run -v
     :/var/run/docker.sock` 挂载参数畸形，退出码 125。
  5. 04:03 起在拉 `higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-embedded:v1.2.0`
     （2.47GB），安装器把 pull 输出吞了，日志里看不到进度，只能从宿主进程列表确认。

- **最终结果**：04:15:43 退出，`start.sh` 退出码 125（透传自 docker）。最后几行：
  ```
  [AgentTeams] 正在启动 Manager 容器...
  docker: invalid spec: :/var/run/docker.sock: empty section between colons
  Run 'docker run --help' for more information
  EXIT=125
  ```
  拉取账单（全部成功，阿里云仓库很快）：`agentteams-embedded` 2.47GB、`manager-copaw` 1.93GB、
  `agentteams-worker` 3.15GB、`copaw-worker` 3.26GB、`hermes-worker` 1.63GB，约 12.4GB / 12 分钟。
  **三个 Worker 镜像一个不落全拉了**，哪怕本项目只用 copaw 一种。
  没有任何 AgentTeams 容器被创建；setup plane 的四个容器仍在跑。
- **状态**：**BLOCKED**（项目缺陷：Windows 上 `start-platform.sh` 不该直接调 vendored 的 `.sh` 安装器）
- **原因分析**：`agentteams-install.sh:1527 detect_socket()` 只用 `[ -S ... ]` 探测 POSIX socket 文件，
  Windows（Docker Desktop 用命名管道）上返回空串，随后 `docker run -v "${CONTAINER_SOCK}:/var/run/docker.sock"`
  变成 `-v :/var/run/docker.sock`。安装器上游 README 标注 `.sh` 是「On Linux」用的；
  但 `scripts/start-platform.sh:112` 无条件调用它，而 README 又把 `./scripts/start.sh` 列为 Windows 用户
  可用的 bash 形态。官方 Windows 安装路径是 `agentteams-install.ps1`，它写死了 `//var/run/docker.sock`。
- **影响面**：Windows + Git Bash 用户走 `start.sh` 且配了模型密钥时必挂，且是在**下载 12GB 之后**才挂。
- **建议修法**：`start-platform.sh` 在 MSYS/Cygwin 下（`uname -s` 含 `MINGW`/`MSYS`）改调 `.ps1` 安装器，
  或至少在调用前 `export CONTAINER_SOCK=//var/run/docker.sock`（安装器若支持该覆盖）；同时把 socket
  探测提前到任何镜像拉取之前，fail fast。

## 5. README「Run locally → full platform」Windows 形态：`.\scripts\start-platform.ps1`

- **背景**：README 写「use Docker and PowerShell 7+」，但 `docs/clean-startup-guide-20260831.md` 与
  提交 `4d4abadd`（PS 5.1-safe secret generation）声称 5.1 已可用。本机只有 5.1，正好验证。
  `.env` 沿用 4b（含模型密钥）。上一步残留：`C:\Users\18092\agentteams-manager.env` 已被 `.sh` 安装器写过。
- **命令**：
  ```
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-platform.ps1
  ```
- **时间**：04:17:47 → 04:17:57（10 秒）
- **预期**：README：装 AgentTeams → 起完整平台。
- **实际**：退出码 1。PS 5.1 语法层面这次**过了**（`4d4abadd` 的修复有效）。挂在安装器选镜像：
  ```
  [AgentTeams ERROR] Embedded controller image is not available in the registry:
    - tried: higress-registry.us-west-1.cr.aliyuncs.com/agentteams/agentteams-embedded:v1.2.0
    - tried: higress-registry.us-west-1.cr.aliyuncs.com/agentteams/agentteams-embedded:latest
  AgentTeams installation failed.
  所在位置 scripts/start-platform.ps1:168
  ```
- **状态**：**BLOCKED**（环境因素 + vendored 安装器缺陷叠加）
- **原因分析**：
  - `agentteams-install.ps1:139-165 Get-AgentTeamsTimeZone` 把 Windows 时区 ID 映射成 IANA 名，
    `:176-190 Get-Registry` 再按 IANA 名选阿里云仓库地域：`America/*` → `us-west-1`。
    **本机 Windows 时区是 `Pacific Standard Time`**（→ `America/Los_Angeles`），于是去了 us-west-1。
  - 实测 `docker manifest inspect higress-registry.us-west-1.cr.aliyuncs.com/agentteams/agentteams-embedded:v1.2.0`
    → `denied: requested access to the resource is denied / unauthorized`；`/v2/.../tags/list` 匿名 401。
    即 **us-west-1 仓库不对匿名开放**（或根本没发这个镜像），而 `cn-hangzhou` 匿名可拉（4b 已证明）。
  - `.sh` 安装器的 `detect_timezone()` 只认 `/etc/timezone`、`/etc/localtime`、`timedatectl`，Git Bash
    里三样都没有，non-interactive 下落到默认 → `cn-hangzhou`。**同一台机器两个安装器选了不同仓库**。
  - 安装器发现镜像拉不到时没有任何回退（比如退回 cn-hangzhou），直接退出。
- **影响面**：任何 Windows 时区设成美洲的机器（包括人在国内但把时钟设成美国时间的开发者，以及
  真正的北美用户）。北美用户用 PS 路径会**必挂**，除非 us-west-1 仓库其实对外开放而本次是网络问题。
- **建议修法**：本项目已经在 `.env.example` 里钉死了 `AGENTTEAMS_VERSION=v1.2.0`，应一并钉
  `AGENTTEAMS_REGISTRY=higress-registry.cn-hangzhou.cr.aliyuncs.com`（两个安装器都认这个覆盖），
  别让安装器按时区猜；上游安装器则该在 pull 失败时回退到默认地域。
- **本次绕法**：`.env` 追加 `AGENTTEAMS_REGISTRY=higress-registry.cn-hangzhou.cr.aliyuncs.com` 后重跑（步骤 6）。

## 6. 绕过后重跑：`.env` 加 `AGENTTEAMS_REGISTRY` → `.\scripts\start-platform.ps1`

- **时间**：04:20:10 → 04:25:53（5 分 43 秒；其中安装器约 2.5 分钟，静默的 compose 重建约 3 分钟）
- **命令**：`.env` 末尾追加 `AGENTTEAMS_REGISTRY=higress-registry.cn-hangzhou.cr.aliyuncs.com`，然后
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-platform.ps1`
- **实际**：退出码 0，打印 `RepoMesh is ready ... / RepoMesh console is ready ...`。安装器这次走通：
  `agentteams-controller`（Tuwunel/Matrix 2s、MinIO 0s、Higress 6s）+ `agentteams-manager` 起来，
  Manager Agent 在 300s 窗口内就绪；写出 `~/agentteams-manager.env`，卷 `agentteams-data`。
  api / web / bootstrap 被重建，三者 healthy。**但接线没有发生**：

  | 探测 | 结果 |
  |---|---|
  | `.secrets/platform-runtime.env` | **0 字节**（04:22 写出） |
  | api 容器内 `REPOMESH_AGENTTEAMS_*` | 一个都没有 |
  | `GET /api/v1/setup/status` | `model=true` 但 `agentteams=missing, matrix=missing` |
  | `GET /health/ready` | 200 `{"status":"ready"}` —— 因为 api 仍以 `REPOMESH_AGENTTEAMS_REQUIRED=false` 跑，是**假绿** |
  | controller `/healthz` | ok；`/var/run/agentteams/cli-token` 642 字节存在 |
  | Matrix admin 登录（用 env 里的账密） | 返回 `access_token`，正常 |
  | MinIO 账密 | env 里都在 |
  | 18080 / 18888 / 18001 | 200；**13000 dashboard 没起**（PS 安装器不装 dashboard，`.sh` 会装） |

  即所有输入都齐全，脚本却把运行时配置写成了空文件，然后照常宣布成功。
- **状态**：**BLOCKED**（项目缺陷，静默失败）
- **原因分析（已隔离复现）**：`scripts/start-platform.ps1:80-84` 为 PS 5.1 新写的
  `function Set-Utf8NoBom([string]$Path, [string[]]$Lines)` 只接**位置参数**，没有
  `ValueFromPipeline`；而 `:285-295` 用**管道**调用它：`@("REPOMESH_AGENTTEAMS_REQUIRED=true" ...) | Set-Utf8NoBom $RuntimeTemporary`，
  于是 `$Lines` 为 `$null`，`WriteAllText` 写出空文件。把函数单独抽出来测：
  ```
  @("A=1";"B=2";"C=3") | Set-Utf8NoBom $t   -> size=0
  Set-Utf8NoBom $t @("A=1";"B=2")           -> size=10
  ```
  同一脚本里 `startup.env` / `platform.env` 之所以正常，是因为那些调用点用的是位置形式
  （见本记录下方的调用点清单）。这个 bug 是 `4d4abadd`「PS 5.1-safe secret generation」引入的，
  即 08-31 修 PS 5.1 兼容时把最关键的那次写入修坏了，而且脚本对空文件没有任何校验。
- **影响面**：所有 Windows 用户走 `start-platform.ps1`（含 `start.ps1` 修好后的路径）：装完 AgentTeams
  照样得到一个没接 AgentTeams 的 api，materialize / 派活会 503，而所有健康检查都是绿的。
- **建议修法**：`:295` 改成位置调用 `Set-Utf8NoBom $RuntimeTemporary @(...)`，或给函数加
  `[Parameter(ValueFromPipeline)]` + `process/end` 块；并在 `Move-Item` 前断言文件非空。
  另外 `/health/ready` 在 `REQUIRED=false` 下不该被当作「平台就绪」信号，README 的检查建议应换成
  `setup/status` 的 `agentteams`/`matrix` 字段。
- **本次绕法**：AgentTeams 已装好，改用 bash 形态 `./scripts/start.sh` 做接线（它用 heredoc 写文件，
  且会因 controller 已健康而跳过安装器）。见步骤 7。

## 7. 接线绕法：AgentTeams 已存在的前提下再跑 `./scripts/start.sh`

- **时间**：04:28:27 → 04:30:39（2 分 12 秒，其中约 1.5 分钟又是基础镜像元数据重解析）
- **命令**：`bash scripts/start.sh`（`.env` 同步骤 6）
- **实际**：退出码 0。脚本探测到 controller 健康 → 跳过安装器 → 读到 controller token → Matrix 登录拿到
  token → 从 `~/agentteams-manager.env` 取 MinIO 账密 → **`platform-runtime.env` 写出 1147 字节，9 个键齐全**
  → `compose up --build api web bootstrap`。
  但 `setup/status` 仍是 `agentteams=missing, matrix=missing`：compose 只重建了 web，**api 容器没动**
  （`Up 6 minutes`）。原因：运行时配置是 `.secrets/` 挂载卷里的文件，不在 compose 跟踪的容器配置里，
  compose 认为 api 没变化就不重建；而 api 只在进程启动时读一次 `platform-runtime.env`。
- **状态**：**WORKAROUND**（文件写对了，但接线对已存在的 api 不生效）
- **原因分析**：`start-platform.sh` / `.ps1` 写完 `platform-runtime.env` 后只靠 `compose up`，没有像
  `src/repomesh/integrations/bootstrap/executor.py` 那样显式 `docker restart` api。在真正的
  Linux 白机上 `.sh` 一次跑通时 api 是首次创建，不会踩到；但任何「先起了 setup plane、后装 AgentTeams」的
  顺序（= 本项目自己设计的 UI 路径，以及 Windows 上必然的混合路径）都会踩到。
- **建议修法**：写完运行时配置后，若 api 容器已存在则 `docker compose --profile platform restart api`
  （或 `up -d --force-recreate api`）；并在脚本末尾用 `setup/status.checks.agentteams` 做断言，而不是只等 healthcheck。

## 8. 手动重启 api：`docker restart goai-infra-repomesh-api-1`

- **时间**：04:33，9 秒后 healthy。
- **结果**：`GET /api/v1/setup/status`：
  `model=true, database=true, agentteams=true, matrix=true, internal_auth=true`；
  未就绪项只剩 `administrator: waiting_for_user`（以及可选的 github_app / 待 onboarding 的 repositories、agent_directory）。
  `ready_for_project_creation=false` 仅因为管理员还没建。bootstrap 容器 healthy，
  日志 `bootstrap worker ready mode=production`。
- **状态**：**PASS**（平台进程层面从零到全接线完成）

## 9. 终态（04:34）

| 组件 | 状态 | 地址 |
|---|---|---|
| postgres（compose） | healthy | 127.0.0.1:5432 |
| api（compose，已接 AgentTeams） | healthy | http://127.0.0.1:8000（/docs） |
| web（nginx 控制台） | healthy | http://127.0.0.1:5280 |
| bootstrap reconciler | healthy，production 模式 | - |
| agentteams-controller | healthz ok | 18080 网关 / 18001 Higress / 18088 Element |
| agentteams-manager | running | 18888 |
| agentteams-dashboard | **未安装**（PS 安装器不装） | 13000 不通 |

宿主侧生成态：`.env`（= `.env.example` + 用户自己的模型密钥 + `AGENTTEAMS_REGISTRY` 钉死）、
`.secrets/`（6 个文件）、`~/agentteams-manager.env`、`~/agentteams-manager/`、`~/agentteams-install.log`。
整拆前的旧文件仍在 `D:\Project4work\repomesh-wipe-backup-20260902\`，没有回填任何 token。

**留给操作者的最后一步**：浏览器打开 http://127.0.0.1:5280 → 「初始化管理员」→ 登录。
建完管理员 `ready_for_project_creation` 应翻成 true。之后才是扫仓库 / 建团队 / 物化，那些不在本次范围。

**总耗时**：03:33 → 04:34，约 61 分钟。粗分：死 mirror 造成的纯等待约 25 分钟、AgentTeams 镜像拉取约 12 分钟、
真正的构建/启动约 8 分钟、其余是各次失败与诊断。

## 10. 问题清单（按严重度）

| # | 严重度 | 位置 | 现象 | 性质 | 状态 |
|---|---|---|---|---|---|
| 1 | **致命** | `scripts/start.ps1:6,45` | PS 5.1 下 `docker info` 的 stderr WARNING 触发 `EAP=Stop` 终止，README 第一条 Windows 命令 2 秒即死 | 项目 | 未修，改走 `.sh` |
| 2 | **致命** | `compose.yaml:256-263`、`start-platform.*` setup-plane 分支 | api 硬依赖 `external` 的 `agentteams-net`/`agentteams-data`，白机上 AgentTeams 尚未安装 → 建完镜像后 `compose up` 必挂；README 的「零配置进控制台」路径在结构上走不通 | 项目 | 未修，手工 `docker network/volume create` 绕过 |
| 3 | **致命** | `scripts/start-platform.sh:112` → `agentteams-install.sh:1527` | Windows Git Bash 下调用 Linux-only 安装器，`detect_socket()` 为空 → 拉完 12GB 后 `-v :/var/run/docker.sock` 退出 125 | 项目 + vendored | 未修，改走 `.ps1` |
| 4 | **致命（静默）** | `scripts/start-platform.ps1:80,295` | `Set-Utf8NoBom` 无管道绑定却被管道调用 → `platform-runtime.env` 0 字节 → api 未接 AgentTeams 却全绿 | 项目（`4d4abadd` 引入） | 未修，改用 `.sh` 接线 |
| 5 | 高 | `agentteams-install.ps1:139-190` + `.env.example` | 按时区猜仓库地域，Pacific 时区 → `us-west-1`（匿名 401），无回退；`.sh` 在同机选 `cn-hangzhou` | 环境 + vendored；项目应钉 `AGENTTEAMS_REGISTRY` | `.env` 覆盖绕过 |
| 6 | 高 | `start-platform.*` 末尾 | 写完运行时配置不重启已存在的 api；`/health/ready` 在 `REQUIRED=false` 下 200 是假绿 | 项目 | `docker restart` 绕过 |
| 7 | 中（环境） | Docker Desktop 设置 | 四个 registry mirror 全死，每个 `FROM` 白等 1-5 分钟；基础镜像无换源 build-arg | 环境；README 未提示 | 未改设置 |
| 8 | 低 | `.env.example` | 全文重复了一遍（7-9/91-93、59-67/143-151 行） | 项目 | - |
| 9 | 低 | 两个安装器 | `.sh` 装 dashboard 而 `.ps1` 不装；`.sh` 把三种 Worker 镜像（8GB）全拉；pull 进度被吞；PS 5.1 控制台中文乱码 | vendored | - |
| 10 | 低 | `README.md` | 说 `start-platform.ps1` 要 PS 7+（实际 5.1 可跑）；「cold path 尚未端到端跑过」——本记录即该实走 | 文档 | - |

**一句话结论**：README 列出的三条 Windows 启动路径（`start.ps1`、`start.sh`、`start-platform.ps1`）
在白机上**没有一条能原样跑通**；把各自能走的段落拼起来、外加两处手工补救，约一小时可以到达
「平台全接线、只差建管理员」的状态。#1、#2、#4 是几行就能修的，修完之后 `start.ps1` 应能一条命令到底。

原始日志：`2026-09-02-step1-start-ps1.log` … `2026-09-02-step7-start-sh-wire.log`。
