# 测试团队 v1 交接（2026-09-01，W4 活体进行中）

分支 `feat/module-test-team-v1`（头 `511fb080`，自 `codex/v2-stage-fixes@18cf6169` 切出，**未推 GitHub**）。
资产仓 `catbobyman/repomesh-test-assets` main 头 `955f652`（已推）。
本文件是会话恢复入口：先按 §1 的顺序读文件，再照 §3 对账环境，然后从 §7 的第一步继续。

---

## 0. 一句话状态

W1/W2/W0b/W3 完成；W0a 实测 **P-1 FAIL** 已按改道条款落地（runner 轨 + 取 A + Bridge 轨）；
W4 活体走到 **B1 闭环的最后一格**：AC-D1/D2/D3、S-1 活体、B3 路由、B1「跑完且证据落盘」都过了，
**证据进不了提交/候选分支**——阻断点需要用户裁决（§6），载荷与栈都留着，裁决后照 §7 继续。

---

## 1. 必读文件（按顺序）

| # | 文件 | 读什么 |
|---|---|---|
| 1 | `CONTEXT.md` | 术语表；**判据 ≠ 验收标准** |
| 2 | `docs/development/module-test-team/module-test-team-spec-20260901.md` | 冻结 spec；**修订 A（A.0–A.5）是改道后的现行条文**，A.2 退出码约定已按「取 A」改版 |
| 3 | `docs/development/module-test-team/module-test-team-plan-20260901.md` | 五波计划 + 改道条款两次生效记录 |
| 4 | `docs/development/module-test-team/module-test-team-acceptance-criteria-20260901.md` | 验收标准；**执行记录表**与「改道修订」「W4 实走中的发现」三节是当前真相 |
| 5 | 本文件 §3–§7 | 环境、编制、阻断、下一步 |
| 6 | `docs/development/room-native-bridge-handoff-20260829-wave3-m7.md` §7.6 | Bridge 轨环境重建配方（本栈就是照它起的，差异见 §3.4） |
| 7 | `capabilities/skills/integration-run/SKILL.md`、`capabilities/skills/cross-repo-test/SKILL.md` | 改道后的技能文档（A.3） |
| 8 | 资产仓 `environments/e2e-fixture-joint/{environment.md,runner-task-template.md,run_round.py}`、`scenarios/multi-currency-joint/{scenario.md,combinations.md,steps.json,combinations/*.json}` | 配方与判据的机器形态 |

**不要读** `docs/development/test-team-tiered-route-spec-20260831.md`（另一条线，对读另行安排）。
设计稿 `module-test-team-topology-draft-20260901.md` 只在要设计理由时查（§5.2 有改道留痕）。

---

## 2. 进度总表

| 波次 | 状态 | 落点 |
|---|---|---|
| W0a P-1 | **FAIL**，改道生效 | spec A.0；AC 表 P-1 行；实测法可复用 `scripts/module-test-team/p1_probe.py` |
| W0b 建仓注册 | 完成 | 资产仓已建；W4 栈里由 seed 脚本注册 |
| W1 平台十行 + A 组 | 完成 | `06efc040`；A 组双档绿（见 AC 表） |
| W2 控制台三处 | 完成 | `1911bdb2`；D4 静态半场绿 |
| W3 配方 + 技能 7 条 | 完成并两次改版 | 平台 `6a0e6738`/`d4e64ace`/`390a57b0`；资产仓 `2058b19`→`955f652` |
| W4 活体 | **进行中，卡 B1 最后一格** | 本文件 §5–§7 |

AC 逐条：A1–A6 PASS（自动化）；D1–D4 PASS（D1–D3 活体）；B3 路由 PASS；B1 到证据落盘（入仓未达）；B2/C1/C2 未跑（载荷已备）。

---

## 3. 环境实况（写本文件时全部活着）

### 3.1 进程与端口

| 组件 | 位置 | 备注 |
|---|---|---|
| W4 后端 uvicorn | `127.0.0.1:8077` | 由 `output/bridge-team/w4-live/start-backend.sh` 起；日志 `output/bridge-team/w4-live/logs/backend.log`；**当前是本会话的后台任务，会话结束即退出，重启照 §3.3** |
| W4 前端 vite | `127.0.0.1:5281` | `.claude/launch.json` 的 `m8-frontend`（`REPOMESH_API_TARGET=8077`，`VITE_API_TOKEN=m8-console-token`）；由浏览器工具起，会话结束即退出 |
| 一次性 postgres | `127.0.0.1:15547` | 容器 `repomesh-w4-pg`（`--rm`，**停即消失，库不可恢复**）；已 `alembic upgrade head`=`20260829_0041` |
| controller forwarder | `127.0.0.1:18090 → agentteams-controller:8090` | 容器 `repomesh-controller-forwarder`（`docker start` 即可复用） |
| AgentTeams 控制器 | `agentteams-controller`（宿主单例，`agentteams-embedded:v1.2.0-rm3`） | Matrix 客户端入口 `127.0.0.1:18080`；**18080 的 Higress 只放 GET，POST 要从 agentteams-net 网内打 8090 或走 18090** |
| Bridge worker | pid 见 `output/bridge-team/w4-live/pids/test-worker.pid`（写时 20144） | 成员 `repomesh-test-worker`，profile codex，日志 `output/bridge-team/w4-live/logs/test-worker.err.log`（`bridge ready … rooms=2 governed=on`） |
| runner 工作区根 | `D:/Project4work/.repomesh-w4-live/workspaces` | 已 `icacls … /grant <user>:(OI)(CI)F`；四个 run 的 worktree 在 `w/<run>/<repo>` |
| W0b 的 dev 栈（与 W4 无关） | 后端 `8100` + `repomesh-dev-pg@5433` | `dev-up.sh` 起的，catalog 里只注册了资产仓（未贴档）；可 `scripts/dev-down.sh` 收 |
| 他线勿动 | `5280`/`8000`/`5432`/`55432`/`3000`/`8080` | 见记忆 `repomesh-local-env-recipe` |

### 3.2 凭据（全部在 gitignored 的 `output/bridge-team/w4-live/secrets/`）

| 文件 | 内容 | 有效期 |
|---|---|---|
| `controller-token.txt` | 控制器 API token（`/run/agentteams/cli-token`） | **控制器每次重启轮转**，重取：`docker exec agentteams-controller sh -c 'cat "$AGENTTEAMS_AUTH_TOKEN_FILE"'` |
| `appservice-as-token.txt` | Matrix appservice as_token（铸任何成员 Matrix token 的钥匙） | 随控制器数据卷，长期 |
| `admin-matrix-token.txt` | 服务端 Matrix 发信身份 `@admin`（≠ 任何 Bridge 成员） | M7 留下的，写时仍 200 |
| `runner-worker-tokens.json` | `REPOMESH_RUNNER_WORKER_TOKENS` 的值（三个 agent id → 随机 token） | 随后端 env |
| `admin-session-token.txt` | 本地 admin 会话（`POST /api/v1/auth/login` 的 `access_token`） | 后端重启需重登 |
| `../w4-members.env` | `E1_TEST_WORKER_MATRIX_TOKEN` / `E1_TEST_WORKER_REPOMESH_TOKEN`（Bridge 进程 env） | Matrix 用 appservice login 铸，长期 |
| GitHub token | `output/bridge-team/secrets/e0b/github-token.txt`（E0b 留下） | 写时对资产仓 `push=True`，delivery 用它 |
| 本地 admin | `w4admin` / `W4admin-2026!`（`scripts/module-test-team/w4_seed.py` 默认值，一次性栈专用） | — |
| LLM | `.env` 的 `REPOMESH_MODEL_API_KEY`（后端继承 `.env`） | — |

### 3.3 后端 env（`start-backend.sh` 全文即配置）

```text
REPOMESH_DATABASE_URL=postgresql+asyncpg://postgres:e2e@127.0.0.1:15547/postgres
REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://127.0.0.1:18090   (+ _TOKEN 从 secrets 读)
REPOMESH_AGENTTEAMS_MATRIX_URL=http://127.0.0.1:18080       (+ _ACCESS_TOKEN=@admin)
REPOMESH_RUNNER_CONTROL_TOKEN=w4-runner-control-token
REPOMESH_RUNNER_WORKER_TOKENS=<runner-worker-tokens.json>
REPOMESH_AGENT_ACTION_TOKEN=m8-console-token
REPOMESH_RUNNER_WORKSPACE_ROOT=D:/Project4work/.repomesh-w4-live/workspaces
REPOMESH_DELIVERY_GITHUB_TOKEN=<e0b token>
REPOMESH_DELIVERY_AUTO_ENABLED=true
REPOMESH_DELIVERY_REQUIRED_CHECKS=["evidence-review"]      # 必须是 JSON 列表；检查名故意不存在，PR 只开不合
REPOMESH_DELIVERY_REQUIRED_APPROVALS=1
REPOMESH_DELIVERY_BASE_BRANCH=main
MSYS_NO_PATHCONV=1
```

重启后端：`bash output/bridge-team/w4-live/start-backend.sh >> output/bridge-team/w4-live/logs/backend.log 2>&1 &`
（先确认 8077 空：`netstat -ano | grep :8077`，残留就 `taskkill //PID <pid> //F`）。
重启后要重新 `POST /api/v1/auth/login` 拿 admin 会话；Bridge 会自动重连。

### 3.4 从零重建（栈散了照这个；与 §7.6 的差异已标）

1. `docker run --rm -d --name repomesh-w4-pg -e POSTGRES_PASSWORD=e2e -p 127.0.0.1:15547:5432 postgres:17-alpine`；`REPOMESH_DATABASE_URL=… alembic upgrade head`。
2. `docker start repomesh-controller-forwarder`（不存在则照 §7.6 步骤 1 `docker run … socat`）。
3. 重取 controller token 进 secrets；其余 secrets 文件可复用。
4. **seed**：`MSYS_NO_PATHCONV=1 REPOMESH_DATABASE_URL=… REPOMESH_RUNNER_WORKSPACE_ROOT=… .venv/Scripts/python.exe scripts/module-test-team/w4_seed.py`（admin + 两仓 + 五 principal，**不种拓扑**；幂等）。
5. 起后端（§3.3）→ `POST /auth/login` 存 `admin-session-token.txt`。
6. **provision 测试 worker 为外部成员**（必须先于 materialize）：
   `E1_REPOMESH_ADMIN_TOKEN=<admin token> python scripts/bridge-e1/provision_members.py --members output/bridge-team/w4-live/members.w4.json --out output/bridge-team/w4-live/bindings --subset w4 --stage provision`
7. AC-D1：前端 5281 登录 → 仓库页 → 资产仓「团队档案」→ `cross-repo-test-team`（或 `PATCH /api/v1/repositories/55555555-0000-4000-8000-000000000005/capability-profile`）。
8. 发现链 + materialize：`PYTHONIOENCODING=utf-8 MSYS_NO_PATHCONV=1 W4_RUN=<tag> .venv/Scripts/python.exe scripts/module-test-team/w4_chain.py`（真 LLM；materialize 首次多半 503「rooms pending」，脚本会重试）。
9. binding（成员须已属 Team）：同 6 的命令改 `--stage binding` 并加 `E1_TEST_WORKER_REPOMESH_TOKEN=<runner-worker-tokens.json 里 …00e1 的值>`。
10. enrollment + Matrix token + codex auth + 起 Bridge：
    ```bash
    python scripts/bridge-e1/make_enrollments.py --members …/members.w4.json --bindings …/bindings --out …/enrollments --subset w4
    E1_CONTROLLER_TOKEN=… E1_APPSERVICE_TOKEN=… python scripts/bridge-e1/fetch_matrix_tokens.py --members …/members.w4.json --out …/w4-members.env --subset w4
    printf 'E1_TEST_WORKER_REPOMESH_TOKEN=%s\n' "<token>" >> …/w4-members.env
    powershell -File scripts/bridge-e1/copy_codex_auth.ps1 -Members …/members.w4.json -Subset w4 -SourceCodexHome "$LOCALAPPDATA\repomesh-agent-bridge\sessions\4d1e6f00-0000-4000-8000-000000000004\codex-home"
    MSYS_NO_PATHCONV=1 icacls "D:\Project4work\.repomesh-w4-live\workspaces" /grant "%USERNAME%:(OI)(CI)F" /T
    powershell -File scripts/bridge-e1/start_members.ps1 -Members …/members.w4.json -EnrollmentDir …/enrollments -EnvFile …/w4-members.env -PidDir …/pids -LogDir …/logs -Subset w4   # 会把调用它的 Bash 挂住，但进程已起；看日志 bridge ready
    ```
11. 派联调轮：`curl -X POST http://127.0.0.1:8077/api/v1/bridge/materialize -H "Authorization: Bearer m8-console-token" -H "Content-Type: application/json" --data-binary @scripts/module-test-team/payloads/<b1_green|b2_red|c1_blocked>.json`——**每次换 `idempotency_prefix`**（同前缀=重放）。

---

## 4. 编制与身份（W4 库里的事实）

| 角色 | agent id | resource name | 形态 |
|---|---|---|---|
| 组织 `11111111-…-0001` 的 Manager | `22222222-0000-4000-8000-000000000002` | `repomesh-preflight-manager` | 控制器已有 manager 资源 |
| 业务仓 `pricing-fixture`（`42cf099f-fadc-4222-95ab-bbd4770f7fdc`，本地路径 `D:/Project4work/.repomesh-v1-live/fixture-pricing`）队长 | `33333333-…-0003` | `repomesh-preflight-leader` | M7 外部成员，**未起 Bridge**（leader 任务停驻，无碍） |
| 业务 worker | `4d1e6f00-…-0004` | `repomesh-preflight-probe` | M7 外部成员，未起 |
| 测试资产仓 `repomesh-test-assets`（`55555555-0000-4000-8000-000000000005`，`test_commands=[python environments/e2e-fixture-joint/run_round.py]`，`test_paths=[evidence/**]`，档案 `cross-repo-test-team`）队长 | `66666666-0000-4000-8000-000000000006` | `repomesh-test-leader` | **容器 copaw**（控制器建，技能=覆盖表三项，server 拆解） |
| 测试 worker | `4d1e6f00-0000-4000-8000-0000000000e1` | `repomesh-test-worker` | **Bridge 外部成员**（codex-home 按 D-10 复制自 …0004） |

拓扑（项目 = issue `21899c3e-989f-5173-b6b7-838c6ca8a492`）：业务团队 `repomesh-preflight-team`（mode leader）；
测试团队 `repomesh-team-55555555000040008000000000000005`（mode server），team room `!sY1lFBp4hiErPGFPd6:matrix-local.agentteams.io:18080`，
leader room `!n53KhR8E5xhvolJO1F:…`，worker DM `!iBuL2hIZ11UY2ptZYE:…`。

已派的联调轮（全部 `/bridge/materialize`，项目同上）：

| 前缀 | plan | worker task | run | 结果 |
|---|---|---|---|---|
| `w4-b1-green` | `70794443` | `d6758df1` | `f375610a` | FAIL：工作区 Low 标签失败（ACL） |
| `w4-b1-green-2` | `1a06be85` | `78d545d3` | `7eaef69e` | succeeded；证据 `itest-20260901202856-26d2ea`；0 changed（`.gitignore` 误忽略） |
| `w4-b1-green-3` | `9fda8693` | `4894faf2` | `1c1c6975` | succeeded；证据 `itest-20260901203118-780e37`；0 changed（收集顺序） |
| `w4-b1-green-4` | — | `582d8505` | `461699d9` | succeeded；证据 `itest-t582d850506d2` `overall=PASS`；0 changed（**阻断点**） |

---

## 5. 已落地的改动（本线提交）

平台仓（`feat/module-test-team-v1`）：`61153fd8` 文档四件套+CONTEXT → `06efc040` S-1+A 组双档 → `1911bdb2` S-2 三处 → `6a0e6738` 技能 7 条 → `d4e64ace` P-1 FAIL 留痕+改道 → `390a57b0` 取 A+Bridge 轨留痕 → `edd423f3` **修 dispatch 漏传 profile** → `511fb080` W4 记录。
本次交接顺带入库：`scripts/module-test-team/{w4_seed.py,w4_chain.py,p1_probe.py,mk_payloads.py,payloads/*.json}`。

资产仓：`2e473a8` 三目录 → `2058b19` W3 配方 → `97780ed` exit-0-on-completion → `7092ea6` 组合读任务上下文 → `e8bc07f` `.gitignore` 锚定 → `971dc1d` run-id 按任务派生+幂等回放 → `955f652` C1 组合。

---

## 6. 阻断点（待用户裁决）

**现象**：B1 第 3/4 轮 `succeeded`、工作区 `evidence/<run-id>/` 四节齐全，但 runner 回「0 file(s) changed」，无提交、无候选分支、无 PR。

**根因（两层，都已坐实）**：
1. runner `_collect_evidence`（`src/repomesh_runner/executor.py:295`）在 agent 阶段后、`test_commands` 前收集 changed_files；配方在 test 阶段产出的证据落盘晚于收集。
2. 想让 agent 阶段跑配方也不行：Bridge 给受限 codex 的 PATH **按设计只含 node/codex 目录**（`repomesh_agent_bridge/adapters/coding_session.py:462-511`，J-12），codex 执行 `python …run_round.py` 得到 "python is not recognized"（codex 会话 `…/sessions/4d1e6f00-…-00e1/codex-home/sessions/2026/09/01/rollout-…01a0602f-2ab9-….jsonl`）。

**选项**（AC 文档「W4 实走中的发现」第 5 条同款）：
- **R1（建议）**：执行器在 `test_commands` 之后再收集一次；建议只在 agent 阶段变更集为空时生效，且同样过 allowed/denied 校验；改 `executor.py` + 加 runner 单测 + spec A.2 留痕。
- R2：放宽 Bridge 受限 PATH 加 python/git——J-12 安全设计变更，不该本线顺手改。
- R3：证据不入 git——违背 S-3 冻结。

---

## 7. 下一步（裁决后照此顺序）

1. **实施裁决**（R1 为例）：改 `src/repomesh_runner/executor.py::_collect_evidence`；单测加在 `tests/runner/test_executor.py`（既有执行器用例族）；spec A.2 加一段留痕。**改执行器后必须重启 Bridge**（runner 执行器在 Bridge 进程内运行：`stop_members.ps1` 再 `start_members.ps1`，参数同 §3.4 第 10 步）；后端不运行执行器，仅当改了 `src/repomesh` 才需重启（§3.3）。
2. **重派 B1**：改 `payloads/b1_green.json` 的 `idempotency_prefix`（如 `w4-b1-green-5`）→ POST → 轮询 worker 任务（**用回执 `task_ids[1]`**，别按 UUID 排序取）→ 核：工作区 HEAD 出现 `repomesh: complete task …` 含 `evidence/<id>/`；团队房 `[done] N file(s) changed`；`git ls-remote` 资产仓出现 `refs/heads/repomesh/<plan8>/<repo8>`；GitHub 有 draft PR；回执 artifacts 指针可解引用。填 AC-B1。
3. **B2 红轮**：`payloads/b2_red.json`（组合 red.json，pricing-core@`3c72ca6…`）→ 期望 `overall=FAIL`、`joint-multi-currency` 摘录含 `199.99 != 200.0` 与三条 `src` 行、三 unit 步 PASS、证据入仓、判据未动。
4. **C1 阻塞轮**：`payloads/c1_blocked.json`（组合 `blocked-unknown-commit.json`）→ `overall=BLOCKED`、原因为 git 原话、证据入仓。
5. **C2 清扫**：在**下一轮的 worktree 根**（`w/<run>/<repo>`，每轮新建——所以要先起一轮取到路径，或改在 `REPOMESH_RUNNER_WORKSPACE_ROOT` 下伪造并把 sweep 的 workdir 指过去）伪造 `itest-stale-*`（mtime 做旧 >24h）与 `itest-fresh-*`；再派一轮，看 `round.md` §3 清扫输出「removing/keeping」。注意 `run_round.py` 的 sweep 目标是**仓库根**，不是工作区根的上层。
6. 填 AC 表、更新 spec/plan 留痕、提交；两稿对读另行安排。
7. 拆环境：`stop_members.ps1`（或 kill pid）→ 停后端 → `docker rm -f repomesh-w4-pg`（--rm 会自删）→ `docker stop repomesh-controller-forwarder` → 控制器里 `DELETE /api/v1/workers/repomesh-test-worker`、`repomesh-test-leader` 及 Team（可留作下次复用，M7 就复用了）。

---

## 8. 坑清单（本轮踩过的）

- `icacls` 的 `/grant` 在 Git Bash 会被转成 `D:/Git/grant`：**`MSYS_NO_PATHCONV=1`**，且 `%USERNAME%:(OI)(CI)F`。
- `start_members.ps1` 挂住调用它的 Bash（进程已起，看 pid/日志）。
- Windows 下 `python`（Anaconda）不认 `/d/...` 路径：脚本里用 `D:\...`，或用 `.venv/Scripts/python.exe` 配 `MSYS_NO_PATHCONV=1`。
- bash heredoc 未加引号时反引号会被当命令替换（载荷里的 `` `python …` `` 被掏空过）：写载荷用 `scripts/module-test-team/mk_payloads.py`。
- 发现链：`force_continue` 首轮不能带；`approval` 键常在，看 `approval.state`；`candidates` 是 `{items:[…]}`；`classification_evidence_version` 在投影顶层。
- `REPOMESH_DELIVERY_REQUIRED_CHECKS` 必须是 JSON 列表字符串。
- 房间流 `GET /rooms/{id}/stream` 的 JSON 偶有控制字符，`json.loads(..., strict=False)`。
- 控制器 `/docker/` 直通、`/api/v1/workers` POST：18080 只放 GET，从网内容器或 18090 打。
- 工作区 `.repomesh/workspace.json` 不存在于 worktree（元数据在别处），别用它找 run；用 Bridge 日志的 `accepted task <id> as run <run>`。

---

## 9. 记忆入口

`~/.claude/projects/D--Project4work-GOAI-infra-repomesh/memory/test-team-line-state.md`（索引行在 `MEMORY.md`）。
