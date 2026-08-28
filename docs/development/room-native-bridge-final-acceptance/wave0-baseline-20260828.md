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
      ④D-2 实测已非阻断(相对路径 shell 读 context 被 allow)。PR 6 段接续开工
W-A2  PR 7 完整集成 [波次2·A线]  状态: 排队
W-B2  PR 8         [波次2·B线]  状态: 排队
W-C2  PR 9+Q2      [波次2·C线]  状态: 排队
W-C3  PR10+Q1+Q3a  [波次2·等待] 状态: 排队
W-E1  E1 脚本       [空闲槽]      状态: 排队(等 W-A1 部署)
```

E0a 环境凭据(LLM key、Worker Matrix token、`REPOMESH_RUNNER_WORKER_TOKENS` 值)不入库,
存放于 gitignored 的 `output/bridge-team/e0a-live-env.md`;.env 只承载 LLM key,
delivery 全套开关保持关闭。
