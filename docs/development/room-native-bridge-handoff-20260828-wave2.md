# Room-Native Bridge 交接文档（波次 2 开发面收口）

> 日期:2026-08-28(深夜)
> 分支:`feat/room-native-agent-bridge`(main 之上 84 提交,未推送;头 `f2f6388a`)
> 状态:**波次 2 开发面全清——S-1 关闭(代码+稳态活体双证)、W-A2/W-B2 底座/W-B2b/W-C2 五单全部验收合入;PR 6/7/8/9 + Q2 尽在主分支**
> 上一份交接:`room-native-bridge-handoff-20260828-wave1.md`(仍有效;其 §5 停车现场已全部消化,其 §3 的 S-1 待裁项已了结)
> 工单台账权威:`room-native-bridge-final-acceptance/wave0-baseline-20260828.md`(每单的合入账、裁决与预检清单以它为准)
> 读者:零上下文接手本线的工程师或 agent 会话

---

## 0. 从这里开始(接手第一屏)

```bash
git -C <repo> log --oneline -20        # 本轮 20 个提交(5 单代码 + 6 docs)
git -C <repo> status --short           # M/?? 都是他线的,别动
git worktree list                      # 本轮新增 4 个已合并 worktree,见 §8

# 门禁(全量约 7.5 分钟)
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q  # 期望 2209 passed / 26 skipped
# 26 个 skip 里有 3 个是 PG 时间线测试:设 REPOMESH_TEST_POSTGRES_URL 指向
# 一次性 postgres 即转 passed(本轮已在合并头上实证 3 passed)

# 只跑本线快测(秒级)
.venv/Scripts/python.exe -m pytest tests/agent_bridge -q -m "not packaging"   # 515
```

**当前一句话**:终局验收所需的**全部开发件只剩 W-C3(PR 10+Q1+Q3a)一张单**;
M7(一 leader 一 worker smoke)与 M8(Room/UI 活体)的服务端与 Bridge 两侧代码都已齐,
两份活体预检清单已备好(§5)。

---

## 0.5 达成度对照(对 wave1 交接 §0.5 的增量)

| wave1 交接的结论 | 本轮变化 |
|---|---|
| AC-03 稳态路径被 S-1 挡住,FAIL | **已翻绿**:S-1 修复合入并经稳态活体取证 PASS(§2)——Bridge 全程在线、零人工、零 SpecificationNotFound |
| W-A2 已验收未合 | **已合入**(PR 7 完整状态机:plan/review/rework 三路全在主分支) |
| W-B2 接近完工未提交 | **PR 8 全部收官**:底座段+supervisor 集成段皆合入;leader Bridge 可从房间通知走完 planning/review 双流程(自动化) |
| W-C2 零改动 | **已合入**:RoomTimelineIngest 全链(AC-06 服务端半边)+ Q2 收窄(AC-04 伪汇报口子关闭) |
| 9 缺陷账 8 修 1 待裁 | **全清** |

尚未活体验证的:leader 轨全链(M7)、房间时间线到前端(M8)、六实例(E1)、真实交付(E0b/V2)。

---

## 1. 波次 2 五单合入账(执行模式不变:fable 主脑定界/裁决/验收 + opus 子代理工单制,逐单亲审 diff、独立复跑、cherry-pick/ff 合入、每次合入独立全量门禁)

| 工单 | 内容 | 合入提交 | 合入后门禁 |
|---|---|---|---|
| W-A2 | PR 7 完整状态机(leader plan 校验/clamp/幂等派发、review_due 快照证据、approve/rework/escalate 三路、迁移 0040) | `bc8f6410`/`7590070d`/`f9ea4fbf`(cherry-pick 自 A worktree,零冲突) | 2043/23 |
| W-S1 | S-1 派发/宣告拆分(§2) | `64da59f7` | 2053/23 |
| W-B2 底座 | PR 8 基础设施:v2 enrollment/binding 消费+**BINDING_PATH v1→v2 切换**(W-A1 记账闭环)、`LeaderActionPort`+HTTP/memory 双 adapter、协调会话(D-8 零工作区)、cli role 门 | `52577d76`/`64f36e3a`(ff) | 2147/23 |
| W-B2b | supervisor 集成 leader 轨:两种服务端通知的识别与双流程、幂等=「先 fetch 再决策」、房间文案纪律 | `bc455b1d` | 2178/23 |
| W-C2 | PR 9 RoomTimelineIngest(迁移 0039、record/list、身份反解、白名单、room_stream 合并去重)+ Q2 收窄(D-7) | `d83700dc`/`bb0d57f2`/`483e30dc`/`371f582e`/`793b0bf9` | **2209/26** |

迁移链尾现为 **0039**,全链 `0035→0036→0038→0037→0040→0039` 单头,已在一次性 PG 上实证
upgrade head 全通。W-B2 底座的停车现场快照保全在分支
`worktree-agent-a38e6b03ce68112fc`(两个 WIP 提交,审计凭据,勿动勿删)。

## 2. S-1 关闭详情(本轮最重要的一笔)

**缺陷**(wave1 交接 §3):Worker 任务的房间派活通知先于执行许可落库;在线 Bridge 收到通知
立即回调 start-worker-task,preflight 吃 `SpecificationNotFound`,按设计拒绝不重试,派活即丢。

**修法**(冻结裁决,合入 `64da59f7`):`TaskAssignmentGateway.assign` 增 keyword-only
`deliver: bool = True` + 新方法 `deliver_assignment(task_id)`(原始 assignment key 经既有
`TaskStore.assignment_key` 读回——publish 把 key 烧进任务包内容哈希,换 key 必撞冲突)。
**三处同型调用点**(decompose / leader plan 派发 / review rework)全部改为
「建行 → 写许可 → 宣告」三步序。A-10 重放语义保留并增强:重放=认 key 返既有行→许可幂等
补齐→重跑投递。scm/CI-rework 与 leader task 派发走默认 `deliver=True` 零感知。

**测试方法论(本轮沉淀,写进了测试本体)**:排序类不变式必须在**接收方回调时刻**断言——
collaboration fake 的 `send` 里当场回查许可是否已批(等价在线 Bridge 的 preflight),
录音机式事后计数会眼睁睁放过此缺陷;另做了反证(摘掉三处 `deliver=False` 复跑 6 条红)。

**稳态活体取证 PASS**(证据 `output/bridge-team/s1-steady-evidence/`,13 件):
- Bridge 在线 2 分 37 秒后才派活;单实例、sync cursor 单调、零重启;
- 零人工 UUID(唯一人工动作是 materialize,当时 task id 尚不存在);
- 两侧日志 `SpecificationNotFound` **零命中**(grep 命令与空输出存档);
- **同一请求内 DB 时钟直录:许可 13:28:30.260 → 房间消息 13:28:30.449,许可先行 189ms**
  ——顺带了结「事务边界」残余疑虑(真库上许可先于通告可见);
- 超出判据:任务全链走到 succeeded,真提交 `b3784bae`,仓库测试 exit 0,环境按 PID 拆净。

**AC-03 的稳态口径自此成立**(此前只有「Bridge 重启期间派活、cursor 续读」旁路取证)。

## 3. 本轮关键裁决(细账在台账)

1. **W-B2b/跨重启防双跑**:不建本地 state 表,改「先 `fetch_assignment` 再决策」——
   RepoMesh 的 `phase` 就是这轮已决与否的持久真相,本地表反而可能分叉;免 SCHEMA_VERSION
   升版。陈旧通知只花一次 GET、零 codex 会话、零 POST。
2. **W-B2b/畸形草稿房间文案**:固定句+详情进日志。模型产出(node id/path/assignee)不是
   平台笔迹不得逐字入房;RepoMesh 自己的 refusal 照旧逐字——与 PR 5 白名单纪律同构。
3. **W-B2b/fixture 反漂移升级**:两种 leader 通知正文**不是逐字抄写**,而是测试导入时跑一遍
   服务端真实 leader-mode 轮次取回原始 body——服务端改措辞自动跟随、改路由必红。
4. **W-C2/六项**:幂等键单一来源(event_id 即键,不设第二形参)|回放不重解析身份(如实未知
   不得事后改口)|D-7 检查前置于身份校验(路对谁都关;防伪造上报 raise 致 poller 整批无限
   重试)|审计复用 `platform.audit_events`+`processed_matrix_events` 去重,**审计行记 raw
   matrix id**(未经验证的自称不得记为行为人)|`origin_server_ts` 必填、非法丢弃|
   死代码当场自删。
5. **W-S1/deliver_assignment 不带 key 形参**:内部经 `assignment_key(task_id)` 读回,
   杜绝调用方传错 key 撞内容哈希。

## 4. 门禁演进(每次合入独立全量,全绿)

| 时点 | 计数 |
|---|---|
| wave1 交接头 `adbdf789` | 1977 / 23 |
| W-A2 合入 | 2043 / 23 |
| W-S1 合入 | 2053 / 23 |
| W-B2 底座合入 | 2147 / 23 |
| W-B2b 合入 | 2178 / 23 |
| W-C2 合入(本交接头) | **2209 / 26,exit 0**(+3 skip=PG 时间线测试,合并头上一次性 PG 实证 3 passed;分项 bridge 515、contracts 260+) |

## 5. M7 / M8 活体预检清单(两子代理产出、主脑确认;活体开跑前逐条核)

### M7(一 leader 一 worker 真机 smoke;需 E1 子集)

1. **头号未知:唤醒是否发生**——`integrations/agentteams/matrix.py:129-150` 只在
   `recipient_resource_name` 非空且 messenger 带 `control_plane` 时写 `m.mentions`;
   不带则 `_mentions_me` 判 False,leader 轨「活着但沉默」。**第一步先抓一条真实 DM 事件
   核实 m.mentions**。
2. 两个 leader POST(`/plan`、`/review`)从未对真实路由跑过(Bridge 侧只对 MockTransport
   验过);已开后台任务卡补 in-process ASGI 用例。
3. leader enrollment 凭据只认 `env:` locator;`credentialRefs.repomesh` 放 **external
   member token**(D-6),不是全局 runner token。
4. leader DM 房必须同时在 enrollment 与 binding v2 的 `allowedRoomIds`,否则 stage 2 拒启动。
5. M7 必须一 leader + 一**能真执行**的 worker——review_due 通知要 worker 全终态才发。
6. 真 codex 不肯只吐 JSON 时走「拒绝草稿」路径:房间一条 note、零 POST、round 停 planning,
   人再 @ 一次重来——设计预期,白花一轮会话而已。

### M8(人在 Matrix 发话 → 约 10 秒内 Room 页可见)

1. **先 `alembic upgrade head`**——0039 未应用则 ingest 全 500,Room 页照旧空白。
2. **RepoMesh 服务端自己的 Matrix 账号必须已 join 目标房间**——`/sync` 只回 `rooms.join`,
   仅被 invite 不产生 timeline 事件。这是最可能挡 M8 的一条。
3. 拓扑 `room_id`/`leader_room_id` 必须已回写——白名单全来自拓扑,空房间号=消息按未授权丢弃。
4. 人类发送者显示为 raw matrix id(如 `@bohan:matrix.local`)是 D-4 设计,不是 bug。
5. `REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN` 有效——否则 matrix_client 为 None,poller 与
   recorder 都不组装,read model 静默只显出站消息。

## 6. 记账(不阻塞,接手须知)

| # | 事项 |
|---|---|
| 1 | `room_stream` 录制侧单房间 1000 条隐性截断(`container.py` 常量有 docstring);真解=跨源游标合并,未做 |
| 2 | 控制面不可达时 timeline 摄取停摆并刷错(resolver 抛→poller 整批重试;刻意,与 verifier 依赖形态一致) |
| 3 | 前端 `CollaborationMessageView.sender_agent_id` TS 类型应收窄 `string \| null` → **归 W-C3** |
| 4 | 出站 `_message_item` 硬编码 `direction: "leader_to_worker"`(既有,未动) |
| 5 | run 终态记录里 codex 自述与权威 testResults 两个声音并存(受限子进程 6 键 PATH vs Runner 验证是两个主体;缺口 P 同族,待 P 立项一并处理) |
| 6 | W-B2b 的 900s 超时覆盖整轮(fetch+codex+submit);超时卡在 submit 时语义闭合但白花一轮会话 |
| 7 | `RepoMeshGovernedTaskAdapter` 至今无组合根关闭(既有小缺口;leader 侧 adapter 的 close 本轮已接) |
| 8 | leader Bridge 落入 `start task`/dispatch 分支回 `GOVERNANCE_DISABLED_NOTE`(既有行为) |
| 9 | 后台任务卡 `task_474c1900`:leader 双 POST 的 in-process ASGI 契约用例(用户点卡即开单) |
| 10 | wave1 记账继续有效:401-vs-503 两面答法不一、to_wire 截断、fileChange 别名只覆盖观测形状等 |

## 7. 下一步(按此顺序)

1. **W-C3(PR 10 + Q1 + Q3a)——最后一张开发单**:External 运行形态展示改动链
   (`sources.py`/`container.py`/`service.py`/`display.ts`/RuntimeBadge/TeamsPage,主计划 §2.3)
   + Q1 `DeliveryTraceability`(D-9,两条 PR 路径共用正文生成器)+ Q3a mock 零触达静态审计
   + 记账 3 的 TS 类型收窄。前端验证定式:浏览器实走 + `tsc -b` + oxlint(`tsc --noEmit`
   是空转桩不作数)。
2. **E1(环境轨,可与 W-C3 并行)**:六身份开通(3 Leader+3 Worker,`containerManaged:false`)、
   六份 enrollment、auth.json 复制(D-10)、PowerShell 启停脚本(按 PID 收尾);先做一 leader
   一 worker 子集供 M7。Materialize 前六 binding 只读预检(R8)。
3. **波次 3 串行收口(独占活体队列)**:M8(Room/UI)→ M7(leader/worker smoke,先核 §5 清单)
   → E1 六实例 soak → E0b(隔离环境短开 delivery,三仓白名单)→ **V2/Q3b**(六前置显式核验,
   门禁 #10;PASS 结论用验收标准 §8 推荐原文;取证后立即关 delivery)。

## 8. 环境、凭据与现场

- 凭据指针不变:`output/bridge-team/e0a-live-env.md`(gitignored)+ `secrets/`;
  **任何 token 不入 tracked 文件不入报告**。controller/appservice 凭据取法见 PR 4 交接 §7.5。
- 活体证据:`output/bridge-team/v1-evidence/`(68 件)+ **`s1-steady-evidence/`(13 件,本轮新增)**;
  现场 `D:/Project4work/.repomesh-v1-live/`(夹具仓+workspaces)。
- 环境三坑不变:`MSYS_NO_PATHCONV=1`;控制面与 Bridge 同跑 Windows 宿主;5432 活体库不碰。
  拆环境按 PID(`pkill -f` 杀不掉 nohup 链)。他线端口 5432/55432/8080/3000/5280/8100 未碰。
- 本轮 worktree 账:`s1-dispatch-split`/`wb2-supervisor`/`wc2-room-timeline`(主脑亲手建,
  全部已合并,可删可留);`agent-ac465c0fee699f4c1`(W-A2)与 `agent-a0eb6ff2878b836cf`
  (旧 W-C2 停车,零改动)已过时可清理;`agent-a38e6b03ce68112fc` 上的两个停车快照提交**保留**。
- PG 时间线测试开关:`REPOMESH_TEST_POSTGRES_URL`(注意不是 `REPOMESH_TEST_DATABASE_URL`)。

## 9. 编排机制知识(继续多代理施工必读;对 wave1 §9 的增补)

1. **worktree 陈旧基线第 4 次复发**:Agent 工具自动建的 worktree 又被切在 `f3d343b0`。
   **最稳处方=主脑亲手 `git worktree add <path> -b <branch> <baseline>`**,把路径写进工单;
   子代理第一步仍必须核对基线头。
2. **子代理死于 API 中断时,先看盘面再决定续做方式**:W-B2 底座代理死在最后验证一步,
   但交付分支已收好、工作区干净——直接主脑接手验证合入,比盲目 resume 省一轮。
3. **停车现场恢复定式**:先把现场快照成 WIP 提交保全(原分支留作审计)→ 迁基线(checkout -b
   新分支 + cherry-pick 快照)→ 解冲突 → 接管理解 → reset --soft 重分逻辑提交。
4. **并行双单的前提是文件面零交集**:本轮 W-B2b(纯 Bridge)与 W-C2(纯服务端)真并行;
   工单里互相写明对方在改什么、不许越界。
5. **排序类不变式的测试要在接收方回调时刻断言**(§2 方法论);**反证法**(临时摘修复跑红)
   应作为验收步骤。
6. **合入方式选择**:交付分支恰好基于当前头→ff 合入保留原提交;有落后→cherry-pick。
   两者都要求合入后独立全量门禁。

**红线现状**:`src/repomesh_runner/**` 零改动保持;冻结契约(agent-bridge v1/v2、
leader-actions v1)整个波次 2 **零修改**;`room-observation.v1` 只收投影;THINKING/协议帧/
stderr/token 永不入房。supervisor.py 的「禁区」已按计划开启并随 PR 8 收官关闭。
