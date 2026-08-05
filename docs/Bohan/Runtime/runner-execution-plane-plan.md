# RepoMesh Runner 执行面落地方案 v0.1

- 日期：2026-08-05
- 状态：**执行中**（D1-D3 已裁决并开工；D4/D5 取默认值待队友异议；M5 需 GitHub 权限暂缓）
- 上游文档：`agentteams-runner-runtime-plan.md`（runtime 注册计划，已部分实施）、
  `contracts/runtime/v1/worker-runtime.md`（已冻结）、runner 接入交接清单（队友 2026-08 手稿）
- 基线分支：`feat/agentteams-runner-runtime`（本地，含 runtime 注册 5 提交，叠在
  `feat/runner-drivers` 之上）

---

## 一、定位：两条轨道，一个产品

| 轨道 | 形态 | 状态 | 归宿 |
|---|---|---|---|
| **A. Bridge 插件**（前期） | 外部 CLI 跑在操作者笔记本，`containerManaged: false`，Matrix 收发 | **已完成并 E2E 验证**（`feat/agentteams-external-cli-runtimes`） | 提上游 AgentTeams（#399） |
| **B. repomesh-runner runtime**（后期） | Worker 容器内跑 Runner（PID 1），走网关，平台全权控制 workspace/权限 | AgentTeams 侧注册已完成；**Runner 侧是本方案的主体** | 产品自有（fork，永不上游） |

两轨道**共存不合并**：A 解决"个人订阅 CLI 当团队成员"，B 解决"平台受控的规模化编码执行"。
本方案只做 B，**不改动轨道 A 的任何插件代码**。

队友交接清单的两项核心诉求——接收 `workspace_id` 隔离工作区、补全 `RunnerTask` 契约做通用
模板——语义上属于轨道 B（平台能造 worktree 的前提只在容器内成立），全部纳入本方案。

## 二、目标与非目标

**目标**

1. `RunnerTask` / `RunnerExecutionResult` 契约补全到可做通用模板（交接清单 §建议契约）。
2. Runner 从"库"变成"进程"：能作为 PID 1 常驻，拉任务、执行、回传事件。
3. 交接清单 6 个合并前阻断项全部关闭。
4. 打通验证阶梯第 4 层（活体兼容测试：真 controller + 新 runtime Worker CR + Mock 镜像）。

**非目标（明确不做）**

- Matrix 协作通道（冻结契约已声明为后置里程碑）。
- Edge 部署模式支持。
- ATG-01 策略执行补丁（skill 白名单、房间成员、file-sync 禁用——runtime 计划已排为后续）。
- 轨道 A 插件的任何改动。
- Trae / Cursor 适配（先确认 headless 协议稳定性再排期）。

## 三、里程碑

### M0 · 分支止血（半天）

- `feat/agentteams-runner-runtime` rebase 到最新 `origin/main`（预演已确认两侧文件不相交，
  预期零冲突），跑全量测试后**推送**。
- 本地 `main` 上未推送的 runner-drivers 合并提交（`e9c736e`）与队友对齐处置：
  要么推上去，要么放弃改走分支 PR。
- 后续工作分支：`feat/runner-execution-plane`，基于 rebase 后的 runtime 分支。

### M1 · 契约补全（1~2 天，与队友协商字段后动手）

**RunnerTask 新增（全部为 v1 可选字段，向后兼容）：**

```
workspace:            # 平台准备好的 worktree；缺省时退回 runner 自建目录（过渡期）
  workspace_id: str
  path: str           # 绝对路径；runner 校验必须位于配置的 workspace 根内，禁止逃逸
  base_sha: str
worker_agent_id: UUID
repository_id: UUID   # 提升到顶层；RepositoryCheckout.url 降级为可选参考信息
context:
  coding_package_hash: str    # 在 ContextBundleRef 上追加
permissions:
  allowed_paths: tuple[str, ...]
  denied_paths: tuple[str, ...]
test_commands: tuple[str, ...]
```

**RunnerExecutionResult 新增：**

```
changed_files: tuple[str, ...]
test_results: tuple[TestResult, ...]     # {command, exit_code}
```

失败结果不得包装成成功（结构上强制：`SUCCEEDED` 必须携带非空执行证据或显式空声明）。

**同步项：**

- `contracts/runtime/v1/runner-task.schema.json` / `runner-event.schema.json` 同步加可选字段
  （README 明文：v1 内 additive optional 兼容,不升版本）。
- `tests/contracts/test_runtime_v1_contract.py` 跟进。

**语义决策（2026-08-05 已裁决，author 归属以 git 为证）：**

| # | 决策点 | 冲突来源 | 裁决 |
|---|---|---|---|
| D1 | `workspace.base_sha` 与 `RepositoryCheckout.base_revision` 双真相源 | **队友 vs 队友**：`RepositoryCheckout{url,base_revision}` 是其 08-02 契约（`d5e9775`，隐含 Runner 自行 clone），交接清单里他本人已推翻（"Runner 不负责 Clone"）。我方 executor 从未消费 `task.repository`，无既得设计 | 采纳其新方向：workspace 存在时为唯一权威，`RepositoryCheckout` 降级为参考元数据，优先级写进 schema 描述。待其确认降级形式（保留参考 vs 废弃 url） |
| D2 | bypass 语义 | **我 vs 队友**：executor "bypass 即全放"是我 08-03 的有意设计（`2e7385b`，docstring 注明，依据是 CLI flag 实测仅建议性、真边界在容器）；硬边界要求来自其交接清单 §3。注意我 08-04 冻结契约"Permissions come only from the RunnerTask"已在语义上倒向其立场——同一结构里 `mode` 作废兄弟字段即授权自相矛盾 | 采纳其语义：拒绝规则全模式生效，bypass 只免交互确认。**两个联动点缺一不可**：① policy 回调在 bypass 下对 disallowed/denied 回答 DENY；② `profiles.py` 的 bypass 不再映射 CLI 自身 bypass flag（否则 CLI 不发 control_request，回调永不触发），CLI 保持询问模式靠回调放行，代价为每工具一次协议往返。边界分层写进契约：协议回调=合作性防线，容器/文件系统/网络=硬边界。过渡期（硬隔离前）任务校验直接拒绝 BYPASS 模式 |
| D3 | 权限优先级 | 交接清单 | 照抄：`denied_paths > disallowed_tools > allowed_paths > allowed_tools > provider mode` |
| D4 | 任务传输形态(M3 用) | 未定 | 默认 HTTP 长轮询先跑通（`task_source` 做成接口可替换）；待队友异议 |
| D5 | resume 首批验证 CLI | 未定 | 默认 Codex（app-server thread/resume 已实现未验证，距离最近）；实测不过如实保持 `resumable=False` |

### M2 · Runner 核心修复（2~3 天）

按交接清单逐项，全部在 `src/repomesh_runner/`：

1. **修 pytest 收集失败**：`drivers/stream_json.py` 加 `from __future__ import annotations`
   （`CliProfile` 运行时 NameError）。第一件事做，不然测试都收集不了。
2. **workspace 接收**：executor 停止自建 `workspace_root/run_id`；task 带 workspace 时使用
   外部路径 + 根目录归属校验（`Path.resolve()` 后前缀判定，防 `..`/symlink 逃逸）。
3. **权限硬边界**（D2/D3 落地）：重写 `AllowlistPermissionPolicy` 优先级链；
   `permission_arguments` 的 bypass 映射不得注入绕过平台拒绝的 CLI flag。
4. **context 消费**：执行前校验 `bundle_id`/`content_hash`/`coding_package_hash`，
   不一致 → 拒绝执行（`FAILED`，diagnostics 说明 hash 不匹配），不静默继续。
5. **结构化结果**：driver 结果映射补 `changed_files`（git status/diff worktree 采集）与
   `test_results`（`test_commands` 逐条执行，记录 exit_code）。
6. **真实 session resume**（D5 选定的 CLI）：首轮拿真实 session id → 杀进程 →
   凭 id 恢复 → 断言续接原会话；无效 id 显式失败不静默新建。
   （轨道 A 的教训直接搬来：session 句柄**事件时刻落盘**，不等 turn 结束。）

门槛：`ruff check .` + `pytest -q` 全绿。

### M3 · 进程形态（2~3 天）

Runner 目前是库（contracts + engine + executor + drivers），没有入口。补齐：

```
src/repomesh_runner/
  __main__.py        # python -m repomesh_runner；读 env 契约,组装,进主循环
  task_source.py     # D4 选定形态;至少一次投递,按 idempotency_key 去重
  event_sink.py      # RunnerEvent 回传;失败重试,orderly sequence
  runtime_env.py     # 冻结契约的 env 三分类：消费/忽略/拒绝(权限变量出现即启动失败)
```

- 主循环语义照冻结契约：SIGTERM → 完成当前工作 → 发 `interrupted` 终态事件 → 退出；
  宽限期后 SIGKILL。与 drivers/supervision 的进程组终止对齐。
- **至多一次执行**：同一 `idempotency_key` 不重复执行（轨道 A 的 dedup 语义,实现独立）。
- 心跳：不做（runtime 计划 §2.2 已钉死：无 `idleTimeout` 永不休眠,Local 模式不看 heartbeat）。

### M4 · 镜像与活体验证（2 天）

- Worker 镜像：Runner + Scenario Mock（**不含任何 vendor CLI 与凭据**）。
- 验证阶梯 4 层：embedded controller `apply` 一个 `runtime: repomesh-runner` 的 Worker CR →
  容器 Running、phase 正确、`agt get workers` 可见 → Mock RunnerTask 端到端（下发 → 执行 →
  事件回传全链路）。
- 验证阶梯 5 层：Mock 长任务空转 30 分钟、零 Matrix 活动，断言 Worker 不被休眠。

### M5 · Fork 阶段 0 — **已完成（2026-08-05）**

- Fork：`catbobyman/AgentTeams`，分支 `repomesh/main`，从上游基线 `793db24`(v1.2.0)
  **fast-forward** 到 `c306069`(含 runtime 注册补丁 + main 侧 install 脚本改动)。
  没有强推，历史与上游真正接续。
- `upstream.toml` 的 `[product_fork]` 已填实；ADR 0002 四元组现在报真值
  (`fork_commit` / `upstream_commit` / `runtime_contract` 实测非空,
  `repomesh_commit` 由构建注入 `REPOMESH_BUILD_COMMIT`,本地为空属预期)。
- 往返管线（验证阶梯 6 层）已双向跑通：`subtree split` → push fork，
  再 `subtree pull --squash` 回来，**merge 相对第一父零内容漂移**，
  两个提交是纯记账。记账点建立后，后续 pull 才不会重复导入。

**踩到的坑（会挡住任何人第一次做往返）**：squash 方式导入的 subtree
**切断了与上游的历史连接**——本地根本没有 `793db24` 这个对象，
`git subtree split` 直接失败(`could not rev-parse split hash ... from commit <squash>`)。
必须先把上游对象取回本地：

```bash
git remote add agentteams-upstream https://github.com/agentscope-ai/AgentTeams.git
git fetch --filter=blob:none agentteams-upstream 793db242257a569d911b1aa59c1cd554af78511f
```

`--filter=blob:none` 让这一步只取历史不取文件内容，很快。

**流程偏差，需知情**：计划原文要求"所有产品补丁经 fork 分支 PR 评审合入"。
第一刀补丁是直接 push 建立基线的——它已在本 monorepo 的分支上评审过，
再走一次自评自合的 PR 是走过场。**从第二个补丁起**按原流程走 PR。

## 四、验收标准（对交接清单逐条回应）

| 交接清单要求 | 本方案落点 |
|---|---|
| Ruff + 完整 Pytest 通过 | M2 门槛 |
| Claude、Codex 真实 CLI 冒烟 | M2-6 + 既有 `test_smoke_real_clis.py` |
| 显式拒绝规则任何模式不可绕过 | M2-3（D2/D3） |
| 只在指定 Workspace 内执行 | M2-2 |
| Hash 不一致拒绝执行 | M2-4 |
| 成功结果含改动文件与测试证据 | M2-5 |
| 至少一个 CLI 真实失败后 resume | M2-6（D5） |
| workspace_id 隔离 | M1 + M2-2 |
| RunnerTask 通用模板 | M1（schema 同步后即可出模板） |

## 五、风险

1. **D2 语义反转是破坏性变更**——现有测试断言 bypass 全放行，需成对改；PR 单列说明,避免
   评审误读为回归。
2. **传输形态选错返工**（D4）——先拍板再写 M3；task_source 做成接口,形态可替换。
3. **resume 实测不可控**——依赖 vendor CLI 行为,预留探测时间;实测不过就如实标
   `resumable=False`（"未真实验证的能力不能标记为支持"是清单红线）。
4. **rebase 后的活体环境漂移**——本地 embedded 部署里的测试成员/镜像与新代码不匹配时,
   以重建容器为准,不修旧容器。

## 六、分工建议

- 契约字段协商（M1 决策表 D1-D5）：双方一次会议定完,写进本文档后冻结。
- M1/M2 与 M3 可两人并行（契约层与进程层耦合点只有 task_source 的消息形状）。
- M5 需要 GitHub org 权限,谁有谁做,与主线无依赖。
