# RepoMesh 当前可用启动方式

更新时间：2026-09-03

## 1. 结论

需要启动完整产品栈时，包括执行面依赖，只使用这个标准入口：

```powershell
.\scripts\start-platform.ps1 -InstallAgentTeams
```

该入口负责启动或检查 PostgreSQL、AgentTeams Controller 及其 Matrix/MinIO/网关、RepoMesh API、前端和 bootstrap reconciler。它是完整执行面的正确启动入口，但每次启动后仍必须按本文的物化前检查确认 Worker 真正就绪。

默认地址：

- 前端：http://127.0.0.1:5280
- API：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs

## 2. 首次干净启动

前置条件只有 Windows PowerShell 5.1 或 PowerShell 7+、已启动 Linux 容器引擎的 Docker Desktop，以及当前仓库。完整平台不要求宿主机安装 Python、uv、Node.js 或 PostgreSQL。

在仓库根目录准备 `.env`，至少确认：

```dotenv
REPOMESH_MODEL_API_KEY=填写你的模型密钥
REPOMESH_MODEL_BASE_URL=填写 OpenAI 兼容接口地址
REPOMESH_MODEL=填写模型名
```

私有仓库扫描的 GitHub Token 只放在本机配置或前端私有仓库输入框中，不写入文档、不提交 Git。

启动：

```powershell
.\scripts\start-platform.ps1 -InstallAgentTeams
```

启动脚本会读取当前 `.env`，启动本项目 PostgreSQL，安装或检查 AgentTeams，加载 Controller Token，获取 Matrix 访问令牌，配置 MinIO 对象存储，启动 API、前端和 bootstrap，并执行就绪检查。

看到以下信息，只能确认平台进程启动完成；不能据此确认物化或 Worker 已开工：

```text
RepoMesh is ready at http://127.0.0.1:8000/docs
RepoMesh console is ready at http://127.0.0.1:5280
```

启动后检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health/ready
Invoke-WebRequest http://127.0.0.1:5280/
docker ps
```

首次进入控制台需要初始化本地管理员。初始化完成后重新登录，再进行扫描和物化。

## 3. 日常重启

Docker Desktop 或电脑重启后，在仓库根目录执行：

```powershell
.\scripts\start-platform.ps1
```

只有明确需要安装或修复 AgentTeams 执行面时才加 `-InstallAgentTeams`。不要自行复制旧容器环境变量，也不要手工替换 Controller、Matrix 或 MinIO 参数。

## 4. 开发模式的边界

只需要 API 和前端热重载时使用：

```powershell
.\scripts\dev-up.ps1 -NoBrowser
```

默认地址：

- API：http://127.0.0.1:8100
- 前端：http://127.0.0.1:5280

该模式适合前后端联调、扫描、分析和观测开发，但不包含 AgentTeams 执行面，不能用于需求物化、真实 Worker 派单或验证 Worker 消费任务。物化返回执行平面不可用是该模式的预期限制。

停止：

```powershell
.\scripts\dev-down.ps1
```

## 5. 纯 Docker 控制台模式的边界

兼容或 CI 场景可以使用：

```powershell
docker compose --profile console up -d --build
```

访问 http://127.0.0.1:8100。该模式使用独立数据库，只包含控制台和 API，不包含 AgentTeams Controller、Matrix、MinIO 执行面，因此不能用于物化或真实 Worker 执行。

停止：

```powershell
docker compose --profile console down
```

不要在未确认数据用途时追加 `-v`，因为它会删除该 profile 的数据库卷。

## 6. 严禁混用的方式

以下内容不属于正常启动流程：

- 不要设置 `AGENTTEAMS_FORCE_LEGACY=1`。legacy 架构可能绕过当前嵌入式 Controller，造成物化、房间和 Worker 状态不一致。
- 不要执行 `docker start <旧容器名>` 复用旧 Controller、Manager 或 Worker。旧容器可能携带过期二进制、环境变量、Matrix Token、MinIO 凭据、镜像和网络别名。
- 不要使用 `agentteams/copaw-worker:local` 作为真实 Worker。历史检查确认它可能只是休眠占位镜像。
- 不要混用 `AGENTTEAMS_*` 和手工复制的 `HICLAW_*` 配置。执行面环境变量应由启动脚本、安装器和 Controller 统一生成。
- 不要从 `manager-current.env`、`agentteams-controller.env`、临时容器环境文件或临时二进制快照恢复配置。

## 7. 数据隔离

启动前可检查最终 Compose 配置：

```powershell
docker compose config --profiles platform
```

确认 API 使用本项目的 `postgres` 服务，API 与 Worker 使用同一个 AgentTeams 存储桶，`REPOMESH_SECRETS_DIR` 指向当前项目的 `.secrets` 或明确指定的新目录，并且没有挂入其他项目的 PostgreSQL、MinIO 或 Matrix 数据目录。

以下命令不属于正常启动步骤，除非明确确认要销毁数据，否则不要执行：

```powershell
docker compose down -v
docker system prune
docker volume prune
```

启动脚本生成的运行凭据保存在被 Git 忽略的 `.secrets/` 中。不要把 API Key、GitHub Token、Matrix Token、密码或这些文件提交到仓库。

## 8. 物化前检查

完整平台启动后，按顺序确认执行面是否真正可用：

1. 前端打开并登录成功。
2. API readiness 返回成功。
3. AgentTeams Controller 健康检查成功。
4. 对应 Team 处于 `Active`。
5. Leader 和 Worker 是当前 Controller 管理的真实运行容器。
6. Worker 日志没有出现 `HICLAW_WORKER_NAME is required`、`openclaw.json not found in MinIO`、Access Key 不存在或签名不匹配。
7. 先用一个小任务验证消息投递和 Worker 消费，再批量物化或批量派单。

## 9. 故障处理

不要第一时间删除容器或卷。先保留不含敏感值的状态和日志：

```powershell
docker ps -a
docker compose --profile platform ps
docker compose --profile platform logs --tail 200 api
docker compose --profile platform logs --tail 200 bootstrap
docker logs --tail 200 agentteams-controller
```

日志中若出现 Token、密码或 API Key，提交前必须脱敏。

## 10. 干净复位与安装脚本重跑（仅限执行面故障时）

`agentteams-install.ps1` 是执行面（Controller + Manager + Worker）的安装入口。出现凭据错配、旧二进制注入、崩溃循环等不可自愈的问题时，才需要干净复位。日常重启走第 3 节，不要执行本节。

### 10.1 干净复位

```powershell
# 1) 停止并删除旧执行面容器
docker stop agentteams-manager agentteams-controller 2>$null
docker rm -f agentteams-manager agentteams-controller 2>$null
docker ps -a --format "{{.Names}}" | Select-String "^agentteams-worker-" | ForEach-Object {
    docker rm -f $_ 2>$null
}
# 2) 删除数据卷（MinIO 对象、worker 凭据、Matrix 状态全清）
docker volume rm agentteams-data
```

复位前如需保留容器内配置做诊断，可 `docker cp` 出 `/data/worker-creds` 等目录；不要用 `docker commit` 生成的镜像继续跑，它不携带正确 ENTRYPOINT。

### 10.2 用当前源码重建 embedded 镜像（可选，源码有改动时）

参考 `C:\Users\PC\.trae\work\6a7022c7c017ef30afc6ec04\Dockerfile.rebuild-controller`：多阶段构建从源码编译 controller + agt，基于 `clean-20260831` 基础镜像产出 `agentteams/agentteams-embedded:clean-20260831-fix`。CRLF 必须保持 LF（`*.sh`/`*.conf`/Dockerfile 由 `.gitattributes` 约束）。

### 10.3 重跑安装脚本（非交互、就地升级）

```powershell
$env:AGENTTEAMS_INSTALL_EMBEDDED_IMAGE = 'agentteams/agentteams-embedded:clean-20260831-fix'
$env:AGENTTEAMS_UPGRADE_KEEP_ALL   = '1'    # 跳过“升级方式”子菜单，保留全部数据
$env:AGENTTEAMS_NON_INTERACTIVE    = '1'    # 跳过“就地升级/全新重装”确认，默认就地升级
$env:AGENTTEAMS_READY_TIMEOUT      = '900'  # K8s 模式 Manager 首次启动需同步 1.8G workspace
.\components\agentteams\install\agentteams-install.ps1
```

注意：

- 缺 `AGENTTEAMS_UPGRADE_KEEP_ALL=1` 会在升级子菜单的 `Read-Host` 处卡住；缺 `AGENTTEAMS_NON_INTERACTIVE=1` 且容器在运行时会直接“安装已取消”。
- `AGENTTEAMS_RUNTIME=k8s` 时 Manager 每次启动都无条件全量 `mc mirror`（约 1.8G / 2.7 万文件）后才启动 CoPaw，300s 默认就绪超时不够，必须用 `AGENTTEAMS_READY_TIMEOUT` 覆盖。
- 重跑脚本会重建 Manager/Worker 容器，但 `agentteams-data` 卷内的 MinIO 对象和 `/data/worker-creds` 凭据会保留；`ensure` 逻辑幂等，凭据不漂移。

### 10.4 复位后验证

```powershell
# Manager 已就绪（CoPaw API 返回 agents）
docker exec agentteams-manager curl -sf http://127.0.0.1:18799/api/agents
# Manager CR 状态
docker exec agentteams-controller agt get managers default -o json   # phase: Running, welcomeSent: true
# Worker CR 状态
docker exec agentteams-controller agt get workers -o json            # phase/state: Running
# Higress consumer（manager + worker）与 AI route 授权
docker exec agentteams-controller curl -sf http://127.0.0.1:8001/v1/consumers
```

端到端验证：用 Manager Matrix token 创建与 worker 的 DM 房间并发一条任务消息，确认 worker 回复（完整链路：Matrix → Worker → Higress gateway 模型调用 → 响应）。

## 11. 推荐顺序

```text
准备当前 .env
  -> start-platform.ps1
  -> 检查 API / 前端 / bootstrap
  -> 登录控制台
  -> 小批次扫描与分析
  -> 确认 Team/Worker Ready
  -> 小批次物化
  -> 验证 Worker 消费
```

要在本地不接外部 git 托管、不接真实编码 CLI 的前提下把「issue → 发现链 → 计划 → 派单 → Runner 执行 → succeeded」整条执行链一次性跑通，按 `docs/one-shot-e2e-guide.md` 执行（平台现在自带 runner 服务与 mock 编码代理，fixture 仓库用 `scripts/seed_e2e_fixtures.py` 播种）。

本文只记录源码定义的启动入口和隔离规则，不把临时容器恢复、手工替换二进制、旧数据库迁移、旧镜像修补或历史快照恢复当作正常启动方式。
