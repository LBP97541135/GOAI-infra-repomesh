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
git -C <repo> log --oneline -3     # 头仍是 44ebc91a,本轮没有代码提交
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

**活体环境仍在运行,没有拆**(§7)。

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
