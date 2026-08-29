# Room-Native Bridge 交接文档(波次 3:M8 通过、M7 主链贯通、一处缺陷待修)

> 日期:2026-08-29
> 分支:`feat/room-native-agent-bridge`(main 之上 95 提交,未推送;头 `44ebc91a`,**本轮零代码改动**)
> 状态:**M8 活体 PASS;M7 主链在真机上全跑通,末端「Leader → Manager 汇总」发现缺陷
> D-M7-1,阻断 AC-05,V2 前必修**
> 上一份交接:`room-native-bridge-handoff-20260828-dev-close.md`(开发面收口,仍有效)
> 活体判定与证据:`output/bridge-team/m8-evidence/11-verdict.md`、
> `output/bridge-team/m7-evidence/05-preflight-verdict.md`、`.../13-m7-verdict.md`(均 gitignored)
> 读者:零上下文接手本线的工程师或 agent 会话

---

## 0. 从这里开始(接手第一屏)

```bash
git -C <repo> log --oneline -3     # 本轮只有这份 docs 提交,代码零改动
git -C <repo> status --short       # M/?? 都是他线的,别动;`.claude/` 见 §7
git worktree list

# 门禁(代码未变,数字应与 dev-close 一致)
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q          # 期望 2213 passed / 26 skipped
```

**当前一句话**:开发面早已收口,本轮是波次 3 的**活体**。M8 过了;M7 把 leader 轨从
「计划 → 派活 → 受治理执行 → 基于证据审查」整条在真机上走通了,只在最后向
Organization Manager 汇总那一跳撞出一个此前从未暴露的缺陷(§2)。**下一个动作是修
D-M7-1**,而不是接着往 E1 走——六实例会把这条路径乘以三。

**活体环境仍在运行,没有拆**(§7.1 是现场账面)。**要从零重建它,照 §7.6 的完整命令配方**
——本轮的 seed 与链驱动脚本都在会被清掉的 scratchpad 里,配方因此写进了这份文档。

---

## 0.5 达成度对照(对 dev-close §0.5 的增量)

| dev-close 时的结论 | 本轮变化 |
|---|---|
| 「尚未活体验证:leader 轨全链(M7)、房间时间线到前端(M8)、六实例(E1)、真实交付(E0b/V2)」 | **M8 与 M7 主链已转实证**;E1/E0b/V2 仍未做 |
| PR 9(RoomTimelineIngest)只有自动化证据 | ✅ 真实 Matrix 消息 ~3 秒进读模型、开着的页面 10 秒内自动出现;非白名单房间零落库 |
| PR 10(External 展示)只有 replay 夹具截图 | ✅ 活体复核过:Agents 页显 `External`,无 Pending/容器词,不伪造 uptime |
| PR 7 + PR 8(leader 轨)只有自动化证据 | ✅ 计划面全通(GET 包 / POST plan / 派活 / 执行 / review 判定);❌ 汇总投递撞 D-M7-1 |
| D-2(materialize 采用外部 leader 激活 leader 模式) | ✅ **首次活体成立** |
| AC-03 自动接单 | ✅ **首次在 leader 派生的任务上成立**(此前只在服务端直拆的任务上验过) |

---

## 1. 本轮三段

### 1.1 M8(Room/UI)—— PASS

判据来自验收标准 AC-06「真实房间消息应在 5 秒轮询下约 10 秒内出现」。

- 真实 Matrix 消息 → `room_stream` **~3 秒**;**页面开着不刷新,第二条消息在 10 秒内自动出现**。
- 历史消息按 Matrix `origin_server_ts` 回填,排序稳定;身份全部解析正确。
- leader DM 分支同样落库(白名单两分支都活)。
- **负向过**:往不在拓扑的房间发消息 → DB 零行、读模型 404。
- PR 10 活体复核并入:Agents 页两名 external 成员显 `External`,manager 无数据显「未接入」。

wave2 §5 的 M8 预检五条全过(迁移 head=0039;服务端 Matrix 账号已 join;拓扑房间号已回写;
token 有效 poller 组装;raw matrix id 是 D-4 设计)。

### 1.2 M7 预检 —— 六条过五条,并修掉三处

见 `m7-evidence/05-preflight-verdict.md`。**最值钱的一条**见 §3.1。另外两处:

- leader **整个 session 目录不存在** → 按 D-10 用 `copy_codex_auth.ps1` 复制 auth.json
  (必须在首次 `ensure_ready` 之前,否则文件停在 Medium 完整性,受限子进程写不了)。
- `.env` 的 `REPOMESH_RUNNER_WORKSPACE_ROOT` 指主仓,而 V1 现场在
  `.repomesh-v1-live/workspaces`;后端启动 env 不显式传就会继承 `.env`,与花名册的
  `workspaceRoot` 打架(PR 5 §5 要求同一路径)。

**leader 双 POST 首次对真实路由跑通**(`m7-evidence/04`):用**冻结契约夹具**当 body,
401 `invalid_token` / 403 `forbidden_role` / 404 `assignment_not_found` 与冻结错误矩阵
逐条一致。其中 **worker token 调 leader 端点 = 403**,是 AC-02 反向封锁的另一半。
夹具能通过 producer 的 body 模型,等于顺带做了一次契约一致性验证——
**wave2 记账 9 / 任务卡 `task_474c1900` 可以关了**,活体比 in-process ASGI 用例更强。

### 1.3 M7 正式流程 —— 主链贯通

见 `m7-evidence/13-m7-verdict.md`。逐段:

| 段 | 结果 |
|---|---|
| 发现链(Issue→分析→候选→分档→范围确认→计划) | ✅ 真 LLM(DeepSeek)全过 |
| **materialize 采用外部 leader → 闩 `leader` 模式** | ✅ D-2 首次活体 |
| 服务端为 leader 停驻(不再同步直拆) | ✅ 只有 1 条 leader 任务、**无 worker 子任务** |
| leader 认出 plan notice → GET 包 → **真 Codex 出 Spec/DAG/worker 任务** → POST `/plan` | ✅ `accepted plan revision 1` |
| worker 自动接单(AC-03) | ✅ `start-worker-task 202`,**全程无人敲 UUID** |
| 受治理执行:真改码 / 真测试 / 真提交 | ✅ `return subtotal * (1 + tax_rate)`,commit `6d9172cc4e05`,`run_tests.py` exit 0 |
| 房间叙事四条 + 终态纯证据 | ✅ accepted / started / tests / done |
| leader 收 review notice → 取证据包 → **基于证据判定** | ✅ 汇总原话引用了改动文件与 exit 0,leader 任务 succeeded |
| **leader → Manager 汇总投递** | ❌ **500,见 §2** |

worker 任务的标题 `Apply tax rate in calculate_total` 是 **Codex 自己的措辞**,不是平台模板
——这是「产物真出自 leader 会话」的旁证。

---

## 2. 缺陷 D-M7-1(全案最重要;阻断 AC-05)

**现象**:`POST /agent-actions/leader/assignments/{id}/review` 返 **500**,leader Bridge 记
`RepoMesh could not be asked about leader task …`。**但服务端其实已经把 review 收下了**
——leader 任务已 succeeded、汇总正文已落库。即「事实成功,调用方收到失败」。

**根因**(逐层从 traceback 核实):

```
submit_repository_review              api/leader_actions.py:126
  → SubmitRepositoryReview.execute    task_orchestration/application.py:2052
  → self._reporter.report(...)        task_orchestration/application.py:632
       ① 先 self._tasks.update(...)   ← 状态在此提交,所以任务是 succeeded
       ② 再向 task.assigned_by_agent_id(= Organization Leader)发协作消息
  → SendCollaborationMessage._deliver collaboration/application.py:145
       标记 message.failed() 后 re-raise
  → AgentTeamsMatrixClient.send_task  integrations/agentteams/matrix.py:152-158
       recipient_matrix_id = (await self._control_plane.get_worker(name)).matrix_user_id
       取不到 → AgentTeamsUnavailable("AgentTeams recipient Matrix identity is unavailable")
```

**关键事实**:Organization Leader 在 controller 里是 **Manager 资源**,不是 Worker
(`integrations/agentteams/runtime_projection.py` 的 `_register`:ORGANIZATION_LEADER 走
`get_manager` / `ensure_manager`)。而 `send_task` **只查 workers 集合**。本机实证:

```
GET /api/v1/managers/repomesh-preflight-manager -> 200   (matrixUserID=@manager:… 齐全)
GET /api/v1/workers/repomesh-preflight-manager  -> 404   ← send_task 走的就是这条
```

⇒ **凡收件人是 Organization Leader 的协作消息,身份解析必失败**。`CollaborationDeliveryRetryWorker`
也救不回来——路由本身错,不是瞬时不可达。

**为什么此前从未暴露**:server 模式的 roll-up(`task_orchestration/application.py:1436-1454`)
直接调**领域对象** `parent.report(...)` + `tasks.update(...)`,**不发协作消息**;只有 leader
模式的 review 走**应用层** `_reporter.report(ReportTaskCommand…)`,那条才向上汇总。
所以这是 **leader 模式专属、且正好落在 M7/V2 关键路径上**的缺陷。

**修法方向(待裁决,主脑活)**:

1. **主问题**:`send_task` 的收件人身份解析要按**资源种类**分流——worker / repository_leader
   查 workers,organization_leader 查 managers。Manager 文档确实带 `matrixUserID`(已实证)。
   角色→集合的映射 `_register` 里已有,**不该在 messenger 里再发明第二份**;
   干净的做法是让解析走一个知道角色的 port,而不是让 messenger 猜。
2. **次要问题**:`report()` 先落状态再发通知,通知失败时调用方收 500 而事实已生效。
   至少要让两个信号一致——或把投递失败降级为「已受理 + 待重试」而不是 500,
   否则 Bridge 会把成功的审查记成失败(本轮就是这样)。
3. 两处都要有 interface 行为测试;`send_task` 的双集合解析要有 memory adapter 覆盖。

**AC 影响**:AC-05 明写 `Manager → Leader → Worker → Leader → Manager`。最后一跳断了,
**V2 不修必 FAIL**。

---

## 3. 本轮的两条环境级修正(无代码改动,但必须写进 runbook)

### 3.1 服务端 Matrix 发信身份不得等于任何 Bridge 成员身份

wave2 §5 的 M7 头号未知是「leader DM 事件是否带 `m.mentions`」。**实测是带的**
(`m7-evidence/01`),`_mentions_me` 会判 True。但顺藤摸到了真正会让 M7 静默失败的东西:

`repomesh_agent_bridge/inbox.py:167-168` —— **`event.sender == matrix_user_id` 直接跳过**
(自己的投影回声)。而 E0a/V1 配方把 **leader 的 Matrix token 给了服务端**当发信账号:
V1 无害(服务端发给 worker,身份不同),**M7 下 leader Bridge 就是这个身份,派活会被它
自己当回声丢掉**——症状正是「活着但沉默」,但根因与清单猜的不同。

**修法(零环境改动)**:服务端改用 **`@admin`**——它已 join 两个目标房间(共 63 个),
且区别于 leader/probe 两个 Bridge 身份。切换后 M8 无回归(重发消息 ~3 秒到、身份正确)。

**推广到 E1/V2 的硬约束**:**RepoMesh 服务端的 Matrix 身份必须区别于每一个 Bridge 成员
身份,且必须已 join 全部授权房间。** `scripts/bridge-e1/README.md` 的 env 表只写了
「`REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN` 是 M8 需要的」,**没规定用哪个身份**;
六成员时若沿用任一成员 token,那名成员必然哑掉。**建议补进 runbook**。

顺带证实:**D-3 出站优先去重生效**——团队房里派活消息显示的发送者是
`repomesh-preflight-leader`(出站记录的业务语义),而不是 Matrix 层的 `@admin`。

### 3.2 Bridge 必须先起,再 materialize

停驻通知(正文 B,带 `/plan` 决策路由)由 `_park_for_leader` 发出,**带幂等键
`{key}:leader-plan`,设计上不会重发**;Bridge 无 backfill,启动即建基线跳过历史。
首轮我先 materialize 后起 Bridge,leader 错过了通知。

**`redispatch` 救不了**:它重发的是**正文 A(任务包)**,不带决策路由;
`leader_lane.parse_leader_notice` 键控的是**路由**而非措辞,所以正文 A 不是 leader notice
→ 落会话轨 → 工具被 deny-all 拒 → 回一条 blocked note(设计正确,不是 bug)。

⇒ **硬顺序:起全部 Bridge → 再 materialize/派活**。建议写进 E1 runbook 的步骤序。

---

## 4. 门禁

**本轮零代码改动**,门禁数字沿用 dev-close:`2213 passed / 26 skipped`,ruff 干净。
接手若要复跑,注意 3 个 skip 是 PG 时间线测试(设 `REPOMESH_TEST_POSTGRES_URL` 即转 passed)。

---

## 5. 下一步(顺序建议)

1. **修 D-M7-1**(§2)。带 interface 行为测试;修完在**当前活体环境**上直接复验:
   leader 任务已 succeeded,但 `collaboration.messages` 里那条 `task_report` 仍是 `failed`
   ——重试或重跑一轮 review 即可验证投递是否恢复。
2. **重跑一次 M7 收尾**,确认 `Leader → Manager` 汇总真的落进 manager 的房间(AC-05 取证)。
3. **E1 六实例 soak**(`scripts/bridge-e1/README.md` 11 步;先把 §3.1/§3.2 两条补进 runbook)。
4. **E0b**(隔离环境短开 delivery,三仓白名单)。
5. **V2/Q3b**:门禁 #10 六前置显式核验;PASS 结论用验收标准 §8 推荐原文;
   三个 Draft PR 取证后**立即关 delivery**。

---

## 6. 记账(不阻塞)

| # | 事项 |
|---|---|
| 1 | **未鉴权 + 畸形 body 的 leader POST 返 422 而非 401**(FastAPI 先验 body 模型)。body 合法时一律 401,故只是框架顺序;泄漏面仅限已冻结公开的 schema 字段名 |
| 2 | `preflight_bindings.py` 的 **skills 检查过严**:leader 在 controller 是 `['code-review']`、期望 `['code-review','planning']` → FAIL。但 **P2 修正后 `_register` 读优先**,资源已存在时只断言名字、**不比对 skills/runtime/model**;external 成员又无容器,skills 纯装饰。建议降级为 warn,或在 runbook 注明「仅对**将被创建**的资源有意义」 |
| 3 | **seed 保真**:手种 principal 时,仓库级角色(repository_leader/worker)**必须给非空 `responsibility_paths`**——`agent_directory/application/create.py:124-125` 的 `_validate_scope` 会拒绝空值,即产品自己造不出那种 principal。本轮 leader 曾种成空,已改 `["**"]` |
| 4 | 首轮(顺序错)的 Issue `f1a6cce9…` 残留一条 `assigned` 的 leader 任务;有效那轮是 `5f09cfd7…`。取证时别读错轮次 |
| 5 | dev-close §6 的记账 1–9 继续有效(F-1 mock 端点无鉴权 `task_082d8b72`、`recipient_agent_id` 类型谎言、`sender_matrix_user_id` 前端够不着、`RUNTIME_SKIN.external` 无生产渲染者等) |

---

## 7. 环境、凭据与现场(**活体仍在运行,未拆**)

> **要从零重建这套环境,直接读 §7.6 的完整命令配方**;§7.1–§7.5 是当前现场的账面与
> 几条必须知情的事实。本轮所有 seed / 链驱动脚本都在会被清掉的 scratchpad 里,
> 所以配方写在这份 tracked 文档中(同 PR 4 交接 §7.5 的理由)。

### 7.1 进程与端口

| 项 | 值 |
|---|---|
| 一次性 postgres | 容器 `repomesh-e2e-pg` @`127.0.0.1:15547`(`--rm`,停即消失);**本轮重建过一次**,原因见 §7.4 |
| controller forwarder | 容器 `repomesh-controller-forwarder` @`127.0.0.1:18090`(用完删) |
| RepoMesh 后端 | uvicorn @`127.0.0.1:8077`,**PID 对 `22804` / `31788`** |
| 前端 | vite @`127.0.0.1:5281`(避开他线 5280),配置 `.claude/launch.json` 的 `m8-frontend` |
| leader Bridge | PID `26252`(`output/bridge-team/m7-live/pids/preflight-leader.pid`) |
| worker Bridge | PID `19924`(同上 `preflight-worker.pid`) |

**拆环境**:Bridge 用 `scripts/bridge-e1/stop_members.ps1`(读同一批 PID 文件);
后端**两个 PID 都要杀**;最后 `docker rm -f repomesh-e2e-pg repomesh-controller-forwarder`。
⚠️ **`pkill -f` 杀不掉这些进程**。他线端口 5432/55432/8080/3000/5280/8100 全程未碰。

### 7.2 后端启动 env(本轮定型,比 E0a 配方多三项)

```
REPOMESH_DATABASE_URL=postgresql+asyncpg://postgres:e2e@127.0.0.1:15547/postgres
REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://127.0.0.1:18090
REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN=<容器内取,见下>
REPOMESH_AGENTTEAMS_MATRIX_URL=http://127.0.0.1:18080
REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN=<@admin 的 token>        ← §3.1,不得用成员 token
REPOMESH_RUNNER_CONTROL_TOKEN=live-runner-token
REPOMESH_RUNNER_WORKER_TOKENS=<leader + worker 两条的 JSON map>   ← D-6,leader 也要有
REPOMESH_AGENT_ACTION_TOKEN=m8-console-token                      ← 读模型路由鉴权
REPOMESH_RUNNER_WORKSPACE_ROOT=D:/Project4work/.repomesh-v1-live/workspaces
```

三项新知:
- **`REPOMESH_AGENT_ACTION_TOKEN` 是读模型路由(`/console/*`、`/rooms/*`、`/issues/*`)的
  鉴权**,V1 从未配过;**前端 `VITE_API_TOKEN` 必须同值**,否则页面 401。
- `REPOMESH_RUNNER_WORKER_TOKENS` 现在要装**两条**(D-6 已把语义推广为 external 成员 token)。
- workspace root 必须与花名册一致(§1.2)。

后端会继承 `.env`(pydantic-settings 的 `env_file`),LLM key 就是这么进来的;
`.env` 无任何 `REPOMESH_DELIVERY_*` 键,**delivery 全程关闭**(D-12,实测 `delivery_auto_enabled=False`)。

### 7.3 凭据(全部 gitignored,任何 token 不入 tracked 文件)

`output/bridge-team/secrets/`:
- `controller-token.txt` —— **栈重启会轮转**,本轮已更新;取法
  `docker exec agentteams-controller sh -c 'cat "$AGENTTEAMS_AUTH_TOKEN_FILE"'`
- `appservice-as-token.txt` —— 未变;取法 `docker exec … printf %s "$AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN"`
- `admin-matrix-token.txt` —— **本轮新增**,服务端发信身份(§3.1),appservice login 铸
- `m7-leader-token.txt` —— **本轮新增**,leader 的 external 成员 token
- `runner-worker-tokens.json` —— 已扩为 leader + worker 两条
- `leader-matrix-token.txt` / `probe-matrix-token.txt` —— 两个 Bridge 各自的 Matrix token

### 7.4 M7 工作件与「为什么重建了库」

`output/bridge-team/m7-live/`:`members.m7.json`(花名册)、`bindings/`、`enrollments/`、
`m7-members.env`、`pids/`、`logs/`。

**重建库的原因**:M8 阶段我手种了拓扑(为给房间白名单),但 D-2 的 leader 模式只能由
**发现链的 materialize** 落定(见下),它会为新 project 另建拓扑——两个拓扑指向同一批房间
会违反 `authorized_room_reader` / `find_view_by_room` 假设的「一个房间号只属于一个团队」。
故重建库、**不再手种拓扑**,只种 admin + 仓库 + 三个 principal(与 `seed_members.py` 同类)。

### 7.5 一条容易踩空的路由事实

**`POST /api/v1/bridge/materialize`(plan bridge)不跑 runtime projection**,因此
**不会**采用外部 leader、不会闩 leader 模式;它只建计划与任务。
唯一会 reconcile 并采用的是**发现链的**
`POST /api/v1/issues/{issue_id}/discovery/materialize`
(`modules/repository_intelligence/application/discovery_materialization.py:226` 调
`_runtime.project(...)`)。本轮先打了前者、白跑一轮服务端直拆,才发现这件事。

M7 用手写 plan 打 plan bridge 是 smoke 的合法做法;**V2 不行**——终局验收的 plan 必须
来自真实发现链(验收标准 §5)。

### 7.6 从零复现本轮环境的完整命令配方

> **这套配方必须留在这里**:本轮用的 seed / 链驱动脚本都在 scratchpad,会被清掉;
> `output/` 整个是 gitignored。照此可从零重建 M8 + M7 的活体环境。
> 与 PR 4 交接 §7.5 的关系:那份是「后端 + 单 Worker」的最小配方,这份是它的超集
> (多了 external leader、leader-actions token、发现链与两个 Bridge)。
> **三条环境坑全程适用**:`MSYS_NO_PATHCONV=1`、控制面与 Bridge 同跑 Windows 宿主、
> **5432 活体库谱系不符不得触碰**。他线端口 5432/55432/8080/3000/5280/8100 勿动。

#### 步骤 1 — 平台栈与一次性库

```bash
# Docker 引擎(第 6 次 socket 损坏的修法见 §8.1;冷启动也可能撞)
docker ps                                  # agentteams-controller 应在跑,18080 已发布

# 一次性 postgres(--rm,停即消失;绝不用 5432)
docker run --rm -d --name repomesh-e2e-pg \
  -e POSTGRES_PASSWORD=e2e -p 127.0.0.1:15547:5432 postgres:17-alpine
sleep 6 && docker exec repomesh-e2e-pg pg_isready -U postgres

REPOMESH_DATABASE_URL="postgresql+asyncpg://postgres:e2e@127.0.0.1:15547/postgres" \
  .venv/Scripts/python.exe -m alembic upgrade head        # 期望 head = 20260828_0039

# controller forwarder(后端跑宿主时必需;controller 8090 未发布到宿主。用完删)
NET=$(docker inspect agentteams-controller --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
docker run -d --name repomesh-controller-forwarder --network "$NET" \
  -p 127.0.0.1:18090:8090 --entrypoint sh \
  alpine/socat:latest -c "socat TCP-LISTEN:8090,fork,reuseaddr TCP:agentteams-controller:8090"
```

#### 步骤 2 — 凭据(全部写进 gitignored 的 `output/bridge-team/secrets/`)

```bash
# controller API token —— 栈每次重启都会轮转,必须重取
docker exec agentteams-controller sh -c 'cat "$AGENTTEAMS_AUTH_TOKEN_FILE"' | tr -d '\r' \
  > output/bridge-team/secrets/controller-token.txt
# appservice as_token —— 取任何成员 Matrix token 的钥匙
docker exec agentteams-controller sh -c 'printf %s "$AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN"' \
  > output/bridge-team/secrets/appservice-as-token.txt

# 逐身份铸 Matrix token(appservice login)。<user> 依次取:
#   admin                        → 服务端发信身份(§3.1,必须 ≠ 任何 Bridge 成员)
#   repomesh-preflight-leader    → leader Bridge
#   repomesh-preflight-probe     → worker Bridge
AS=$(cat output/bridge-team/secrets/appservice-as-token.txt | tr -d '\r\n')
curl -s -X POST -H "Authorization: Bearer $AS" -H "Content-Type: application/json" \
  -d '{"type":"m.login.application_service","identifier":{"type":"m.id.user","user":"<user>"}}' \
  http://127.0.0.1:18080/_matrix/client/v3/login
```

**服务端身份的选取判据**(§3.1):必须已 join 全部授权房间,且不等于任何 Bridge 成员。
本轮选 `@admin`,因为它已在团队房与 leader DM 里(共 63 个房间)。换环境时先核:

```bash
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:18080/_matrix/client/v3/joined_rooms
```

**external 成员 token**(D-6,`REPOMESH_RUNNER_WORKER_TOKENS` 的值)人工铸随机值,
写成 `{"<leaderAgentId>":"<tok>","<workerAgentId>":"<tok>"}` 存
`secrets/runner-worker-tokens.json`。**leader 也必须有一条**,否则 leader-actions 全 401。

#### 步骤 3 — seed(admin + 仓库 + 三个 principal,**不种拓扑**)

约 40 行脚本,要点(拓扑留给产品建,原因见 §7.4):

- 用 `repomesh.bootstrap.app.build_default_container()`(**不是** `ApplicationContainer()`,
  后者要 10 个位置参数);收尾 `await container.close()`。
- admin:`container.local_account_service().bootstrap_admin(user, pass, display)`
  ——仅当账户表为空时可用。本轮用 `v1admin`。
- 仓库:`container.repository_catalog.add(RepositoryProfile(id=…, name="pricing-fixture",
  url="D:/Project4work/.repomesh-v1-live/fixture-pricing",
  test_commands=("python scripts/run_tests.py",), test_paths=("tests/**",)))`。
  `RepositoryProfile` 接受显式 `id`。
- principal:`container.agent_directory.add(p, idempotency_key=…,
  request_fingerprint=command_fingerprint(p), events=(registered_event(p),))` 直写
  (与 tracked 的 `scripts/bridge-e1/seed_members.py` 同型,可照抄其 `registered_event`)。
- **id 必须钉死**(后面每一步都以它们为键):

  | 角色 | agent id | `agentteams_resource_name` |
  |---|---|---|
  | organization_leader | `22222222-0000-4000-8000-000000000002` | `repomesh-preflight-manager` |
  | repository_leader | `33333333-0000-4000-8000-000000000003` | `repomesh-preflight-leader` |
  | worker | `4d1e6f00-0000-4000-8000-000000000004` | `repomesh-preflight-probe` |

  organization/repository id:`11111111-0000-4000-8000-000000000001` /
  `42cf099f-fadc-4222-95ab-bbd4770f7fdc`。
  **worker 的 id 必须是 `4d1e6f00-…-0004`**——`session_root()` 由它派生,钉住才能落在
  已登录 codex 的 `CODEX_HOME` 上(PR 4 §7.5 的省一次登录手法)。
- `singleton_key` 按 `CreateAgent._singleton_key` 的公式:org leader
  `organization:{org}:leader`、repo leader `repository:{repo}:leader`、worker `None`。
- **仓库级角色的 `responsibility_paths` 不能为空**(记账 3):leader 给 `("**",)`,
  worker 给该仓的责任路径(本轮 `("src/**","tests/**")`)。

夹具仓 `D:/Project4work/.repomesh-v1-live/fixture-pricing` 需处于**未修复**状态
(`calculate_total` 直接 `return subtotal`,自带测试必失败),worker 的活就是修它。

#### 步骤 4 — 后端(env 全表见 §7.2)

```bash
CTL=$(cat output/bridge-team/secrets/controller-token.txt | tr -d '\r\n')
AT=$(cat output/bridge-team/secrets/admin-matrix-token.txt | tr -d '\r\n')
WT=$(cat output/bridge-team/secrets/runner-worker-tokens.json | python -c "import json,sys;print(json.dumps(json.load(sys.stdin)))")

MSYS_NO_PATHCONV=1 \
REPOMESH_DATABASE_URL="postgresql+asyncpg://postgres:e2e@127.0.0.1:15547/postgres" \
REPOMESH_AGENTTEAMS_CONTROLLER_URL="http://127.0.0.1:18090" \
REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN="$CTL" \
REPOMESH_AGENTTEAMS_MATRIX_URL="http://127.0.0.1:18080" \
REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN="$AT" \
REPOMESH_RUNNER_CONTROL_TOKEN="live-runner-token" \
REPOMESH_RUNNER_WORKER_TOKENS="$WT" \
REPOMESH_AGENT_ACTION_TOKEN="m8-console-token" \
REPOMESH_RUNNER_WORKSPACE_ROOT="D:/Project4work/.repomesh-v1-live/workspaces" \
nohup .venv/Scripts/python.exe -m uvicorn repomesh.bootstrap.app:create_app \
  --factory --host 127.0.0.1 --port 8077 > <log> 2>&1 &
```

后端会继承 `.env`(LLM key 从那里来);`.env` 无 `REPOMESH_DELIVERY_*`,delivery 保持关闭。

#### 步骤 5 — 前端(可选,M8 取证与 AC-06 实走要)

`.claude/launch.json` 已含 `m8-frontend`:vite 跑 **5281**(避开他线 5280),
`REPOMESH_API_TARGET=http://127.0.0.1:8077`,**`VITE_API_TOKEN` 必须等于
`REPOMESH_AGENT_ACTION_TOKEN`**。登录用 seed 的 admin 账户。
**本库无 Issue 行时**,Room 页可直接走 `#/issues/<project_id>/rooms/<room_id>`。

#### 步骤 6 — 两个 Bridge(**必须在派活之前起来**,§3.2)

```bash
# 6.1 花名册:output/bridge-team/m7-live/members.m7.json
#     两名成员 subsets:["m7"];leader 无 workspaceRoot(CLI 会拒),
#     worker 的 workspaceRoot 必须 == 后端 REPOMESH_RUNNER_WORKSPACE_ROOT

# 6.2 取 binding v2(成员须已属 Team;本轮复用既有 repomesh-preflight-team)
GET /api/v1/runtime/v2/external-members/{agentId}/binding?role={worker|repository_leader}
#   → 存成 bindings/binding.<key>.json;核对 containerManaged:false 与 allowedRoomIds

# 6.3 生成 enrollment(身份字段一律取自 binding,不取自花名册)
.venv/Scripts/python.exe scripts/bridge-e1/make_enrollments.py \
  --members output/bridge-team/m7-live/members.m7.json \
  --bindings output/bridge-team/m7-live/bindings \
  --out output/bridge-team/m7-live/enrollments --subset m7

# 6.4 D-10 复制 auth.json —— 必须在首次 ensure_ready 之前
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bridge-e1/copy_codex_auth.ps1 \
  -Members output/bridge-team/m7-live/members.m7.json -Subset m7 \
  -SourceCodexHome "$LOCALAPPDATA\repomesh-agent-bridge\sessions\4d1e6f00-0000-4000-8000-000000000004\codex-home"

# 6.5 env 文件(四个变量,名字由 enrollment 的 env: locator 决定)
#   E1_PREFLIGHT_LEADER_MATRIX_TOKEN / E1_PREFLIGHT_LEADER_REPOMESH_TOKEN
#   E1_PREFLIGHT_WORKER_MATRIX_TOKEN / E1_PREFLIGHT_WORKER_REPOMESH_TOKEN

# 6.6 起进程(每成员一个;PID 写进 pids/)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bridge-e1/start_members.ps1 \
  -Members output/bridge-team/m7-live/members.m7.json \
  -EnrollmentDir output/bridge-team/m7-live/enrollments \
  -EnvFile output/bridge-team/m7-live/m7-members.env \
  -PidDir output/bridge-team/m7-live/pids -LogDir output/bridge-team/m7-live/logs -Subset m7
```

起好的判据(各自日志里恰好一行,两侧的开关是镜像的):

```text
bridge ready: member=repomesh-preflight-leader role=repository_leader profile=codex rooms=2 governed=off leader-lane=on
bridge ready: member=repomesh-preflight-probe  role=worker            profile=codex rooms=2 governed=on  leader-lane=off
```

`rooms=2` 两侧含义不同:leader 是「团队房 + leader DM」,worker 是「团队房 + worker DM」。

**R8 只读预检**(可随时跑,两 GET/成员):

```bash
E1_CONTROLLER_TOKEN=… E1_PREFLIGHT_LEADER_REPOMESH_TOKEN=… E1_PREFLIGHT_WORKER_REPOMESH_TOKEN=… \
  .venv/Scripts/python.exe scripts/bridge-e1/preflight_bindings.py \
  --members output/bridge-team/m7-live/members.m7.json --subset m7
```

skills 那条 FAIL 是良性的,见记账 2。

#### 步骤 7 — 发现链(**产品路径,顺序不可跳**)

全部打 `Authorization: Bearer <REPOMESH_AGENT_ACTION_TOKEN>`;
`created_by_agent_id` 一律是 **organization leader**。

```text
POST /api/v1/issues                                   {requirement_text, created_by_agent_id, idempotency_key}
POST /api/v1/issues/{id}/discovery/analysis           {created_by_agent_id, idempotency_key}          # LLM
POST /api/v1/issues/{id}/discovery/candidates         {…, limit}                                      # 打目录评分
POST /api/v1/issues/{id}/discovery/classification     {…}                                             # LLM,每候选一次
POST /api/v1/issues/{id}/discovery/approval           {decided_by_agent_id, decision:"approved",
                                                       evidence_version, idempotency_key}
POST /api/v1/issues/{id}/discovery/plan               {…}                                             # LLM 集成
POST /api/v1/issues/{id}/discovery/materialize        {…}   ← 唯一会 reconcile + 采用外部 leader 的一步
```

要点:

- **进度真相是 `GET /api/v1/issues/{id}/discovery` 的 `step_state`(`idle`/`running`/`done`)
  + `running_task_id`**,不是 `…/discovery/tasks/{task_id}`(进程内、重启即失、只报进度)。
  同一 issue 有步骤在跑时其余步骤一律 409。
- `evidence_version` 取自该投影的**顶层** `classification_evidence_version`(不在
  `classification` 块里);对不上是 409。
- `project_id` **由 idempotency key 派生**,不能指定;拓扑由
  `CreateAutomaticProjectTopology` 从**长期 agent 目录**解析,因此会复用步骤 3 种的三个
  principal,并采用 controller 里既有的 Team。
- materialize 之后核 `SELECT decomposition_mode FROM project.repository_agent_teams;`
  应为 **`leader`**;任务表应**只有一条 leader 任务、无 worker 子任务**。

随后是自动的:leader Bridge 收 plan notice → GET 包 → codex 出计划 → POST `/plan` →
worker task 派发 → worker 自动接单 → 受治理执行 → leader 收 review notice → POST `/review`
(**当前会 500,D-M7-1**)。

#### 步骤 8 — 取证与拆环境

```bash
# 读模型(前端同源代理走的就是这些)
GET /api/v1/rooms/{room_id}/stream?limit=100     # room_id 需 urlencode(含 ! 与 :)
GET /api/v1/console/agents                       # runtime.kind = external/container/null
GET /api/v1/console/teams
GET /api/v1/issues/{project_id}/rooms

# 库内对账(只读)
SELECT decomposition_mode FROM project.repository_agent_teams;
SELECT id,status,assignee_agent_id,parent_task_id,result_summary FROM task_orchestration.tasks;
SELECT kind,status,subject,recipient_agent_id FROM collaboration.messages ORDER BY created_at;
SELECT room_id,count(*) FROM collaboration.room_timeline_messages GROUP BY room_id;

# 拆环境(顺序:Bridge → 后端 → 容器)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bridge-e1/stop_members.ps1 \
  -Members output/bridge-team/m7-live/members.m7.json \
  -PidDir output/bridge-team/m7-live/pids -Subset m7
powershell -NoProfile -Command "Stop-Process -Id <uvicorn 父>,<uvicorn 子> -Force"
docker rm -f repomesh-e2e-pg repomesh-controller-forwarder
```

⚠️ **`pkill -f` 杀不掉 nohup 起的 python**;uvicorn 是**父子 PID 对**,两个都要杀
(只杀子留孤儿,杀父连坐子)。

---

## 8. 编排与验证机制知识(对 dev-close §8 的增补)

1. **Docker socket 第 6 次损坏**,且出现**新变体**:进程完全没在跑时冷启动也会撞同一坏
   socket(UI 报 `removing stale socket`),不限于「进程在跑但引擎死」。修法不变
   (停进程 + 把 `%LOCALAPPDATA%\Docker\run` 整目录改名重建),20 秒生效。
2. **Windows 下 `python -m uvicorn` 是父子进程对**:只杀子进程会留孤儿父进程,
   杀父进程会连坐子进程。拆环境要按 PID 杀两个(本轮实证过一次误判)。
3. **Git Bash 与 Windows Python 的 `/tmp` 不是同一个目录**——`curl -o /tmp/x` 再用
   `.venv/Scripts/python.exe` 读 `/tmp/x` 会读空。取证脚本要么全用 python,要么用绝对路径。
4. **控制台 GBK 会写坏中文证据文件**:让脚本自己 `write_text(..., encoding='utf-8')`,
   不要靠 `tee` 接管道。
5. **发现链的进度真相是 `step_state` + `running_task_id`**(取值含 `idle` / `running` /
   `done`),不是 in-process 的 task registry(重启即失,且只报进度不报结果)。

**红线现状**:`src/repomesh_runner/**` 零改动保持;冻结契约(agent-bridge v1/v2、
leader-actions v1)本轮**零修改**(只作为夹具被消费);`room-observation.v1` 只收投影;
THINKING/协议帧/stderr/token 全程未入房、未入 tracked 文件。
