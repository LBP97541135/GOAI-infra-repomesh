# 测试团队 v1 交接（2026-09-01，W4 活体 B/C 组已走完）

分支 `feat/module-test-team-v1`（自 `codex/v2-stage-fixes@18cf6169` 切出，**未推 GitHub**；头以 `git log` 为准）。
资产仓 `catbobyman/repomesh-test-assets` main 头 `955f652`（未动）；四条候选分支 `repomesh/<plan8>/55555555` 与 PR #1–#4 由平台交付开出（见 §4）。
本文件是会话恢复入口：先按 §1 的顺序读文件，再照 §3 对账环境，然后从 §7 继续。

---

## 0. 一句话状态

W1/W2/W0b/W3 完成；W0a 实测 **P-1 FAIL** 已改道（runner 轨 + 取 A + Bridge 轨）；W4 活体 **A/B/C/D 四组全部有名字的结果**：
A1–A6 PASS、D1–D4 PASS、B1/B2/B3/C1/C2 PASS——其中 B2/C1「轮次结论写进任务回执摘要」子句**未达**（Bridge 形态的结构性缺口，
spec A.5 已知局限 + AC 发现 8，未修）。前一会话的阻断（证据进不了提交）已按用户裁决 **R1** 解掉；交付 push 403 已查明是 E0b token 只读并已换 token。
剩余：两稿对读、拆环境、四个 PR/候选分支去留（用户决定）。

---

## 1. 必读文件（按顺序）

| # | 文件 | 读什么 |
|---|---|---|
| 1 | `CONTEXT.md` | 术语表；**判据 ≠ 验收标准** |
| 2 | `docs/development/module-test-team/module-test-team-spec-20260901.md` | 冻结 spec；**修订 A（A.0–A.5）是现行条文**，A.2「退出码约定」「证据收集时点」与 A.5「已知局限」是 W4 期间的三处留痕 |
| 3 | `docs/development/module-test-team/module-test-team-plan-20260901.md` | 五波计划 + 改道条款三次裁决记录 |
| 4 | `docs/development/module-test-team/module-test-team-acceptance-criteria-20260901.md` | 验收标准；**执行记录表**（B/C 组已填）与「W4 实走中的发现」1–11 条是当前真相 |
| 5 | 本文件 §3–§8 | 环境、编制、四轮台账、遗留、下一步、坑 |
| 6 | `docs/development/room-native-bridge-handoff-20260829-wave3-m7.md` §7.6 | Bridge 轨环境重建配方（本栈照它起，差异见 §3.4） |
| 7 | `capabilities/skills/integration-run/SKILL.md`、`capabilities/skills/cross-repo-test/SKILL.md` | 改道后的技能文档（A.3） |
| 8 | 资产仓 `environments/e2e-fixture-joint/{environment.md,runner-task-template.md,run_round.py}`、`environments/sweep-itest.sh`、`scenarios/multi-currency-joint/{scenario.md,combinations.md,steps.json,combinations/*.json}` | 配方与判据的机器形态 |

**不要读** `docs/development/test-team-tiered-route-spec-20260831.md`（另一条线，对读另行安排）。
设计稿 `module-test-team-topology-draft-20260901.md` 只在要设计理由时查。

---

## 2. 进度总表

| 波次 | 状态 | 落点 |
|---|---|---|
| W0a P-1 | **FAIL**，改道生效 | spec A.0；AC 表 P-1 行；探针 `scripts/module-test-team/p1_probe.py` |
| W0b 建仓注册 | 完成 | 资产仓已建；W4 栈里由 seed 脚本注册 |
| W1 平台十行 + A 组 | 完成 | `06efc040`；A 组双档绿 |
| W2 控制台三处 | 完成 | `1911bdb2`；D4 静态半场绿 |
| W3 配方 + 技能 7 条 | 完成并两次改版 | 平台 `6a0e6738`/`d4e64ace`/`390a57b0`；资产仓 `2058b19`→`955f652` |
| W4 活体 | **B/C 组走完** | 本文件 §4–§6；AC 表 B1/B2/B3/C1/C2 行 |
| R1（执行器二次收集） | 完成 | `src/repomesh_runner/executor.py` + `tests/runner/test_executor.py` 四条；spec A.2 留痕 |

---

## 3. 环境实况（写本文件时全部活着）

### 3.1 进程与端口

| 组件 | 位置 | 备注 |
|---|---|---|
| W4 后端 uvicorn | `127.0.0.1:8077` | 由 `output/bridge-team/w4-live/start-backend.sh` 起（本会话用 `nohup … &` 重启过一次，pid 看 `netstat -ano \| grep :8077`）；访问日志 `output/bridge-team/w4-live/logs/backend.log`；**app 日志不在文件里，见 §8** |
| W4 前端 vite | `127.0.0.1:5281` | `.claude/launch.json` 的 `m8-frontend`（`REPOMESH_API_TARGET=8077`，`VITE_API_TOKEN=m8-console-token`）；上一会话的浏览器工具起的，本会话没用它，可能已退出 |
| 一次性 postgres | `127.0.0.1:15547` | 容器 `repomesh-w4-pg`（`--rm`，**停即消失**）；`alembic head`=`20260829_0041`；四轮台账都在里面 |
| controller forwarder | `127.0.0.1:18090 → agentteams-controller:8090` | 容器 `repomesh-controller-forwarder`（`docker start` 即可复用） |
| AgentTeams 控制器 | `agentteams-controller`（宿主单例，`agentteams-embedded:v1.2.0-rm3`） | Matrix 客户端入口 `127.0.0.1:18080`；18080 的 Higress 只放 GET |
| Bridge worker | pid 见 `output/bridge-team/w4-live/pids/test-worker.pid`（写时 26244，**R1 补丁后重启的**） | 成员 `repomesh-test-worker`，profile codex；日志 `logs/test-worker.err.log`（重启前的日志备份为 `test-worker.err.pre-r1.log`） |
| runner 工作区根 | `D:/Project4work/.repomesh-w4-live/workspaces` | 镜像 `repositories/55555555-….git` + 八个 run 的 worktree `w/<run-hash>/f3df6c0a5e1116c006b1`（都是 detached HEAD，四轮新的各含一笔证据提交） |
| W0b 的 dev 栈（与 W4 无关） | 后端 `8100` + `repomesh-dev-pg@5433` | `dev-up.sh` 起的；可 `scripts/dev-down.sh` 收 |
| 他线勿动 | `5280`/`8000`/`5432`/`55432`/`3000`/`8080` | 见记忆 `repomesh-local-env-recipe` |

### 3.2 凭据（全部在 gitignored 的 `output/bridge-team/w4-live/secrets/`）

| 文件 | 内容 | 有效期 |
|---|---|---|
| `controller-token.txt` | 控制器 API token | **控制器每次重启轮转**，重取：`docker exec agentteams-controller sh -c 'cat "$AGENTTEAMS_AUTH_TOKEN_FILE"'` |
| `appservice-as-token.txt` | Matrix appservice as_token | 随控制器数据卷，长期 |
| `admin-matrix-token.txt` | 服务端 Matrix 发信身份 `@admin`（≠ 任何 Bridge 成员） | M7 留下的，仍 200 |
| `runner-worker-tokens.json` | `REPOMESH_RUNNER_WORKER_TOKENS`（三个 agent id → token） | 随后端 env |
| `admin-session-token.txt` | 本地 admin 会话（本会话后端重启后已重登） | 后端重启需重登 |
| **`github-token.txt`（新）** | **本机 gh CLI 的 OAuth token（`gh auth token`，作用域 `repo`）**，W4 栈交付用它 push/开 PR | 随 gh 登录；**E0b 的 `output/bridge-team/secrets/e0b/github-token.txt` 是细粒度 PAT，对资产仓只读（push 403），不要再给交付用** |
| `../w4-members.env` | `E1_TEST_WORKER_MATRIX_TOKEN` / `E1_TEST_WORKER_REPOMESH_TOKEN`（Bridge 进程 env） | 长期 |
| 本地 admin | `w4admin` / `W4admin-2026!`（`w4_seed.py` 默认值，一次性栈专用） | — |
| LLM | `.env` 的 `REPOMESH_MODEL_API_KEY` | — |
| 只读 API | 控制台读模型（`/deliveries*`、`/rooms/{id}/stream`）与 `/bridge/materialize` 用 `Authorization: Bearer m8-console-token`；`/runtime/runner-events` 重放用 `w4-runner-control-token` | — |

### 3.3 后端 env（`start-backend.sh` 全文即配置）

与上一版相同，只改一行：`REPOMESH_DELIVERY_GITHUB_TOKEN` 现在读 `$S/github-token.txt`（gh CLI token）。
其余：`REPOMESH_DATABASE_URL=…15547`、`REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://127.0.0.1:18090`、`REPOMESH_RUNNER_WORKSPACE_ROOT=D:/Project4work/.repomesh-w4-live/workspaces`、
`REPOMESH_DELIVERY_AUTO_ENABLED=true`、`REPOMESH_DELIVERY_REQUIRED_CHECKS=["evidence-review"]`（JSON 列表；检查名故意不存在，PR 只开不合）、`REPOMESH_DELIVERY_REQUIRED_APPROVALS=1`、`REPOMESH_DELIVERY_BASE_BRANCH=main`、`MSYS_NO_PATHCONV=1`。

重启后端：先确认 8077 空（`netstat -ano | grep :8077`，残留 `taskkill //PID <pid> //F`），再
`nohup bash output/bridge-team/w4-live/start-backend.sh >> output/bridge-team/w4-live/logs/backend.log 2>&1 &`，
然后 `POST /api/v1/auth/login`（`{"username":"w4admin","password":"W4admin-2026!"}`）把 `access_token` 存进 `admin-session-token.txt`。Bridge 会自动重连。

### 3.4 从零重建（栈散了照这个）

与上一版 11 步相同（pg → forwarder → 重取 controller token → `w4_seed.py` → 起后端+登录 → provision 外部成员 → AC-D1 贴档 → `w4_chain.py` 发现链+materialize → binding → enrollment/Matrix token/codex auth/`icacls`/`start_members.ps1` → 派轮），差异只有：
- 第 5 步起后端前先 `gh auth token > output/bridge-team/w4-live/secrets/github-token.txt`；
- 第 11 步派轮改用 `scripts/module-test-team/w4_round.py dispatch <payload> --follow`（POST + 按回执 `task_ids[1]` 轮询 + 打印回执证据），**每次换 `idempotency_prefix`**；
- Bridge 起来后一定要看到日志 `bridge ready … rooms=2 governed=on`；改过 `src/repomesh_runner` 必须 `stop_members.ps1` + `start_members.ps1` 重启它。

---

## 4. 编制与身份（W4 库里的事实）

| 角色 | agent id | resource name | 形态 |
|---|---|---|---|
| 组织 `11111111-…-0001` 的 Manager | `22222222-0000-4000-8000-000000000002` | `repomesh-preflight-manager` | 控制器已有 manager 资源 |
| 业务仓 `pricing-fixture`（`42cf099f-…`）队长 | `33333333-…-0003` | `repomesh-preflight-leader` | M7 外部成员，未起 Bridge |
| 业务 worker | `4d1e6f00-…-0004` | `repomesh-preflight-probe` | M7 外部成员，未起 |
| 测试资产仓 `repomesh-test-assets`（`55555555-0000-4000-8000-000000000005`，`test_commands=[python environments/e2e-fixture-joint/run_round.py]`，`test_paths=[evidence/**]`，档案 `cross-repo-test-team`）队长 | `66666666-0000-4000-8000-000000000006` | `repomesh-test-leader` | 容器 copaw（server 拆解；在 leader room 试跑 shell 被工具护栏挂住并超时，不影响拆解） |
| 测试 worker | `4d1e6f00-0000-4000-8000-0000000000e1` | `repomesh-test-worker` | Bridge 外部成员 |

拓扑（项目 = issue `21899c3e-989f-5173-b6b7-838c6ca8a492`）：测试团队 `repomesh-team-55555555000040008000000000000005`（mode server），
team room `!sY1lFBp4hiErPGFPd6:matrix-local.agentteams.io:18080`，leader room `!n53KhR8E5xhvolJO1F:…`，worker DM `!iBuL2hIZ11UY2ptZYE:…`。

已派的联调轮（全部 `/bridge/materialize`，项目同上；前四轮是上一会话的）：

| 前缀 | plan | worker task | run | 证据 / 结论 | 提交 → 候选分支 → PR |
|---|---|---|---|---|---|
| `w4-b1-green` | `70794443` | `d6758df1` | `f375610a` | FAIL：工作区 Low 标签失败（ACL） | — |
| `w4-b1-green-2` | `1a06be85` | `78d545d3` | `7eaef69e` | 0 changed（`.gitignore` 误忽略） | 交付拒绝「no frozen commit」 |
| `w4-b1-green-3` | `9fda8693` | `4894faf2` | `1c1c6975` | 0 changed（收集顺序） | 同上 |
| `w4-b1-green-4` | `ce6f52ef` | `582d8505` | `461699d9` | `itest-t582d850506d2` PASS，0 changed（阻断点） | 同上 |
| **`w4-b1-green-5`** | `3f3524d1` | `4d3f3746` | `cf69c87c` | `itest-t4d3f3746c6d2` **PASS** | `64f32539` → `repomesh/3f3524d1/55555555` → **PR #1**（首推 403，换 token 后重放事件补推） |
| **`w4-b2-red`** | `0bc734f1` | `5ea75598` | `c997cb39` | `itest-t5ea7559844d3` **FAIL**（`199.99 != 200.0`） | `784a3eaa` → `repomesh/0bc734f1/55555555` → **PR #2** |
| **`w4-c1-blocked`** | `16127733` | `812db753` | `ed806252` | `itest-t812db753fb7d` **BLOCKED**（`unable to read tree (0000…)`） | `1ca21e00` → `repomesh/16127733/55555555` → **PR #3** |
| **`w4-c2-sweep`** | `6a1c4faa` | `9db7e001` | — | `itest-t9db7e001b276` PASS + 清扫 stale 清/fresh 留 | `57c7a10e` → `repomesh/6a1c4faa/55555555` → **PR #4** |

变更集：`3f1c9237`/`07f73728`/`64d71cc8`/`2b7c84af`，全部 `pr_open`；四个 PR 都被平台 `undraft_when_allowed` 提升为 ready（非 draft），因必需检查 `evidence-review` 不存在而停在未合并。
本会话的原始回执/证据 JSON 与轮询日志在 `output/bridge-team/w4-live/logs/{b1-5,b2,c1,c2}.*`（gitignored）。

---

## 5. 已落地的改动（本线提交）

平台仓（`feat/module-test-team-v1`）：`61153fd8` 文档四件套+CONTEXT → `06efc040` S-1+A 组 → `1911bdb2` S-2 三处 → `6a0e6738` 技能 7 条 → `d4e64ace` P-1 FAIL 留痕+改道 → `390a57b0` 取 A+Bridge 轨 → `edd423f3` 修 dispatch 漏传 profile → `511fb080`/`f1ef46fe` W4 记录与交接 → **本会话**：R1 执行器二次收集 + 四条单测 + spec/plan/AC/交接留痕 + `scripts/module-test-team/{w4_round.py,c2_plant_leftovers.sh,payloads/c2_sweep.json}`（头以 `git log` 为准）。

资产仓 main 未动（`955f652`）；四条候选分支各含一笔 `repomesh: complete task <id>` 提交（只有 `evidence/<run-id>/{round.md,steps.json}`）。

---

## 6. 遗留（不阻断验收，如实列出）

1. **回执摘要不含轮次结论**（spec A.5 已知局限、AC 发现 8）：agent 阶段先于 test 阶段 + J-12 无 python → worker 最终消息永远是「python 未找到」；结论只在证据与 PR 上。AC-B2/C1 对应子句标未达。v2 候选：执行器把 `test_commands` 尾行并入 `summary`，或平台叙事读 `steps.json.overall`。
2. **SCM poller 每轮 ERROR**：`SCM observation identity was reused for another external fact`（`external_id` 含 `:open:-` 但 payload 含可变字段）。交付线待修，与本线无关。
3. **外部成员 provisioning 不套档案覆盖**（发现 2，未修）：控制器侧 `repomesh-test-worker.skills=['coding']`，runner 侧按档案挂载正确。
4. **两稿对读**未做：`docs/development/test-team-tiered-route-spec-20260831.md` vs 本线四件套。
5. **前端 D 组**本会话未重跑（上一会话 D1–D4 PASS 的事实未变；新增的四个 PR 在交付页可见但未截图）。

---

## 7. 下一步

1. **两稿对读**（另行安排，对读时以差异点为议题；本线裁决落点是 spec）。
2. **决定发现 8 的处置**：接受为 v1 局限（现状）或立 v2 条目（执行器尾行并入摘要）。
3. **拆环境**：`stop_members.ps1 -Members …/members.w4.json -PidDir …/pids -Subset w4` → `taskkill` 8077 的 pid → `docker stop repomesh-w4-pg`（`--rm` 自删，**台账随之消失**，要留就先 `pg_dump`）→ `docker stop repomesh-controller-forwarder` → 控制器里 `DELETE /api/v1/workers/repomesh-test-worker`、`repomesh-test-leader` 及 Team（可留作下次复用）。
4. **GitHub 上的四个 PR / 候选分支**：用户决定关闭或保留；它们是 B1/B2/C1/C2 的证据实体，关闭前把 PR 号与分支 SHA 留在 AC 表（已留）。
5. 推分支 `feat/module-test-team-v1` 到 GitHub（用户决定）。

---

## 8. 坑清单（含本会话新增）

- **app 日志不在 backend.log**：`observability` 的 `QueuedLogRecorder` 把 handler 挂在 root logger，WARNING/ERROR（含交付异常 traceback）只进 `observability.log_entries`：`select ts,level,source,message,exc_info from observability.log_entries where level in ('WARNING','ERROR') order by ts desc`。
- **E0b PAT 只读**：`github_pat_…` 对资产仓 push 403；API `permissions.push=true` 是用户角色不是 token 权限；公开仓 `ls-remote` 通不代表能 push。用 `git push --dry-run` 试。
- **交付补推的操作杆**：重放该任务的 `runner.completed` 事件（从 `agent_runtime.runner_events.payload` 取原文）`POST /api/v1/runtime/runner-events`（`Bearer w4-runner-control-token`），`duplicate=true` 也会重跑 advance → `handle_batch` 幂等 → publish。
- **改了 `src/repomesh_runner` 必须重启 Bridge**（执行器在 Bridge 进程内）；`start_members.ps1` 会挂住调用它的 Bash（`< /dev/null` 后台起，看 `bridge ready`）。
- **Windows 控制台编码**：读含中文的 API JSON 要 `PYTHONIOENCODING=utf-8` 或按 bytes 解码，否则像「JSON 损坏」；psql 取行用 `row_to_json`，别用 tab 拼接。
- **contentHash 对账看 blob id**：工作树是 CRLF（`core.autocrlf=true`），blob 是 LF；`git ls-tree` 的 blob id = GitHub contents API 的 `.sha`；raw sha256 要先 `tr -d '\r'`。
- **C2 种残留要抢窗口**：worktree 在派工时才建；`scripts/module-test-team/c2_plant_leftovers.sh <root>/w f3df6c0a5e1116c006b1` 先起再派轮，agent 阶段的几十秒足够；`touch -d '30 hours ago'` 先动内容再动目录根。
- `icacls` 的 `/grant` 在 Git Bash 会被转成路径：`MSYS_NO_PATHCONV=1`。
- Windows `python` 不认 `/d/...` 路径：用 `D:\...` 或 `.venv/Scripts/python.exe` 配 `MSYS_NO_PATHCONV=1`。
- bash heredoc 未加引号时反引号会被当命令替换；载荷用 `mk_payloads.py` 或 json 脚本生成。
- 发现链：`force_continue` 首轮不能带；`approval.state`；`candidates` 是 `{items:[…]}`。
- `REPOMESH_DELIVERY_REQUIRED_CHECKS` 必须是 JSON 列表字符串。
- 房间流 JSON 偶有控制字符：`json.loads(..., strict=False)`。
- 控制器 `/docker/` 直通、`/api/v1/workers` POST：18080 只放 GET，从网内容器或 18090 打。
- 找 run 用 Bridge 日志的 `accepted task <id> as run <run>`；worktree 目录名是 hash，不是 run id 前缀。

---

## 9. 记忆入口

`~/.claude/projects/D--Project4work-GOAI-infra-repomesh/memory/test-team-line-state.md`（索引行在 `MEMORY.md`）。
