# Room-Native Bridge 交接文档（开发面终局收口）

> 日期:2026-08-28
> 分支:`feat/room-native-agent-bridge`(main 之上 94 提交,未推送;头 `2d42c778`)
> 状态:**终局验收所需的全部开发件 + E1 脚本轨全部收口——W-C3(PR 10+Q1+Q3a)与 W-E1
> 已验收合入;波次 0–2 就此全清,只剩波次 3 活体串行队列**
> 上一份交接:`room-native-bridge-handoff-20260828-wave2.md`(仍有效;其 §5 的 M7/M8
> 活体预检清单与 §8 环境指针继续是权威,本文不重抄)
> 工单台账权威:`room-native-bridge-final-acceptance/wave0-baseline-20260828.md`
> (W-C3/W-E1 的合入账、裁决与界外发现细账以它为准)
> 读者:零上下文接手本线的工程师或 agent 会话

---

## 0. 从这里开始(接手第一屏)

```bash
git -C <repo> log --oneline -12          # 本轮 10 个提交(7 代码/契约 + 3 docs)
git -C <repo> status --short             # M/?? 都是他线的,别动
git worktree list                        # 本轮新增 3 个已合并 worktree,见 §7

# 门禁(全量约 8 分钟)
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q    # 期望 2213 passed / 26 skipped
# 3 个 skip 是 PG 时间线测试:设 REPOMESH_TEST_POSTGRES_URL 指一次性 postgres 即转 passed

# 前端定式(本项目铁律:tsc --noEmit 是空转桩,不作数)
cd frontend && npx tsc -b --force && npx oxlint src
```

**当前一句话**:代码侧已无事可做。下一个动作是搭活体环境跑 **M8**,随后按独占队列
M8 → M7 → E1 六实例 soak → E0b → V2/Q3b 串行收口(主计划门禁 #10:V2 前六前置显式核验)。

---

## 0.5 达成度对照(对 wave2 交接 §0.5 的增量)

| wave2 交接的结论 | 本轮变化 |
|---|---|
| W-C3 是最后一张开发单,排队 | **已合入**:PR 10(external 运行形态)+ Q1(交付追溯)+ Q3a(mock 零触达审计)+ sender_agent_id TS 收窄,全在主分支 |
| E1 排队等部署 | **脚本轨已合入**(`scripts/bridge-e1/` 12 文件,11 步 runbook);真开通排波次 3 |
| AC-06 前端半边(PR 10)缺 | 代码面closed:external 成员显示 `External`(kraft 色),永不显示 Pending/容器词汇;replay 夹具浏览器实走截图核过 |
| 交付证据第 9/10 条(追溯)偏薄 | 主路径 PR 正文九行全出(issue/change_set/plan/repository/task/run/worker_agent/branch/commit);两条路径共用一个渲染器,测试逐字节钉死 |
| D-mock 未确认 | Q3a 审计:**零触达成立**,取证条目 E-1~E-7 已备好进 V2 清单 |

尚未活体验证的(与 wave2 相同,一项未减):leader 轨全链(M7)、房间时间线到前端(M8)、
六实例(E1)、真实交付(E0b/V2)。**PR 10 的活体复核并入 M8**(replay 夹具 ≠ 活体)。

---

## 1. 本轮三单合入账(执行模式不变:fable 主脑定界/裁决/验收 + opus 子代理工单制,三线并行、文件面零交集,逐单亲审 diff、独立复跑、ff/cherry-pick 合入、每次合入独立全量门禁)

| 工单 | 内容 | 合入提交 | 合入后门禁 |
|---|---|---|---|
| W-C3a | PR 10:RuntimeSnapshot 增 `container_managed` 三值、`_agent_runtime_fields` 三分支、前端 external 分支与 RuntimeBadge/AgentsPage;`CollaborationMessageView.sender_agent_id` 收窄 `string\|null` | `050759df`/`f8833111`/`12f7e6e1`(ff)+ 契约追认 `5c82310f`(主脑补笔) | 2210/26 |
| W-C3b | Q1:`DeliveryTraceability` + `render_delivery_pull_request_body` 落 delivery contracts,两条 PR 路径共渲染器;Q3a:tracked 审计文档 | `ee0e423d`/`e31f98a3`(cherry-pick) | 2213/26 |
| W-E1 | `scripts/bridge-e1/` 全套(seed/provision/matrix token/enrollment/auth 复制/启停/R8 预检 + runbook)+ 主脑裁决改笔 | `bfb82bbe`/`953f5b4f`(cherry-pick)+ `3bffbe55` | 2213/26(纯脚本) |

台账收口提交 `2d42c778`。迁移链**未动**,链尾仍 0039(Q1 走无迁移方案,见 §2)。

## 2. 本轮裁决(均已冻结;前两条是对主计划的申报偏差,翻案成本一行)

1. **W-C3-D1 —— `coding_profile` 不加,前端文案 `External` 不写 Codex**。主计划 §2.3 说
   「RuntimeSnapshot 增 coding_profile,probe adapter 透传」,但当日核查 controller 的
   `WorkerResponse`(vendored `resource_handler.go` `workerToResponse`)**没有任何
   coding-CLI 字段**——「透传」无源;enrollment 的 `codingProfile` 是 Bridge 侧文件,
   服务端不经手。硬写 "Codex" 即无源编造,AC-06 明写「或等价文案」。
2. **W-C3-D2 —— Teams 成员芯片不加 external 标记**。`list_teams` 只探测 team 资源,
   per-member 托管方式在该页无诚实数据源;Agents 页(逐 agent 探测)是运行形态的
   roster of record。
3. **W-C3-D3 —— Q1 无迁移**。`RepositoryCandidateInput` 与 delivery 持久化零改动;
   主路径(finalizer 持 plan+task view)八 id 全出,reconciler 路径以已持久化字段如实
   渲染,拿不到的 plan/run/worker 三行**如实缺席**(`- run: unknown` 那样的行读起来像
   记录过的事实,是零证据)。
4. **W-E1-D2 —— Team 只由 materialize 正式 reconcile 建立/采用**,`agt create team`
   旁路已从 runbook 删除(验收标准 §5 禁脚本代业务步骤;手工建团撞 reconcile 逐字段
   奇偶性=409)。**R8 预检因此是两段论**:materialize 前只核 controller 半边(资源在/
   名字逐字符合/containerManaged:false/skills),binding 半边预期「不属于 Team」失败
   属知情;materialize 后再跑一次,六行全绿才算 R8 过。
5. 附带裁决:六成员 RepoMesh token 铸发**维持人工**(R2 先例;脚本重跑会把后端
   `REPOMESH_RUNNER_WORKER_TOKENS` 改哑);F-1(见 §6)不入本线,独立任务卡。

## 3. wire 与正文形状变化(下游消费者须知)

1. **`/console/agents` runtime 块新增 `kind: "container" | "external" | null`**
   (契约 v0.2 §4.3/§4.4 已追认,`5c82310f`):controller 确认 `containerManaged` 为
   True→`container`、False→`external`、这次探测没问(manager/team)→`null`。
   **`external` 时 `phase` 与 `runtime_kind` 恒 null**——controller 对永不容器化的成员
   照样回默认 `Pending`,转述即谎报(AC-06 FAIL 条款);反谎言测试钉死
   (`tests/api/read_models/test_grid.py`)。
2. **交付 PR 正文换形**:两条路径(`plan_delivery.py` finalizer / `delivery.py`
   reconciler)共用 `render_delivery_pull_request_body`,同 label 同格式;主路径九行、
   reconciler 六行+省略说明。钉死测试在 `tests/contracts/test_delivery_traceability.py`。
   子代理超工单修掉一处真缺陷:`_backfill_sibling_links` 重写 ChangeSet 内**全部**已
   发布 PR 的正文,批次 2 回填会把批次 1 PR 的 run/worker 行冲掉——已补
   `_delivered_provenance`(取所有已交付批次的 provenance)并测试钉死。
3. `CollaborationMessageView.sender_agent_id` 前端类型已收窄 `string | null`;
   RoomView 对双 null 显「发送者未解析」。

## 4. 门禁演进(每次合入独立全量,全绿)

| 时点 | 计数 |
|---|---|
| wave2 交接头 `e695eeb0` | 2209 / 26 |
| W-C3a 合入 | 2210 / 26(+1 反谎言测试) |
| W-C3b 合入 | 2213 / 26(+3 追溯测试) |
| W-E1 合入(本交接头 `2d42c778`) | **2213 / 26,exit 0**;前端 `tsc -b` + oxlint 过 |

## 5. 下一步 = 波次 3 活体串行(独占活体队列,顺序不可换)

```text
M8(Room/UI) → M7(一 leader 一 worker smoke) → E1 六实例 soak → E0b(短开 delivery) → V2/Q3b
```

- **预检清单不重抄**:M7/M8 逐条清单在 wave2 交接 §5(M8 头号风险=RepoMesh 服务端
  Matrix 账号必须已 join 目标房间;M7 头号未知=leader DM 事件是否带 `m.mentions`,
  第一步先抓一条真实 DM 核实)。
- **E1 操作全按 `scripts/bridge-e1/README.md`**(11 步 runbook):三个排期硬事实——
  ①binding GET 要求成员已属 Team,所以「六个 PUT → materialize 建团 → 六个 GET」
  必须隔着建团;②materialize 不铸名,**预建资源名必须逐字等于 principal 名**
  (三套既有铸法与推导例在 runbook §4);③auth.json 必须在首次 `ensure_ready`
  **之前**放进 codex-home(之后放的文件停在 Medium 完整性,受限子进程写不了)。
  m7 子集(-Subset m7)先行供 M7。
- **V2 开跑前**:门禁 #10 六前置显式核验(M7/PR 9/PR 10/Q/E1/E0b);Q3b 运行证据用
  Q3a 审计文档 §5 的 E-1~E-7 只读命令当场取;PASS 结论用验收标准 §8 推荐原文;
  三个 Draft PR 取证后**立即关 delivery**。

## 6. 记账(不阻塞,接手须知)

| # | 事项 |
|---|---|
| 1 | **F-1(Q3a 审计发现):`POST /api/v1/coding-runs/mock` 无鉴权**——任何能访问 API 的人可伪造一条 coding run 记录(进程内 mock,非代码执行)。已开独立任务卡(task_082d8b72);不阻断 Q3a(零触达靠「无调用方」+E-1 日志正面取证) |
| 2 | leader 双 POST(`/plan`、`/review`)仍未对真实路由跑过——wave2 记账 9 的后台任务卡(task_474c1900,in-process ASGI 契约用例)仍开着,**M7 前补上更稳** |
| 3 | `recipient_agent_id` 有与 sender_agent_id 同类的类型谎言(时间线投影给 None,前端契约仍写 `string`) |
| 4 | 服务端时间线投影输出 `sender_matrix_user_id`,前端 `CollaborationMessageView` 无此字段——诚实的 raw handle 前端够不着 |
| 5 | `RUNTIME_SKIN.external` 生产暂无渲染者(RuntimeBadge 只被 TeamsPage 用,teams 块不带 kind)——`Record` 完整性强制条目,非死代码,知情即可 |
| 6 | E1 待选:把哪个成员的 agentId 钉成已登录 codex 的 UUID(省一次 login,PR 4 §7.5 手法)——M7 激活时再定 |
| 7 | `members.example.json` 用 `repo-<hex12>-*` 铸法;真 roster 换 id 时**重算并确认三仓 hex[:12] 不撞**(撞了三仓共名) |
| 8 | `start_members.ps1` 的 `-Python` 默认指主仓 venv;在 worktree 里跑必须显式传 `-Python` |
| 9 | wave2 记账 1–10 继续有效(room_stream 1000 条截断、900s 超时覆盖整轮、direction 硬编码等) |

## 7. 环境、凭据与现场

- 凭据指针不变:`output/bridge-team/e0a-live-env.md`(gitignored)+ `secrets/`;
  controller/appservice 凭据取法见 PR 4 交接 §7.5;**任何 token 不入 tracked 文件**。
- 本轮证据:`output/bridge-team/wc3a-evidence/`(Agents 页 External 行 + Teams 页
  不变两张截图,已从 worktree 拷回主树)。施工记录
  `output/bridge-team/015-mainsession-wc3-e1-tickets.md`(gitignored)。
- 环境三坑不变:`MSYS_NO_PATHCONV=1`;控制面与 Bridge 同跑 Windows 宿主;5432 活体库
  不碰。拆环境按 PID(`pkill -f` 杀不掉 nohup 链)。他线端口 5432/55432/8080/3000/5280/8100 勿动。
- 本轮 worktree 账:`wc3-pr10`/`wc3-q1`/`we1-scripts`(主脑亲手建于 e695eeb0,
  **全部已合并,可删可留**)。wave2 的账继续有效(`agent-a38e6b03ce68112fc` 两个停车
  快照提交保留)。

## 8. 编排机制知识(对 wave2 §9 的增补)

1. **worktree + 共享 venv 的新坑(本轮首次踩明)**:主仓 venv 是 editable 安装,
   `__editable__.repomesh-0.1.0.pth` **绝对路径指向主树 `src`**——在 worktree 里裸跑
   `python -m pytest` 测到的是**主树代码**。处方=工单里写死
   `PYTHONPATH=<worktree>/src` 前缀;三个子代理照做,全部有效。
2. worktree 前端无 node_modules:子代理 `npm ci` 后再走 `tsc -b`/oxlint/vite 定式,可行。
3. 浏览器实走的登录门:前端即使 `?source=replay` 也硬要 `/api/v1/auth/me`——子代理用
   scratchpad 一次性 stub 只答这一条路由,花名册数据仍全来自夹具,零仓库改动。可复用。
4. **子代理的范围内自由裁量要逐条过目再收**:本轮两例都对(夹具让 leader-core 转正
   external 与 teams 夹具自洽;`_delivered_provenance` 补回填丢行),但「超工单发现并
   修掉缺陷」必须在报告里自报 + 测试钉死才收,默认仍是停工上报。
5. 主脑补笔的定位:契约文本追认(`5c82310f`)与 runbook 裁决改笔(`3bffbe55`)这类
   **裁决性小改**由主脑直接落,不打回子代理——打回的成本高于亲手写,且裁决本就归主脑。

**红线现状**:`src/repomesh_runner/**` 零改动保持;冻结契约(agent-bridge v1/v2、
leader-actions v1)本轮零修改(v0.2 读模型契约的 `kind` 是**追认**新投影字段,不触冻结
schema);`room-observation.v1` 只收投影;THINKING/协议帧/stderr/token 永不入房。
