# 本地 CLI 一键拉起与开工门禁 — 施工 Spec（最简路径版）

> 日期：2026-08-29
> 状态：施工草案 v2（按"最简路径"重写，替代同日 v1 的六 PR 方案）
> 需求基线：`docs/development/local-cli-launch-readiness-requirement-20260829.md`（同目录）
> 代码基线：`codex/v2-stage-fixes` 头 `db55bf29`；施工分支：本 worktree `codex/local-cli-readiness`（先 rebase）
> 交付形态：**两个 PR + 一轮活体验收**

---

## 0. 最简路径的三刀

1. **租约不落库**。readiness 是 45 秒短租约，天生易失：后端存一个带锁的内存 dict 即可。后端重启 → 租约全失 → 门禁 fail-closed 最多 15 秒 → 各 Bridge 下一次续租自动补齐。**省掉**：新迁移、PG adapter、双 adapter 参数化测试。单进程 uvicorn 部署下无正确性损失。
2. **预检就是门禁本身**。不做独立的"仅重新检查"端点族：一个薄读端点直接复用门禁用例；Materialize 的 409 响应体本身携带完整 blocking 清单，页面拿它渲染。**省掉**：第二套成员集合推导、第二份响应形状。
3. **Launcher 是个壳**。一个小 FastAPI 应用（目标 ~250 行），四个固定路由，启停直接 shell 到已被活体验证的 `start-local-cli.ps1` / `stop_members.ps1`，状态 = 读 PID 文件 + 命令行特征复核。**省掉**：安装器、七态状态机（前端从成员行现算）、独立进程管理实现。

砍掉但**不是**放弃正确性的地方，逐条见 §5。

---

## 1. 基线事实（已核实）

| 需求涉及面 | 现状 | 锚点 |
|---|---|---|
| "本地 CLI" 页面 | 只复制命令，不感知结果 | `frontend/src/pages/LocalCliPage.tsx` |
| readiness 端点 | 不存在（只有 binding GET v1/v2 与 operator PUT） | `agent_runtime/api/router.py:139,370` |
| Bridge 启动门 | 六步顺序结构化存在，末尾只打 `bridge ready` 日志 | `repomesh_agent_bridge/application.py:216-327` |
| Materialize | `_runtime.project()` 之后直接 `_materializer.materialize()`，无成员检查 | `discovery_materialization.py:226,232` |
| /runtime 鉴权 | `_authorize_runner`：control token→`None`，成员 token→agent id | `router.py:205` |
| Bridge 已有的地址与凭据 | enrollment 含 `repomeshEndpoint` + `repomesh` credential slot | `repomesh_agent_bridge/contracts.py` |
| 拓扑成员 | `repository_teams[].leader_agent_id + worker_agent_ids` | `project/contracts.py:130-144` |
| containerManaged | controller 文档字段，经 `WorkerBindingReader` port 可读 | `agent_runtime/ports/agent_team.py` |

要防的事故（波次 3 交接 §3.2 实证）：计划停驻通知幂等不重发、Bridge 无 backfill——materialize 早于 Bridge = 通知永久错过，假开工。

---

## 2. 架构（不变的部分）

```text
Console ──固定操作(loopback+自定义头)──▶ Local Launcher ──ps1──▶ Bridge ×N
   │                                                            │
   │        六步启动门全过 → 首报 ready → 15s 续租 → 退出下线      │
   ▼                                                            ▼
RepoMesh ◀──POST readiness(成员自己的 token)─────────────────────┘
   ├─ 内存租约表（唯一就绪真相；45s 过期即 offline）
   ├─ GET readiness → 页面逐成员状态
   └─ materialize: require_ready(计划成员) 全绿放行 / 409 fail-closed
```

真相源分层保持需求原样：**就绪真相只有租约**；Launcher 只报进程事实；门禁不看 Launcher、不看 PID——所以"Launcher 不可用、手动 PowerShell 启动后自动变绿"（AC-07）免费成立。

---

## 3. 实现清单

### 3.1 后端：租约 + 端点 + 门禁（PR 1 的服务端半）

**租约存储**：`agent_runtime/application/readiness.py` 一个模块装下用例与内存存储。

- 行：`{member_agent_id: (instance_id, role, leader_lane, governed_lane, workspace_root, reported_at, expires_at, stopped_at)}`，`asyncio.Lock` 保护。
- TTL 45s / 建议续租 15s：settings 两个常量，响应体回传 `renewAfterSeconds`，Bridge 不硬编码。
- 状态读取时推导：`ready`（未过期）/ `stale`（过期≤1×TTL）/ `offline`（更久或已下线）。门禁只认 `ready`。
- 围栏（AC-05 第三条）：报文带 `instanceId` + `kind: startup|renew|shutdown`。`startup` 总是接管；`renew`/`shutdown` 的 instanceId ≠ 行内值 → 409 `stale_instance`。就这一条规则，不再多。

**端点**（`agent_runtime/api/router.py` 追加）：

```text
POST /api/v1/runtime/v1/external-members/{agentId}/readiness   # Bridge 写
GET  /api/v1/runtime/v1/external-members/readiness             # Console 读，全量
```

- 写端：`_authorize_runner` 同一凭据体系，但**必须是成员 token**（control token 403：无主体的凭据不能替人自述）；token 派生 id ≠ 路径 id → 403。
- 写端校验（任一失败 409 不落行）：目录角色 == 上报角色；leader ⇒ `leader_lane ∧ ¬governed_lane ∧ workspace_root=null`；worker ⇒ `governed_lane ∧ workspace_root == REPOMESH_RUNNER_WORKSPACE_ROOT`；`organization_leader` ⇒ 409。这组校验就是 AC-04 的服务端半。
- 读端：既有读模型鉴权（`REPOMESH_AGENT_ACTION_TOKEN`）；响应不含 token、不含宿主路径以外信息。

**门禁**：

- port：`repository_intelligence/ports.py` 加 `ExternalMemberReadinessGate.require_ready(member_ids) -> tuple[BlockingMember, ...]`（空 = 放行）。
- 实现：`agent_runtime/application/readiness.py` 的 `RequireExternalMembersReady`——逐成员：目录取 principal → `WorkerBindingReader` 取 controller 文档 → `containerManaged:true` 跳过（managed 走既有 runtime readiness，AC-04 第三条）→ external 查租约，非 `ready` 即 blocking。`bootstrap/container.py` 装配成 repository_intelligence 的 port；模块间只见协议不见存储（需求 §8.3）。
- 调用点：`discovery_materialization.py` 在 `_runtime.project()` 之后、`_materializer.materialize()` 之前。成员集合 = 本轮 `repositories` 对应 teams 的 leader + workers（Organization Leader 不在内）。
- 失败：新异常 `ExternalMembersNotReady` → `api/discovery_chain.py` 加一个分支译为 409，detail 用需求 FR-06 建议形状（`code: external_members_not_ready` + `members[]`）；同时复用 `_record_failure` 形态写 `status:"blocked"` 回执（审计 FR-10；失败回执不 replay 不烧 key，所以"补齐后同 key 重试、不重复建 Team"走既有机制免费成立，AC-03 后半）。
- 预检：`GET /api/v1/issues/{issue_id}/discovery/readiness`，同一 port 只读调用，返回同一 `members[]` 形状 + `checkedAt`。约 20 行。

### 3.2 Bridge：首报、续租、下线（PR 1 的客户端半）

- `contracts.py` 追加 `repomesh.agent-bridge.readiness.v1` 常量与报文模型。冻结契约（enrollment/binding v1/v2、leader-actions v1）**零修改**——新文档族。
- `ports.py` 加 `ReadinessReporter`；HTTP 实现放 `adapters/repomesh_binding.py` 旁（复用 `repomeshEndpoint` + `repomesh` token slot，**零新增配置**，老 enrollment 不用重生成）；`adapters/memory.py` 加测试实现。
- 挂点（`application.py` 的 `run()`）：
  1. **首报在 `bridge ready` 日志之前**（`_room_port.start()` 成功后）。失败 → `BridgeStartupError` 退出，不打 ready——FR-04 的"不得伪装"由代码位置保证。
  2. 续租循环加入 TaskGroup 作同伴任务：按 `renewAfterSeconds` 周期报 `renew`；单次失败记 warning 等下一周期（不重启不退出）；收到 `stale_instance` 409 → 本实例已被接管，退出。
  3. exit stack 注册 best-effort `shutdown` 报文；失败只记日志，正确性以过期为准（FR-08 后半）。

### 3.3 Launcher（PR 2）

`src/repomesh_local_launcher/`（与 bridge/runner 平级的小包，FastAPI，同 .venv）：

```text
GET  /v1/status        POST /v1/members/start
POST /v1/members/stop  POST /v1/members/{agentId}/restart
```

- 本机 config（gitignored，`output/local-launcher/config.json`）：roster/enrollment/env 路径 + `rosterVersion` + Origin 白名单。**请求体一律空对象**——FR-09 的"不接受命令/路径/凭据"是 schema 层不可能。
- 只绑 `127.0.0.1:8121`；写操作校验 Origin ∈ 白名单 + 自定义头 `X-RepoMesh-Launcher-Op: 1`（强制 CORS preflight）；响应永不含 env 值/token。
- start/restart/stop shell 到既有 `start-local-cli.ps1` / `stop_members.ps1`（不复制进程管理逻辑）；start 前与 stop 前逐成员做 **PID 存活 + 命令行特征复核**（`-m repomesh_agent_bridge` + enrollment 路径），已在跑不双开（AC-01 第三条）、不符不杀（FR-08）。
- `/v1/status` 只返回逐成员进程事实（running/stopped、pid、日志路径）；`ready/degraded` 之类聚合态由前端把它与 readiness 读端合并现算。
- 入口：`scripts/start-local-launcher.ps1` 手动起，不做安装器。

### 3.4 Console（PR 2）

- `frontend/src/api/`：launcher client（独立 base，探测失败 → `launcher_unavailable`）+ readiness 读端与 409 detail 类型入 `contract.ts`。
- `pages/LocalCliPage.tsx`：探测 → 主按钮"启动并检查本地 CLI" → 逐成员表（进程态 + readiness 态 + 失败阶段 + 日志路径两列并排，允许不一致）→ 停止全部 / 单成员重启 → 探测失败降级回现有命令卡片（保留现组件）。
- `components/MaterializeModal.tsx`：打开时调预检端点渲染就绪清单；有 blocking 成员时列名并给"启动并重新检查"/"仅重新检查"两键；全绿显示检查时间后放开确认。服务端执行时仍再过门禁，页面预检不承担正确性（FR-07 末条由 3.1 保证）。

---

## 4. 交付计划

### PR 1 — 就绪真相链（后端 + Bridge，纯代码可测，无活体依赖）

3.1 + 3.2 全部。测试（既有形态，in-process）：

- 租约规则：首报/续租/过期推导/围栏（内存存储直测，四组）。
- 端点矩阵：401/403（他人 token、control token）/404/409（角色能力四种：leader 带 governed、worker 无 governed、workspace 不符、org leader）。
- 门禁：全停 409 零任务零通知（AC-02）；5/6 拒且点名（AC-03）；managed 不误伤（AC-04）；过期拒（AC-05）；补齐后同 key 重试成功不重复建（AC-03 后半）；预检无副作用。
- Bridge（memory adapter）：首报失败不打 ready；续租失败等下一周期；`stale_instance` 退出。
- 回归：既有 materialize 测试全绿。

### PR 2 — 操作面（Launcher + Console）

3.3 + 3.4 全部。测试：

- Launcher：路由行为 + 安全三件（非白名单 Origin 拒、缺自定义头拒、响应无 env/token 断言）+ 进程身份复核单测（伪 PID + 不符命令行 → 拒杀）。ps1 调用层薄到活体验证即可。
- 前端：`tsc -b`（`--noEmit` 是空转桩不可用）+ oxlint 受影响文件 + 浏览器实走。

### 活体验收（一轮，非 PR）

照波次 3 交接 §7.6 配方重建环境（服务端 Matrix 身份 ≠ 任何 Bridge 成员；一次性库；不碰 5432），一轮跑完：

1. Launcher 一键拉起 → 逐成员转绿 → 全 ready（AC-01）；
2. 全停时 materialize 409 点名（AC-02）；杀 1 个 → 页面转 offline → 5/6 拒（AC-03/05）→ 单成员重启 → 免刷新重检 → 开工（AC-03/08）；
3. **顺带收账**：D-M7-1 修复（`cac5d1e2`）的活体取证——Leader→Manager 汇总落进 manager 房间。同一套环境不跑两遍。

证据落 gitignored 的 `output/`，判定文档入 `docs/development/`。

---

## 5. 砍掉了什么、为什么安全

| 砍掉 | 为什么安全 |
|---|---|
| 租约 PG 持久化 + 迁移 | 45s 短租约天生易失；后端重启后门禁 fail-closed ≤15s 即自愈。单进程部署无损失。若将来多进程部署，换存储是 port 后面的事 |
| 独立"重新检查"端点族 | 预检读端 + 409 detail 已覆盖 FR-07 的全部展示需要 |
| Launcher 七态状态机 | 前端从成员行现算聚合态；`launcher_unavailable` 是探测失败的本地结论，本来就不在服务端 |
| Launcher 独立进程管理 | ps1 已被活体验证（隐藏窗口、env 装载、PID 落盘）；launcher 只做安全壳与幂等检查 |
| 安装器（待裁决 3） | 首期 `scripts/start-local-launcher.ps1` 手动起 + 页面降级提示 |
| readiness 通用化（待裁决 6） | 首期只作用于 `containerManaged:false`；报文无 Codex 特有字段，可扩不先扩 |

**没砍的**（正确性骨架）：首报阻塞启动、续租由服务端牵引、状态读取时推导、实例围栏一条规则、写端角色能力校验、门禁 fail-closed + 结构化 409 + blocked 回执、Launcher 零输入 + Origin/自定义头、进程身份复核。

其余待裁决沿用建议：租约 15s/45s；端口 8121、Origin 默认 `http://127.0.0.1:5281`；门禁在 projection 后任务前、幂等空拓扑保留；新环境用**既有** provision 面（`PUT /runtime/v2/external-members` + `ProvisionTeamModal`）解循环依赖，只在文档写死顺序：provision → Bridge → materialize。

---

## 6. 红线

- `src/repomesh_runner/**` 零改动；冻结契约文档族零修改（readiness 是新增族）。
- token/THINKING/协议帧/私有绝对路径不入 tracked 文件、不入响应、不入回执。
- 秘密全走 gitignored 的 `output/` 与 env；launcher config 亦 gitignored。
- 提交无 Co-Authored-By；验证只做针对性验证（受影响模块测试 + materialize 回归；前端 `tsc -b` + 实走）。

## 7. 验收对照

| AC | 证明位置 |
|---|---|
| AC-01 一键/幂等 | PR 2 单测 + 活体 1 |
| AC-02 全停 409 | PR 1 单测 + 活体 2 |
| AC-03 5/6 拒 + 免刷新重试 | PR 1 单测 + 活体 2 |
| AC-04 角色能力/managed | PR 1 单测 |
| AC-05 租约失效/围栏 | PR 1 单测 + 活体 2（杀进程） |
| AC-06 失败诚实展示 | PR 1（阶段脱敏）+ PR 2（页面） |
| AC-07 降级可恢复 | PR 2 + 活体（手动启动变绿） |
| AC-08 端到端顺序 | 活体全程（并带走 D-M7-1 取证） |
