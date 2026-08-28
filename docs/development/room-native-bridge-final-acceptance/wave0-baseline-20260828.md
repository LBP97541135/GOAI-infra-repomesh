# 波次 0 — tracked 契约基线台账

> 日期:2026-08-28
> 依据:[并行编排计划 §2 波次 0](room-native-bridge-parallel-orchestration-plan-20260828.md)
> 出口判据:任一工作树从 baseline commit 检出后,能运行同一组契约 fixture/测试(见 §3)
> baseline commit:本文所在提交的 head(工单开工时以 `git log -1` 记录具体 hash)

## 1. 冻结产物清单(五项对账)

| # | 波次 0 冻结项 | 落点 |
|---|---|---|
| 1 | enrollment/binding v2(增 `role`)+ v1/v2 round-trip 与 role/room 错误 fixture | `contracts/agent-bridge/v2/`(schema ×2 + README + fixtures ×6)+ `tests/contracts/test_agent_bridge_v2_contract.py` |
| 2 | leader agent-actions HTTP 契约:三文档 wire 形状、结构化错误体、错误矩阵(401/403/404/409/200) | `contracts/leader-actions/v1/`(schema ×6 + README + fixtures ×14) |
| 3 | leader interface 不变量:phase/evidence 耦合、幂等重复响应、DAG 无环/覆盖、evidence 必备字段、rework revision | `contracts/leader-actions/v1/README.md` §Frozen invariants(规范文本)+ `tests/contracts/test_leader_actions_v1_contract.py`(可执行钉死) |
| 4 | team `decomposition_mode` view/reader contract(PR 5.5B 产、PR 7 消费) | `src/repomesh/modules/project/contracts.py`(`TeamDecompositionMode` + `RepositoryTeamView.decomposition_mode` 默认 `server` + `TeamDecompositionModeReader`)+ `tests/contracts/test_project_decomposition_mode_contract.py` |
| 5 | migration 预留唯一 revision id ×3 | 本文 §2 |

## 2. 迁移 revision 预留(合并串行点,非开发串行点)

按 integration owner 关键路径合并次序 `PR 5.5B → PR 7 → PR 9` 分配顺序号
(主计划 PR 9 表格里写的 `20260828_0037_room_timeline` 以本表为准修正——
该处编号写于编排校正前,若沿用会与 PR 5.5B 抢号):

| revision id | 归属 | 内容 |
|---|---|---|
| `20260828_0037`(文件 `20260828_0037_team_decomposition_mode.py`) | PR 5.5B | project topology 持久化 `decomposition_mode` |
| `20260828_0038`(文件 `20260828_0038_leader_decision.py`) | PR 7 | leader plan provenance / DAG / review phase·revision·findings |
| `20260828_0039`(文件 `20260828_0039_room_timeline.py`) | PR 9 | `room_timeline_messages` |
| `20260828_0040`(W-B1 验收时追加预留) | W-A2(PR 7 完整状态机) | leader plan provenance / DAG / review revision / findings(0038 只建了本切片写的 `leader_assignments`) |

规则(编排计划 §4):topic branch 开发期 `down_revision` 一律指向自己检出时的链尾
(当前 `20260827_0036`),各自独立可测;合并时 integration owner 只重写 `down_revision`
串成单 head 并复跑 `alembic upgrade head` 检查。禁止临时抢号、禁止改用其他 id。

## 3. 出口判据的执行方式

```bash
.venv/Scripts/python.exe -m pytest \
  tests/contracts/test_agent_bridge_v2_contract.py \
  tests/contracts/test_leader_actions_v1_contract.py \
  tests/contracts/test_project_decomposition_mode_contract.py \
  tests/contracts/test_agent_bridge_v1_contract.py -q
```

四个文件全绿 = 契约基线可用;`test_agent_bridge_v1_contract.py` 在列是 v1 冻结未被
触碰的回归证明。双端(PR 5.5A/PR 7 服务端、PR 8 Bridge 端)测试必须直接消费
`contracts/**/fixtures/` 里的文件,不得手抄副本。

## 4. 工单台账(抄自编排计划 §7,开工时在此记录状态与 baseline hash)

```text
W-0   基线提交      [波次0·主脑] 状态: 本提交
W-A1  PR 5.5A+B    [波次1·A线]  5.5A 段已验收合入: baseline e3eec185, 合入 41b5decb
      v2 路由: GET /api/v1/runtime/v2/external-members/{id}/binding?role=… + PUT 同前缀;
      role 必填 query、真相源=directory、org leader 双 409;并修了 R0 一格之遥的缺陷
      (ExternalWorkerProjection 硬编码 worker skills → leader 必 409)。记账风险:
      ①leaderName 为空时 leader 身份守卫 fail-open;②skills 修复未经真机,E1 做只读核验;
      ③PR 8 侧 BINDING_PATH 仍 v1 硬编码 + enrollment v2 解析全归 PR 8。
      5.5B 段已验收合入: 8f706382/71a74756/0b89f959(adoption 一读一决、domain 单向闩锁、
      迁移 0037 down_revision=0038、前端只标 Leader 自拆)。记账: E1 时对 containerManaged
      读取路径与 leader skills 做真机只读核验(两条并一次)。W-A1 全单收口;A 线转 W-A2
W-B1  PR 7 核心纵切 [波次1·B线]  状态: 已验收合入  baseline: e3eec185  合入: 91b2e85e/492e715f/2b882089
      已裁决偏差: ①malformed REPOMESH_RUNNER_WORKER_TOKENS 在 leader 面答 401+ERROR 日志
      (冻结 enum 无 server-fault 码;与 agent_runtime 的 503 不一致,W-A2 重审);
      ②to_wire 对超 maxLength 的 title/workerName 截断(有损,记账)
W-C1  V1→PR6       [波次1·C线]  V1 段 **PASS**(08-28,run 2c00225e:三处对账全过+治理活着 15 allow/3 deny)
      过程揪出并修掉六个缺陷(全在主分支): 234222cc(C-2 词表翻译+C-4 标签门禁+C-5 决策日志)、
      57f93532/e38bd5df(C-3 governed codex-home 配置)、6fd4e1f1(C-7 剥「=workspace 根」的 cwd)、
      cd81e691(C-5 迭代)、b1e00c21(**C-8a 红线窄口**: runner 审批应答词表 approved/denied/abort
      →accept/decline/cancel,活体捕获为证,tests/runner 246→255;既有 4 个钉死旧词表的断言字面量
      被迫同步,已注明来源)。证据 output/bridge-team/v1-evidence/(38 件,gitignored)。
      遗留记账: ①fileChange 审批按名恒拒(_approval_tool_name 回退 method 串;本轮 codex 自行
      回退 shell 不阻断;C-9 候选=Bridge 词表翻译追加该 method 名,路径钳制由叶子行走保留);
      ②runner 通用策略把 cwd 当写目标(runner 线真缺陷,Bridge 已 C-7 绕过);③obs-1 坏 config
      锁死启动序/obs-2 config.toml 整写抹 codex 自写键/obs-3 房间指路日志无日志 → PR 6 候选;
      ④D-2 实测已非阻断(相对路径 shell 读 context 被 allow)。
      **PR 6 段已验收(08-28,合入 91c92080+314db5cf):AC-03 四条全过**(run 215216df:
      无人输 UUID、标准通知触发、平台复核双向实拍、真相走事件通道;三处对账 A/B/C 全 PASS、
      8 审批全评估;state 证据证明不双跑)。附属批 C-9/obs-1/obs-2/obs-3 全落。
      **活体新揪缺陷 S-1(服务端,未修待裁)**:平台先投派活通知后写执行许可
      (application.py:728 assign 先于 :746 _ensure_specification),Bridge 在线时自动接单
      必落窗口内 → SpecificationNotFound 拒绝且按设计不重试,该派活即丢;两轮独立复现,
      两 task 停 blocked 而 spec 事后均 frozen。本轮 AC-03 经真实运维场景(Bridge 重启期间
      派活、cursor 续读)取证;**稳态路径(Bridge 在线)在 S-1 修复前 FAIL**。
      修法方向:拆开「派发」与「宣告」(spec 以 task 为键,非简单换序)。
      另两条小观察:夹具无 .gitignore 时 __pycache__ 入 commit(门禁行为正确);
      materialize 幂等重放不重发房间消息(与 A-10 注释描述层次不同,知情即可)
W-A2  PR 7 完整集成 [波次2·A线]  状态: **已验收合入(08-28)**  baseline: b016f058(A worktree)
      合入: bc8f6410/7590070d/f9ea4fbf(cherry-pick 自 c43dfecd/b74f47fc/33596046,零冲突;
      与中间的 PR 6 两提交文件面零交集)。迁移 0040 down=0037 无需改,
      链 0036→0038→0037→0040 单头已核。合入门禁: ruff 干净 + 全量 **2043/23 exit 0**
      (前基线 1977/23,+66 全为本单新测试,skip 未增)。
      已裁决偏差(验收时定,沿 wave1 交接 §5): planRevision 恒 1、rework 回执
      in_progress 不动行、许可回落包络根、save 无版本列
W-S1  S-1 派发/宣告拆分 [插单·主脑裁决 08-28] 状态: **已关闭(08-28,代码+活体双证)**
      裁决与实现: TaskAssignmentGateway.assign 增 keyword-only deliver=True +
      新 deliver_assignment(task_id)(原 key 经 assignment_key 读回);三处调用点
      (decompose/leader plan/rework)改「建行→写许可→宣告」三步序;A-10 重放语义保留。
      合入 `64da59f7`(cherry-pick 自 feat/s1-dispatch-split 的 03139349);
      门禁 ruff 干净 + 全量 **2053/23 exit 0**(+10 为排序不变式新测试,含反证:
      摘掉三处 deliver=False 后 6 条红)。测试关键设计: collaboration fake 在发送时刻
      回查许可(等价在线 Bridge preflight),录音机式断言会放过此缺陷。
      **稳态活体取证 PASS**(证据 output/bridge-team/s1-steady-evidence/,13 件):
      Bridge 在线 2m37s 后派活、单实例 sync cursor 单调、零人工 UUID、两侧
      SpecificationNotFound 零命中、start-worker-task 202、run accepted→completed、
      task succeeded 真提交 b3784bae;**同一请求 DB 时钟直录许可(13:28:30.260)先于
      房间消息(13:28:30.449)189ms**——事务边界残余风险一并了结(真库上许可先于通告可见)。
      AC-03 稳态口径自此成立。
      新记账(不阻塞,缺口 P 同族): run 终态记录里 codex 自述「PATH 无 python 验证被阻断」
      与权威 testResults exit 0 并存——受限子进程 6 键 PATH 与 Runner 验证是两个主体,
      房间转述权威侧,但 agent 反话随同一条记录保留;待 P 立项时一并处理
W-B2  PR 8 底座     [波次2·B线]  状态: **底座段已验收合入(08-28)**;第二段(supervisor 集成)待开单
      从停车现场恢复: 快照保全在原分支 worktree-agent-a38e6b03ce68112fc(两 WIP 提交,
      审计凭据勿动),迁基线 cb58449b 后收成两个逻辑提交 ff 合入:
      `52577d76`(v2 enrollment/binding 消费 + BINDING_PATH v1→v2 兄弟路由切换——
      W-A1 记账项就此闭环 + test_member_v2 六 fixture 直接消费)、
      `64f36e3a`(LeaderActionPort 三方法无重试 + HTTP/memory 双 adapter + 协调会话
      D-8 零工作区 + cli role 门「leader 拒 --workspace-root」+ leader_lane +
      test_leader_lane 逐字消费 leader-actions v1 fixtures)。
      cli.py 与 PR 6 obs 批的 7 行冲突=两边保留,语义合并正确。
      施工插曲: 子代理死于 API 中断(最后一步验证前),主脑接手完成独立验证与合入;
      其停车快照之外的新写仅 2 处(HTTP adapter 补 close() 资源释放 + 双关安全测试)。
      门禁: bridge 分项 484(PR 6 时 390,+94)、contracts 260 零漂移、
      全量 **2147/23 exit 0**(前基线 2053/23)。四禁区(supervisor.py/服务端/冻结契约/
      迁移)零触碰已核
W-B2b supervisor 集成 [波次2·B线] 状态: **已验收合入(08-28)——PR 8 全部收官**
      合入 `bc455b1d`(cherry-pick 自 feat/w-b2-supervisor 的 ad9ccb42);
      门禁: bridge 分项 515(前 484,+31)、contracts+task_orchestration 413、
      全量 **2178/23 exit 0**(前 2147/23)。
      两项主脑裁决(均接受): ①跨重启不双跑=「先 fetch_assignment 再决策」而非本地
      state 表——RepoMesh phase 即持久真相,免 SCHEMA_VERSION 升版,陈旧通知只花一次 GET;
      ②畸形草稿房间文案=固定句+详情进日志(模型产出非平台笔迹,不逐字入房;
      RepoMesh refusal 照旧逐字)——与 PR 5 白名单纪律同构。
      fixture 比工单更强: 两种通知正文由**运行服务端真实 leader-mode 轮次**取得
      (非逐字抄写),服务端改措辞自动跟随、改路由必红。
      M7 smoke 预检清单(子代理产出,主脑确认): ①头号未知=leader DM 事件是否带
      m.mentions(matrix.py:129-150 只在 recipient_resource_name 非空且带 control_plane
      时写),不带则 leader 轨活着但沉默——smoke 第一步先抓一条真实 DM 事件;
      ②两个 POST 未对真实路由跑过(已开后台任务补 in-process ASGI 用例);
      ③leader enrollment 凭据只认 env: locator 且放 external member token(D-6);
      ④leader DM 房须同时在 enrollment 与 binding v2 的 allowedRoomIds;
      ⑤M7 必须一 leader+一能真执行的 worker(review_due 要 worker 全终态);
      ⑥真 codex 不肯只吐 JSON 时走拒绝草稿路径,房间一条 note、人再 @ 重试(设计预期)。
      残余记账: 900s 超时覆盖整轮;RepoMeshGovernedTaskAdapter 至今无组合根关闭(既有小缺口)
W-C2  PR 9+Q2      [波次2·C线]  状态: **已验收合入(08-28)**
      合入 `d83700dc`/`bb0d57f2`/`483e30dc`/`371f582e`/`793b0bf9`(cherry-pick 自
      feat/w-c2-room-timeline 五提交,零冲突)。迁移 0039 down=0040,合并头上一次性 PG
      实证 upgrade head 单链 + PG store 测试 3 passed(开关=REPOMESH_TEST_POSTGRES_URL)。
      门禁: 分项 898、全量 **2209/26 exit 0**(前 2178/23;+3 skip 即无库时的 PG 时间线测试)。
      六项已裁取舍: 幂等键单一来源(event_id 即键,不设第二形参)/回放不重解析身份
      (如实未知不得事后改口)/D-7 检查前置于身份校验(路对谁都关,且防伪造上报 raise
      致整批无限重试)/审计复用 platform.audit_events+processed_matrix_events 去重
      (审计行记 raw matrix id,未经验证的自称不得记为行为人)/origin_server_ts 必填
      非法丢弃/自删无调用者的组合根 recorder。
      残余记账: ①room_stream 单房间录制侧 1000 条隐性截断(真解=跨源游标合并,未做);
      ②控制面不可达时 timeline 停摆刷错(与 verifier 依赖形态一致,刻意);
      ③前端 CollaborationMessageView.sender_agent_id TS 类型应收窄 string|null→**归 W-C3**;
      ④出站 direction 硬编码 leader_to_worker(既有,未动)。
      M8 活体预检清单(子代理产出,主脑确认): ①先 alembic upgrade head(0039 未应用则
      ingest 全 500);②**RepoMesh 服务端 Matrix 账号必须已 join 目标房间**(/sync 只回
      rooms.join,最可能挡 M8 的一条);③拓扑 room_id/leader_room_id 必须已回写(白名单
      全来自拓扑);④人类发送者按 raw matrix id 显示是 D-4 设计而非 bug;
      ⑤REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN 有效否则静默只显出站
W-C3  PR10+Q1+Q3a  [波次2·等待] 状态: 排队
W-E1  E1 脚本       [空闲槽]      状态: 排队(等 W-A1 部署)
```

E0a 环境凭据(LLM key、Worker Matrix token、`REPOMESH_RUNNER_WORKER_TOKENS` 值)不入库,
存放于 gitignored 的 `output/bridge-team/e0a-live-env.md`;.env 只承载 LLM key,
delivery 全套开关保持关闭。
