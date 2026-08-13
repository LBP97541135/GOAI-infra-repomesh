# 平台产品化改造 — 开发记录与交接（2026-08-13）

分支：`codex/productize-platform-setup`

本轮把 RepoMesh 从「一堆需要手工拼装的服务」推进为「可一键启动、按组织管理、可视化装配团队」的控制平面。共 8 项，按依赖排序推进：
**① 一键启动 → ③ 组织模型 → ④ 异步接入 → ⑦ 团队持久化 → ⑤ Runner 检测 → ⑧ 项目路由 → ⑥ SCM 凭证**。

> ①→④→⑦ 已完成并提交；⑤ 已完成方案梳理，待开工；⑧⑥ 未开始。

---

## 全局约束（贯穿本分支所有提交）

- **慢慢推进**：每一项开工前先梳理现状+方案，确认后再动手，按检查点走。
- **Git 卫生**：一律用显式 `git add <paths>`，**禁止 `git add -A`**（保护 `.secrets/.env/*.pem` 等 gitignore 内容）；`.tmp_pytest/` 不入库。
- **提交粒度**：按逻辑拆分，通常「后端一提交 + 前端一提交」。
- **后端验证**：`uv run ruff check .` + `uv run pytest`。
- 未经明确要求不 `git push`（本次交接是用户明确要求推送）。

### 本机测试注意（Windows 环境）
本机跑 pytest 需带参数，且 basetemp 目录要先存在：
```bash
mkdir -p .tmp_pytest/bt
uv run pytest -q --basetemp=.tmp_pytest/bt -p no:cacheprovider
```
否则会报 `PermissionError [WinError 5]` / `FileNotFoundError [WinError 3]`——是 Windows 临时目录/AV 权限问题，**不是代码问题**。CI/Linux 无需这些参数。

当前基线：**733 passed / 13 skipped / 0 failed**；`ruff` 全过；前端 `npm run build`（tsc + vite）通过。
Alembic 单一 head：`20260813_0023`。

---

## 已完成项

### ① 一键启动
- compose 增加前端服务，单一入口 URL 即可拉起全栈（`cfc34b5`, `b30cca7`）。
- 落地页按 `/setup/status` 状态门控，未就绪不放行创建项目。

### ③ 组织模型（一等公民）
提交：`3ad4167`（后端）、`a58d69c`（前端）
- 新表 `platform.organizations`（`OrganizationRecord`）+ CRUD 端点 `/api/v1/organizations`；`repository_onboarding_jobs.organization_id` 外键指向它（迁移 `20260813_0022`）。
- 启动向导 STEP 03 从「手填 UUID」改为「组织名称 + 代码平台（github/gitlab）+ Agent 名称」，创建组织后再建 Organization Leader。

### ④ 异步接入 + 中断恢复（方案 A）
提交：`884442b`（异步 job 基础）、`b57548b`（恢复+去重，后端）、`0046c3c`（前端）
- 仓库接入走后台 job（`RepositoryOnboardingJobRecord`），前端轮询进度。
- **重启恢复（方案 A：标记中断供手动重试，不自动重跑）**：`recover_interrupted_onboarding_jobs()` 在 lifespan 启动时把遗留在 `queued/running` 的 job 置为 `interrupted`（私有仓库 token 不落库，故不自动重跑）。
- **并发去重**：同组织已有进行中 job 时，再次创建返回 **409**。
- 前端把 `interrupted` 也当作可重试态展示。

### ⑦ 团队持久化（方案 A：RepoMesh 作为记录系统）
提交：`cf8542f`（后端）、`1f1a755`（前端）
- **背景缺口**：AgentTeams 控制平面是团队的运行时权威，但 RepoMesh 此前不记录自己请求装配的团队——操作员起的名称/描述、成员快照、幂等键只活在 Go 运行时，重启后只能从 agent 目录「猜」并合成显示名。
- 新表 `agent_directory.agent_teams`（`AgentTeamRecord`，迁移 `20260813_0023`）：`id / organization_id / name / description / leader_agent_id / member_agent_ids(JSON) / agentteams_team_name(unique) / repository_id / idempotency_key / created_at / updated_at`。
- `persist_agent_team()`：**幂等 upsert，键为唯一的 `agentteams_team_name`**——接入重试与团队复用会刷新成员快照而非产生重复行。
- 两条建团路径成功后都落库：`create_manual_agent_team`（手动组队）与 `onboard_repository_agent_team`（仓库接入）。
- 新增 `GET /api/v1/agent-teams`（需本地认证）。
- 前端 Teams 看板改读真实团队（真实名称/负责人/成员数）；仅在旧组织无记录时回退到 agent 目录重建；手动建团后从服务端刷新。
- 测试：落库+列出、幂等不重复、未认证 401（`tests/test_agent_team_persistence.py`）。

---

## 待开工项

### ⑤ Runner 检测（已梳理，待确认后开工）

**现状/缺口 G5：平台对执行面是盲区。**
- Runner 是独立进程（`repomesh_runner`），轮询 `GET /runtime/runner-tasks/next`（`runner_control_token` 鉴权）领任务、回传 `POST /runtime/runner-events`；任务/事件落 `agent_runtime.runner_dispatches` / `runner_events`。
- 向导的 `/setup/coding-agents` 探测的是 **API 容器自己**的本地 CLI，代码自注 *"Runner containers must expose their own probe before remote execution."*——对「任务实际在哪执行」是误导。
- `/setup/status` 没有任何 runner/执行能力检查：**可在零 runner 在线时完成配置并创建项目**，任务会永远堆在 `runner_dispatches` 无人 lease。
- 约束事实：stock runner 轮询用配置好的完整 URL，**不带 `workerAgentId`**，服务端唯一能拿到的信号是「持有 control token 的某 runner 在 T 时刻轮询过」——无稳定身份、无能力上报。

**方案 C（被动心跳，纯服务端，推荐）**
- 每次通过鉴权的 `next_runner_task` 轮询即心跳，记录 `last_seen`（有 `workerAgentId` 按 worker 记，否则记全局执行面）。
- 新表 `agent_runtime.runner_heartbeats`；新增 `GET /runtime/runners`（管理员）；`/setup/status` 增加 `runner` 检查＝「N 秒内有心跳」；向导加「执行运行器」面板。
- 零 runner 改动，延续最小 diff 风格。代价：身份弱、无 adapter 能力。
- 建议 `runner` 设为**非必需检查**（类比 `github_app`），只提示不阻断。
- 表放 `agent_runtime` schema（与 `runner_dispatches`/`runner_events` 同域）。

**方案 B（主动注册，runner + 服务端）**
- 新增 `POST /runtime/runners/heartbeat`，runner 自报 `runner_id` + adapters/版本 + workspace root，落 `runner_registry` 表。
- 能给真实在线数与能力，可**替换**误导的 API-local 探测。代价大（要改 `repomesh_runner` + 加 runner 端配置 + 改向导）。

**建议**：先做方案 C 关掉「有没有 runner」这个最致命盲区，B 的能力上报留作后续增强。
**下次开工的检查点**：确认 方案 C（+ `runner` 非必需检查、`agent_runtime` schema）即可动手；或直接上 B。

### ⑧ 项目路由（未开始）
项目/任务如何路由到具体仓库团队与 runner——待梳理。

### ⑥ SCM 凭证（未开始）
私有仓库接入所需 GitHub/GitLab 凭证的安全存储与注入——待梳理。注意 `.secrets/*.pem` 等严禁入库。

---

## 提交栈（本分支 ahead of origin）

```
1f1a755 feat(web): show real persisted teams on the Teams board        # ⑦ 前端
cf8542f feat(teams): persist composed AgentTeams teams as system of record # ⑦ 后端
0046c3c feat(web): surface interrupted onboarding jobs as retryable      # ④ 前端
b57548b feat(setup): recover interrupted onboarding jobs on startup      # ④ 后端
a58d69c feat(web): create real organizations in setup wizard            # ③ 前端
3ad4167 feat(organization): make organizations a first-class entity      # ③ 后端
b30cca7 feat(platform): unify one-click startup behind a single entry URL # ①
617378c feat(web): status-gated landing, async onboarding UX, ...        # 落地页/UX
101c834 feat(project): add project listing query API
884442b feat(setup): asynchronous repository onboarding jobs             # ④ 基础
cfc34b5 feat(deploy): serve web frontend via compose for one-click startup # ①
```

## 关键文件索引
- 组织：`src/repomesh/api/organizations.py`
- 异步接入 + 恢复：`src/repomesh/api/platform_setup.py`、`src/repomesh/bootstrap/app.py`（lifespan）
- 团队持久化：`src/repomesh/api/agent_teams.py`、建团落库点在 `src/repomesh/api/human_control.py`
- Runner 执行面：`src/repomesh/modules/agent_runtime/api/router.py`、`.../runner_store.py`、`src/repomesh/integrations/runner/gateway.py`
- 前端：`web/src/api.ts`、`web/src/SetupWizard.tsx`、`web/src/TeamSetup.tsx`
- 迁移：`migrations/versions/20260813_00{21,22,23}_*.py`
