<div align="center">

<img src="docs/assets/logo.svg" alt="RepoMesh — 可观测的交付控制平面" width="820">

[English](README.md) · **简体中文** · [日本語](README.ja.md)

![Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-c99e52?style=flat-square&labelColor=16130d)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-c99e52?style=flat-square&labelColor=16130d&logo=python&logoColor=e9dec2)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-c99e52?style=flat-square&labelColor=16130d&logo=fastapi&logoColor=e9dec2)
![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-c99e52?style=flat-square&labelColor=16130d&logo=postgresql&logoColor=e9dec2)
![React 19](https://img.shields.io/badge/React-19-c99e52?style=flat-square&labelColor=16130d&logo=react&logoColor=e9dec2)
![Vite 8](https://img.shields.io/badge/Vite-8-c99e52?style=flat-square&labelColor=16130d&logo=vite&logoColor=e9dec2)
![TypeScript 6](https://img.shields.io/badge/TypeScript-6-c99e52?style=flat-square&labelColor=16130d&logo=typescript&logoColor=e9dec2)
![Docker Compose](https://img.shields.io/badge/Docker-compose-c99e52?style=flat-square&labelColor=16130d&logo=docker&logoColor=e9dec2)

**面向多仓库编码智能体交付的可观测控制平面。**
需求变成计划，计划变成真实仓库里的智能体团队，
沿途每一道闸门都留在账上。

|  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **可搭配** | Claude Code | OpenAI Codex | OpenCode | Cursor Agent | Copilot CLI | Aider | Goose |

*23 个编码智能体 CLI 已接在 `src/repomesh/integrations/coding_agents/catalog.py`；
运行时端口对供应商中立，能接活的 CLI 就能雇。*

</div>

---

RepoMesh 是面向多仓库编码智能体交付的可观测控制平面。项目、工程 Spec、任务、上下文、
验证、变更集、恢复与审计历史都归 RepoMesh 所有。AgentTeams 是 RepoMesh 的第一方运行时
控制平面，负责团队、worker、技能与消息传输。

![RepoMesh 交付控制台：扁平的 issue 列表，每条的交付阶段以徽标承载 ——
计划待物化、执行中、发布、已暂停、待决策](docs/assets/console.svg)

其余五个面 —— 审核台、仓库、团队、智能体、观测 —— 画在[控制台图示](docs/console-tour.md)里。

## 打开控制台

下面两条路，任选其一，都能把一份全新的 clone 带到浏览器里的交付控制台，不需要手工配置。
它们是**并列的两条路，不是两个步骤**：两者都要占用 8100 端口。

**开发启动器** —— 带热重载；宿主需要 Docker、uv 和 Node 20+：

```powershell
.\scripts\dev-up.ps1                # -Seed 灌演示数据，-NoBrowser 不自动开浏览器
```

```bash
./scripts/dev-up.sh                 # --seed, --no-browser
```

它会起 postgres、迁移到链尾、把 API 跑在 8100、Vite 跑在 5280，然后打开
`http://127.0.0.1:5280`。每一步都先探测、再跳过已经在服务的组件，所以**重跑本脚本就是
回到可用状态的正常做法**——而且脚本从不重启、迁移或停掉任何不是它自己起的东西。
`.\scripts\dev-down.ps1` / `./scripts/dev-down.sh` 只收它自己起的组件，逐项征求确认。

**全栈 compose** —— 宿主只需要 Docker：

```bash
docker compose --profile console up -d --build
```

打开 `http://127.0.0.1:8100`。nginx 托管构建好的控制台，并把 `/api` 反向代理到 API 容器；
容器启动时迁移自己的私有数据库：同源、无 CORS、无开发代理。起来之后灌演示数据：

```bash
docker compose --profile console exec console-api python scripts/seed-console-demo.py
```

`REPOMESH_CONSOLE_PORT` 把控制台挪离 8100，`REPOMESH_POSTGRES_PORT` 把开发库挪离 5432。
用 `docker compose --profile console down` 拆栈，加 `-v` 连库一起删。

控制台打开是一道**登录门**。全新的库里没有任何账号，所以第一次访问要走「初始化管理员」，
凭据只留在你自己机器上。两个面的认证方式**故意不同**：读模型认 Bearer action token，
而人工控制——审核台、检查点决策——认会话。这就是为什么 agent 的令牌批不了任何东西。

老实说状态：已经走通的是**可重入路径**（每个组件都已在服务、全部跳过）和 compose 配置。
从一台空机器出发的冷启动路径**还没有端到端跑过**，所以如果某一步的失败信息没解释清楚原因，
请说出来——那条信息和那条命令一样，都是交付物。

## 当前里程碑

仓库里已经有团队、持久化、运行时集成与 Context 四块地基：

- 一个把模块接到可替换适配器上的组装根。
- 十五个业务模块，各自带机器可读的 owner 与边界。
- 一条可用的 Repository Intelligence 纵切。
- 一个对供应商中立的 Agent Runtime 端口、23 个 CLI 适配器，以及一个七场景的 mock 适配器。
- AgentTeams v1.2.0 源码以钉版 subtree 嵌在 `components/agentteams` 下。
- Runtime v1 的 JSON 契约，以及 Python 版 RepoMesh Runner 执行地基。
- 带版本的 Context 对象、权限求交、不可变 bundle、增量与访问审计。
- 模块自带的业务 API 路由与平台入口，由一个**不含行为**的顶层路由聚合。
- CODEOWNERS、PR 检查单、适配器契约测试与架构测试。
- PostgreSQL 持久化、Alembic 迁移、事务事件、审计、outbox 与就绪探针。

## 本地运行

这里起的是 8000 端口上的 v1 平台 API——**不是**交付控制台；控制台是跑在 8100 上的第二个实例，
要它请看上面的「打开控制台」。

```powershell
uv sync --extra dev
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn repomesh.main:app --reload
```

打开 `http://127.0.0.1:8000/docs`。要完整的本地平台，用 Docker 和 PowerShell 7+ 从仓库内
自带的安装器装 AgentTeams，并起容器化的 RepoMesh API：

~~~powershell
.\scripts\start-platform.ps1 -InstallAgentTeams
~~~

Linux 上：

~~~bash
./scripts/start-platform.sh --install-agentteams
~~~

完整平台用**同一个** OpenAI 兼容的模型连接，同时供 RepoMesh 规划与 AgentTeams 智能体使用：

```dotenv
REPOMESH_MODEL_API_KEY=your-key
REPOMESH_MODEL_BASE_URL=https://api.deepseek.com/v1
REPOMESH_MODEL=deepseek-chat
```

进阶部署可以分别覆盖 AgentTeams 的 `AGENTTEAMS_LLM_*` 或 RepoMesh 规划的
`REPOMESH_DEEPSEEK_*` 变量。编码智能体 CLI 的认证是另一条线，互不相干。

启动脚本会在被 gitignore 的 `.secrets/platform.env` 里生成 Runner、agent-action 与 MCP 网关
三个令牌，载入 AgentTeams 控制器令牌，并取一个 Matrix access token。首次运行的就绪状态看
`GET /api/v1/setup/status`，已安装 CLI 的认证情况看 `GET /api/v1/setup/coding-agents`。
扫描完一个仓库之后，`POST /api/v1/repositories/{repository_id}/agent-team` 会为它建立长期的
Repository Leader、默认 Worker 与 AgentTeams Team。交付策略存在 `/api/v1/delivery` 下的组织与
仓库策略端点里，**不在 `.env` 里改**。

跑全部检查：

```powershell
uv run ruff check .
uv run pytest
```

## 许可证

RepoMesh 采用 Apache License 2.0，见 `LICENSE`。

## 启动器到底做了什么，以及怎么手工做一遍

`scripts/dev-up.*` 就是下面四步，按顺序执行，每步前面加一个探测。当某一步失败、当你想要
不同的布局、或者当你要改启动器本身时，读这一节。

**为什么是 8100。**「本地运行」起的是 8000 上的 v1 平台 API。`frontend/` 下的交付控制台**不跟它说话**：
Vite 开发服务器把 `/api` 代理到**同一个应用在 8100 上的第二个实例**，那个实例提供交付读模型
与本地身份端点。端口写死在 `frontend/vite.config.ts` 里，所以启动器把 8100 和 5280 当固定值。

1. `docker compose up -d postgres` —— 发布 `REPOMESH_POSTGRES_PORT`（默认 5432），与默认 DSN
   `postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh` 对应。
2. `uv sync --extra dev` 然后 `uv run alembic upgrade head` —— alembic 读 `REPOMESH_DATABASE_URL`，
   所以迁移和服务端必须拿到**同一个值**。绝不要把它指向属于别的东西的数据库。
3. 用 `frontend/.env.development` 期望的那个令牌起读模型实例，否则每个读模型调用都是 401：

   ```powershell
   $env:REPOMESH_AGENT_ACTION_TOKEN = "console-dev-token"
   uv run uvicorn repomesh.main:app --host 127.0.0.1 --port 8100
   ```

4. `cd frontend && npm install && npm run dev`，然后打开 `http://127.0.0.1:5280`。

这里的就绪判据是 `/docs`（或者根路径给出任何 HTTP 应答），**不是** `/health/ready`：在这套最小
配置下就绪探针会报 503，拿它判定会把一个健康的控制台说成坏的。

`frontend/README.md`（「联调后端起法」）里有同一套走法，另附降级说明、种子脚本与数据源开关。

## 团队入口

- 文档索引：`docs/README.md`
- 技能生命周期：`capabilities/skills/README.md`
- 开源就绪清单：`docs/open-source-readiness.md`
- 第三方声明：`THIRD_PARTY_NOTICES.md`
- 当前阶段计划（全流程 GUI 闭环，施工已收官）：`docs/development/full-loop-plan-20260812.md`
- 团队交接（架构章节有效；状态章节已被取代）：`docs/development/team-handoff.md`
- 并行工作计划：`docs/development/parallel-work-plan.md`
- 公共契约：`docs/contracts/public-contracts-v0.1.md`
- 交付读模型契约：`docs/contracts/delivery-read-model-v0.1.md`（v0.2–v0.4 为增量，全部有效）

- 模块 owner 与职责：`docs/architecture/module-map.md`
- 依赖规则：`docs/architecture/dependency-rules.md`
- Runtime planes：`docs/architecture/runtime-planes.md`
- 数据库搭建：`docs/database.md`
- 数据库所有权：`docs/architecture/database-ownership.md`
- 团队工作流：`docs/architecture/team-development.md`
- 架构决策：`docs/adr/0001-independent-repomesh-core.md` 与
  `docs/adr/0002-first-party-agentteams-runtime.md`

每个模块拥有自己的 schema 与实现。消费方只能 import 生产方的 `contracts` 模块。外部系统与
第一方运行时进程一律经 `repomesh.integrations` 下的适配器跨越边界；具体实现只在
`repomesh.bootstrap` 里选定。
