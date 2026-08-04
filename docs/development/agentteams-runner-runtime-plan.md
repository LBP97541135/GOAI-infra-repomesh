# AgentTeams 产品 Fork 与 repomesh-runner Runtime 实施计划 v0.1

- 日期：2026-08-04
- 状态：方案已确认，待实施
- 决策依据：ADR 0002（Fork 前置与四版本要求）、`agentteams-projection-spec.md` §10.1
- 代码证据基线：`components/agentteams` @ v1.2.0（upstream commit `793db24`）

## 1. 决策

Worker 中运行 RepoMesh Runner 采用**路线二（Fork 后新增第一方 runtime）**：在
AgentTeams Controller 中注册 `repomesh-runner` 作为正式 Worker runtime 值，拥有自己的
入口契约、镜像默认值和生命周期语义。

原基线方案（`spec.image` 挂靠现有 runtime 借壳）降级为**回退方案**：仅当 Fork 流程
受阻且端到端验证急需推进时临时启用，且借壳产生的入口兼容代码不得进入正式镜像。

## 2. 代码证据

### 2.1 runtime 消费点（改动面收敛，全部为加法）

| 位置 | 现状 | 改动 |
| --- | --- | --- |
| `internal/backend/interface.go:34-39,61-63` | 五个 runtime 常量 + `ValidRuntime` | 加 `RuntimeRepomeshRunner = "repomesh-runner"`，纳入校验 |
| `internal/backend/docker.go:111-117` | 按 runtime 选默认镜像 | 加 case → `config.RepomeshRunnerWorkerImage` |
| `internal/backend/kubernetes.go:259-265,742-750` | 同上 + 工作目录/标签解析 | 同上，加 switch 分支 |
| `internal/backend/sandbox.go:149-155` | 同上 | 同上 |
| `internal/config/config.go` | 各 runtime 镜像配置项 | 加 `RepomeshRunnerWorkerImage`（env `AGENTTEAMS_REPOMESH_WORKER_IMAGE`） |
| `internal/agentconfig/generator.go` | 按 runtime 生成配置树 | `repomesh-runner` 生成空/最小配置树（Runner 不读 openclaw 配置目录） |
| `member_reconcile.go:368`、`worker_controller.go:767-777`、`team_controller.go:782` | qwenpaw 等特判 | 核对不误伤新值，预计零改动 |
| `cmd/agt`（apply/create/update） | CLI runtime 校验 | 接受新值 |
| `helm/agentteams` values + embedded 安装 | 各 runtime 默认镜像 | 增加 repomesh-runner 条目 |

估计总量 200 行以内。**保持 additive 是硬要求**：补丁只加 case、不做重构，
使后续 subtree 合并上游更新的冲突面最小。

### 2.2 auto-sleep 与心跳真相（实测替代项，已由代码阅读确认）

1. 休眠判定（`auto_sleep_controller.go:66-79`）：`spec.idleTimeout` 或
   `status.lastActiveAt` 任一为空即永不休眠。**编码 Worker 不投影 `idleTimeout`
   即整体关闭 auto-sleep**，无需 Runner 实现心跳。
2. `lastActiveAt` 由 Matrix AppService 写入（`appservice_handler.go:393`）——
   "活跃"定义是 Matrix 消息活动，不是进程心跳。
3. `lastHeartbeat` 仅在 Edge 部署模式参与生命周期判定
   （`worker_controller.go:118`）；Local 模式不使用。

结论已回写投影规范：§4.1 `idle_timeout` 改为不投影；§10.3 由待决降级为已确认。
Runner 的超时治理归自身 idle watchdog 与 Task Orchestration，与 Controller 无关。

## 3. 实施阶段

### 阶段 0：建立产品 Fork（一次性流程前置）

1. 从 `agentscope-ai/AgentTeams` fork 产品仓库，基线打在 `793db24`
   （现 `upstream.toml` 记录的 v1.2.0 commit）。
2. Fork 上建 `repomesh/main` 分支；所有产品补丁经该分支 PR 评审合入。
3. 扩展 `src/repomesh/integrations/agentteams/upstream.toml` 为四元组（§7），
   并添加读取测试——满足 ADR 0002"构建能报出四个兼容版本"的要求
   （该文件当前无任何测试引用）。
4. monorepo 更新路径固定为：上游 → Fork（评审合并）→ `git subtree pull` 进
   `components/agentteams`。禁止直接从上游 subtree pull。

### 阶段 1：契约先行（写 Go 之前）

按 AGENTS.md"跨面变更先定契约"，先冻结 §5 的 Worker Runtime 契约。

### 阶段 2：第一刀 Go 补丁（刻意最小）

只做 §2.1 清单的 runtime 注册。**不包含** ATG-01 的任何内容（skill 策略执行、
房间成员执行、file-sync 禁用均为后续独立补丁）。第一刀承担双重目的：功能本身 +
验证"Fork → 评审 → subtree → 构建 → 版本记录"整条流水线。

### 阶段 3：RepoMesh 侧对齐

- 投影规范 §4.1 `runtime` 字段改投影 `repomesh-runner`；
- `integrations/agentteams` 适配层放开新值；
- Worker 镜像构建（Runner + 驱动层 + Scenario Mock，不含真实 CLI 凭据）。

## 4. repomesh-runner Worker Runtime 契约（草案 v0）

本契约已于 2026-08-04 冻结于 `contracts/runtime/v1/worker-runtime.md`（英文正式版）。
以下内容为冻结时的快照，仅作背景参考；两者不一致时以冻结的英文文档为准。

1. **入口**：容器启动即运行 RepoMesh Runner 进程（PID 1）。不运行 OpenClaw/
   QwenPaw/Hermes 任何组件，不读取 agentconfig 生成的配置树。
2. **env 契约**：
   - 消费：任务来源端点、事件回传端点、`repomesh.dev/*` labels 透传值、
     对象存储端点与凭据引用；
   - 忽略：Matrix 凭据（协作通道后置里程碑）、OpenClaw 系变量；
   - 拒绝：任何权限语义变量。权限只来自 RunnerTask
     （投影规范 §2.7；`AGENTTEAMS_YOLO` 等一律不读）。
3. **配置树**：无。Controller 的 agentconfig generator 对本 runtime 产出空树。
4. **心跳**：Local 部署模式不要求（§2.2 证据）；Edge 模式暂不支持本 runtime。
5. **终止**：SIGTERM → Runner 优雅收尾（回传 interrupted 终态事件）→ 宽限期后
   SIGKILL。与驱动层 supervision 的进程组终止语义（先组 TERM、等待、组 KILL）对齐。
6. **存储**：仅访问 accessEntries 授权的前缀（投影规范 §6.3）；
   不使用 `agents/<name>/*` 工作区同步。

## 5. 验证阶梯（六层，由快到真）

| 层 | 内容 | 断言 |
| --- | --- | --- |
| 1. Go 单测 | `ValidRuntime`、三个 backend 的镜像解析、generator 空配置树、`shouldSleep` 无 idleTimeout 永不触发 | 固化 §2.1/§2.2 全部行为 |
| 2. Controller reconcile 测试 | 创建 `runtime: repomesh-runner` 的 Worker CR | 容器创建参数（image/env/labels）正确、specHash 稳定、Team 聚合正常 |
| 3. RepoMesh 契约测试 | 投影编译、适配层、`upstream.toml` 四元组完整性 | 新 runtime 值端到端可表达 |
| 4. 本地活体兼容测试 | `start-platform.ps1` 起 embedded Controller，apply 新 runtime Worker CR，镜像为 Runner + Scenario Mock（无真实 CLI） | 容器 Running、phase 正确、Matrix 用户/房间已建、`agt get workers` 可见 labels、Running→Stopped 迁移生效、Mock RunnerTask 事件端到端 |
| 5. 长任务免休眠测试 | Mock 任务空转 30 分钟以上、零 Matrix 活动 | Worker 未 Sleeping、Runner 进程未被打断（§2.2 的端到端确认） |
| 6. Subtree 往返演练 | 补丁在 Fork 合并 → subtree pull 进 monorepo → 构建 | 构建通过、四元组版本输出正确；趁补丁小走通更新管线 |

第 4 层是 ADR 0002 要求的 live compatibility test；第 4、5 层复用 Runner 现有的
七场景 Scenario Mock 作为镜像载荷，不依赖任何 vendor CLI 与凭据。

## 6. upstream.toml 四元组

```toml
name = "agentscope-ai/AgentTeams"
version = "v1.2.0"
commit = "793db242257a569d911b1aa59c1cd554af78511f"   # upstream base
license = "Apache-2.0"
controller_api = "/api/v1"
matrix_api = "/_matrix/client/v3"

[product_fork]
repo = "<org>/AgentTeams"          # 阶段 0 建立后填入
branch = "repomesh/main"
commit = "<fork commit>"           # 每次 subtree pull 时更新

[compatibility]
runtime_contract = "runtime.v1"
# RepoMesh 自身版本与 commit 由构建注入，不手写
```

配套测试断言：四个值（RepoMesh 版本、fork commit、upstream commit、
runtime contract）均可由构建产出；`[product_fork].commit` 与
`components/agentteams` 的 subtree 记录一致。

## 7. 执行顺序

| 步骤 | 依赖 | 可立即开始 |
| --- | --- | --- |
| Fork 仓库 + `repomesh/main` 分支 | GitHub 权限 | 是 |
| `upstream.toml` 四元组 + 读取测试 | 无 | 是 |
| §4 契约评审冻结并迁入 `contracts/runtime/v1` | Owner 评审 | 是 |
| Go 补丁（§2.1 清单） | 上述三项 | 否 |
| 验证 1-3 层 | Go 补丁 | 否 |
| Worker 镜像（Runner + Mock） | 契约冻结 | 与 Go 补丁并行 |
| 验证 4-6 层 | 补丁 + 镜像 | 否 |

ATG-01 的策略执行补丁（skill 白名单、房间成员、file-sync 禁用）在本计划完成、
流水线验证通过后作为后续独立补丁排期。
