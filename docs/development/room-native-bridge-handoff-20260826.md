# Room-Native Bridge 交接文档(至 PR 1 收官)

> 日期:2026-08-26
> 分支:`feat/room-native-agent-bridge`(HEAD `09b9957c`,未推送)
> 状态:**PR 0 + PR 1 完成,整分支终审 Ready to merge;下一步 PR 2**
> 读者:零上下文接手本线的工程师或 agent 会话

## 1. 这条线是什么

把本地 Coding CLI(Codex 先行)以 AgentTeams 外部 Worker(`containerManaged: false`)身份接进
Matrix 房间,可被提及、可连续对话、可重启恢复;正式改码仍走 RepoMesh 的 Task/worktree/测试/
commit 治理。Bridge 是独立进程,复用现有 `repomesh_runner` 驱动栈,Runtime v1 零改动。

**四份事实源(全部已提交,按此顺序读)**:

1. `docs/adr/0004-room-native-agent-bridge.md` — 冻结的裁决(独立进程、四个 seam、两段式启动
   验证、协作式 deny-all、Bridge 兼任 Runner consumer 等)。
2. `contracts/agent-bridge/v1/` — 三份 schema + README 接口语义(幂等三键、信任模型、隔离分层、
   不变量→验收映射表)。**已冻结,改字段=升 v2**(`additionalProperties: false` 使加字段也算破坏)。
3. `docs/development/room-native-bridge-execution-plan-20260826.md` — 执行计划(档位 B+,
   PR 0–5 + 平行轨 P,文件级改动范围与验收门禁)。同名 .html 是可视化版。
4. `docs/development/room-native-coding-agent-bridge-proposal-20260826.md` — 原始提案(背景与
   工作量依据;已逐条核实)。

## 2. 已完成的提交(main 之上 9 个)

| 提交 | 内容 |
|---|---|
| `12886406` | Day 1:ADR 0004 初版 + 契约初稿 |
| `d5ba275c` | **PR 0**:契约校正(workerAgentId/matrixHomeserverUrl、binding schema 新增、统一版本串、去掉悬空 heartbeat 承诺)+ 14 个契约结构测试 |
| `7367b722` | 前缀线:`rm-`→`repomesh-` 全线 21 文件(修 dangerous-rm 守卫误杀;旧房间不改名) |
| `3aafdb82` | PR 0 评审修复:ADR 补 Runner-consumer 复用裁决 |
| `2ecab164` | **PR 1**:`WorkerProjection.container_managed`、control_plane 携带/检查/冲突、`ProvisionExternalWorker` + `ResolveExternalWorkerBinding` use case、preflight 端点、25 个新测试 |
| `ff2efe43` | PR 1 评审修复:port/use case 声明冲突异常穿透契约 |
| `55e63a2e` | 提案文档入库(消除 ADR 悬空引用) |
| `f441bc70` | 终审修复:module 自有 `WorkerControlPlaneUnavailable` 异常 + adapter 层翻译 + 503;`tests/api/test_external_worker_binding.py` 钉死 HTTP 契约 |
| `09b9957c` | 执行计划 md+html 入库 |

**门禁证据**:`ruff check .` 干净;全量 `pytest -q` **1347 passed / 19 skipped**(main 基线
1315/19,+32 全为本线新测试)。评审链:每 PR 独立评审(opus)+ 整分支终审(fable)= Ready to
merge,SDD 台账在 `.superpowers/sdd/progress.md`(git-ignored,含全部 Minor 分诊)。

## 3. PR 1 落成的服务端事实(PR 2 直接对接)

**Preflight 端点**:`GET /runtime/external-workers/{workerAgentId}/binding`,认证暂用
runner control token(`_authorize_runner`;Worker-scoped 凭据是 PR 5)。错误码契约已被
`tests/api/test_external_worker_binding.py` 钉死:

| 码 | 含义 |
|---|---|
| 401 | 缺/错 token |
| 404 | workerAgentId 无对应 principal |
| 409 | 一切拒绝(非 Worker 角色、managed worker、绑定不一致……)——`ExternalWorkerRefused` 一型多消息,**客户端勿按 message 文本分支** |
| 503 | 控制面未配置 或 不可达(`WorkerControlPlaneUnavailable`)——可重试 |
| 500 | controller 答了但答错(fault,刻意不翻译) |
| 200 | `repomesh.agent-bridge.binding.v1` wire body(camelCase,与 schema 逐字段一致) |

**Provisioning**:`ProvisionExternalWorker`(application/external_worker.py)已实现但**无生产
调用者**——组装入口按裁决落在 PR 2(bridge `--check` / enrollment 流程)。adapter 的
`AgentTeamsConflict` 会穿透 `provision()`,调用方须把它映射为 refusal(契约已写进 port docstring)。

## 4. 下一步:PR 2(可启动 Bridge v1 骨架,估 2–3 人日)

范围与验收按执行计划 PR 2 节执行。终审移交的五个要点:

1. **抽窄读 protocol `WorkerBindingReader`**(只含 `get_worker`/`get_team`):修
   `bootstrap/container.py` 里 `external_worker_binding_control_plane()` 返回注解过宽的问题
   (adapter 只实现 3 个方法却标了完整 `AgentTeamControlPlane`),它同时就是 Bridge 侧
   `WorkerBindingPort` 的服务端镜像。
2. **`enrollment.teamName` 与 `binding.teamName` 不一致时的行为无文档规定**(workerAgentId
   不匹配有明文=stage-2 失败;teamName 没有)——PR 2 自行裁决并写进测试。
3. **`allowedRoomIds` 交集为空**同样未明文——按 fail-closed 精神应拒绝启动,直接做成
   fail-fast 用例。
4. `tests/contracts/test_agent_bridge_v1_contract.py` 的 `make_*` fixture 按其 docstring 应在
   PR 2 换成真 dataclass `to_wire()` 输出。
5. **真机 smoke(PR 1 顺延项)在 PR 2 补**:创建 external Worker → 有 Matrix 身份、无容器、
   能进 Team。顺延原因:compose 栈未起、controller 8090 只在 compose 网络内不对宿主暴露、
   provisioning 无组装入口。`bridge --check` + preflight 是天然载体。

平行轨 P(执行面修复:handoff_docs 迁移、materialize 409、runner 镜像活体验证)可与 PR 2–4
并行,是 PR 5 硬前置;详见执行计划。

## 5. 环境与操作

- Python:`.venv/Scripts/python.exe`(Git Bash,Windows)。
- 门禁:`.venv/Scripts/python.exe -m ruff check .` + `... -m pytest -q`(全量约 4–5 分钟)。
  **不要带 `-p no:warnings` 跑门禁**——warning 数是证据的一部分(基线约 5900 条,全部来自
  pytest-asyncio 对 Python 3.16 asyncio-policy 的弃用告警,与本线无关)。
- **工作区剩余未提交文件都不属于本线,勿混入任何提交**:`.github/workflows/ci.yml` +
  `tests/integration/test_runner_gateway_postgres.py`(Runner CI 线)、`docs/architecture/*.html`
  与 08-25 各分析文档(结构分析线)。
- SDD 台账:`.superpowers/sdd/progress.md`(git-ignored;`git clean -fdx` 会清掉,恢复靠
  `git log` 与本文档)。

## 6. 教训(接手前请读)

1. **判定一批工作区改动的归属,先全仓扫描再下结论**:前缀线曾被误判为 4 个文件,险些半丢
   (实际 21 个文件,含前端与 seed);孤儿提交救回。改动分类用 `git diff` 逐文件对内容,
   不要只看撞到的文件。
2. `tests/conftest.py` import `repomesh.bootstrap`,任何 module 层符号缺失会让全套件挂起;
   契约测试可用 `--noconftest` 独立跑,但那不能替代门禁。
3. Minor 分诊全部 DEFER 的清单在台账里(binding freshness 字段、测试假件重复、
   `_assert_fields` 的 0==False 理论洞等),PR 2 起视情况消化,不必现在返工。
