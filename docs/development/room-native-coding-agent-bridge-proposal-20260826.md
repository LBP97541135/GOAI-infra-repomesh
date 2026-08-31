# 房间原生 Coding Agent Bridge 架构报告

> 日期：2026-08-26  
> 状态：提案，尚未实施  
> 目标：让本机 Claude Code、Codex 等 Coding Agent 以 AgentTeams 正式 Worker 身份参与
> Matrix 房间对话，同时继续受 RepoMesh 的 Project、Task、Context、权限、测试和提交规则治理。

## 1. 执行摘要

当前 RepoMesh 把“房间里的 Agent”与“真正写代码的 Coding Agent”分成了两层：

- AgentTeams 的 CoPaw/OpenClaw Worker 拥有 Matrix 身份，可以接收提及和发言；
- RepoMesh Runner 启动 Claude Code、Codex、Kimi 等 CLI，在独立 Git worktree 中执行代码任务；
- Coding CLI 本身没有 Matrix 身份，不能直接被其他 Agent 提及，也不能在房间里连续对话；
- Runner 当前只向 RepoMesh 回传结构化执行事件，房间最多看到平台或 Worker 外壳转述的结果。

本报告建议新增一个 **Room-Native Coding Agent Bridge**。它建立在 AgentTeams 官方
`containerManaged: false` 外部托管 Worker 能力上，在操作者机器运行，并把一个 AgentTeams
Worker 身份绑定到一个本地 Coding CLI 会话。Bridge 同时连接 Matrix 与 RepoMesh，但不把
Matrix 当任务数据库，也不允许普通聊天直接越过 RepoMesh 的执行门禁。

推荐形态不是“让 Bridge 绕过 Runner 直接随意改代码”，而是把 Bridge 做成受治理的本地
Agent 客户端：

```text
Matrix 房间
   │  @提及、问题、回答、可观察进度
   ▼
Room-Native Coding Agent Bridge
   ├── 对话回合 ───────────────► 本地 Coding CLI 长期会话
   └── 正式编码任务 ─► RepoMesh 授权与任务租约
                            │
                            ▼
                    独立 Git worktree
                            │
                            ▼
                   CLI 执行、测试、commit
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
          Runner 结构化事件       Matrix 可观察摘要
          RepoMesh PostgreSQL      不含内部推理
```

这样既得到“Codex/Claude Code 是团队房间里的真实队员”，又保留现有 Runner 的幂等、路径
权限、测试、提交证据和恢复能力。

## 2. 已确认的当前机制

### 2.1 AgentTeams 外部托管 Worker

AgentTeams Worker 资源已有可选字段：

```yaml
spec:
  containerManaged: false
```

该字段默认是 `true`。设置为 `false` 后，Controller 仍可保留 Worker 资源、Matrix 身份、
Team 成员关系及适用的声明式配置，但跳过容器创建、删除、休眠和重启。外部进程的启动、
保活和故障恢复由接入方负责。

这只是一个生命周期扩展点，不是一套完整的远程 Coding Agent 协议。AgentTeams 官方没有
替外部进程实现本地 CLI 启动、会话续接、任务租约、结果证据或 RepoMesh 授权。

### 2.2 RepoMesh 当前执行路径

当前正式 Worker 任务的路径是：

1. RepoMesh 在 PostgreSQL 中保存 Project、Specification、Task 和 Agent 拓扑；
2. Task Spec、`meta.json` 和内容哈希清单发布到 AgentTeams Team 的对象存储命名空间；
3. RepoMesh 在 Team Matrix 房间中提及目标 Worker；
4. Worker 调用 `repomesh-task-control.start_assigned_task`；
5. RepoMesh 验证角色、任务归属、仓库成员关系、Context Grant 和权限；
6. RepoMesh 为该 Run 准备独立 Git worktree，并物化只读上下文；
7. Runner 通过协议 Driver 启动 Coding CLI，执行测试并在成功后创建 commit；
8. Runner 把接受和终态事件写回 RepoMesh，Task 状态由持久化事件推进。

当前 Runner 的已验证 profile 包括 `claude-code`、`codex` 和 `kimi`。它们分别复用
stream-json、app-server 和 ACP 协议 Driver。

### 2.3 当前缺口

- Coding CLI 不是 Matrix 成员，不能直接参与房间对话；
- CoPaw/OpenClaw Worker 只是房间身份和 MCP 触发外壳，容易产生“双 Agent 人格”；
- Runner v1 只发布接受事件和一个终态事件，没有房间进度流；
- `input_required` 会把 Task 置为 blocked，但没有“在房间提问—收到授权回答—恢复原会话”
  的完整闭环；
- 默认 Compose 尚无 Runner 消费者，完整执行编排仍需收尾；
- RepoMesh 主分支没有可直接启动的外部 Coding CLI Bridge。

## 3. 目标与非目标

### 3.1 目标

1. 一个本地 Coding Agent 对应一个 AgentTeams Worker 身份和 Matrix 用户；
2. 其他 Agent 和人可以在授权房间中提及、追问并继续同一个原生 CLI 会话；
3. 正式代码修改必须绑定 RepoMesh Task、Worker、Repository、Run 和 Context Grant；
4. 每个 Run 使用独立 worktree，多个 Worker 可以并行而不共享可写目录；
5. 房间展示经过整理的进度、工具动作、问题、测试和结果，不展示模型内部推理；
6. Matrix 重投、Bridge 重启和网络闪断不能造成同一回合或同一 Task 重复执行；
7. Claude Code、Codex、Kimi 通过现有协议 Driver 接入，而不是各自重写 Matrix、文件同步和
   任务状态机。

### 3.2 非目标

- 不把 Matrix 消息历史变成 Project 或 Task 的事实来源；
- 不允许一句普通房间消息直接获得写仓库权限；
- 不允许 Bridge 自行扩大 allowed paths、tools、network targets 或凭据范围；
- 不转发隐藏推理、原始系统提示、密钥、完整环境变量或未经脱敏的命令输出；
- 不在第一阶段支持一个进程承载多个互不信任组织的 Worker 身份；
- 不为每个 Coding CLI 新增一个 AgentTeams 原生 runtime。

## 4. 推荐模块与接口

### 4.1 模块定位

建议新增一个独立的一方运行时模块，而不是把 Matrix 逻辑直接塞进冻结的 Runner v1：

```text
components/repomesh-agent-bridge/     # 构建、运行和部署说明
src/repomesh_agent_bridge/            # Python 实现
contracts/agent-bridge/v1/             # 进程间和注册契约
```

原因：`contracts/runtime/v1/worker-runtime.md` 明确规定 Runner v1 忽略 Matrix 凭据，并以
Runner 为 PID 1。直接改变该语义会破坏冻结契约。独立 Bridge 可以组合现有 Runner 执行
能力，同时保持 Matrix 生命周期与 Runtime v1 的职责清晰。

该模块应当是一个深模块：调用方只需要提供一次 Enrollment 并启动运行循环，内部隐藏
Matrix 同步、事件去重、会话恢复、任务租约、Driver 选择、进度投影和结果提交。

建议外部接口保持为：

```python
class RoomNativeAgent:
    async def run(self, enrollment: ExternalWorkerEnrollment) -> None: ...
```

`ExternalWorkerEnrollment` 只携带引用和非秘密绑定信息：Worker、组织、允许的房间、
RepoMesh endpoint、Coding profile 和凭据引用。真实 Matrix token、模型密钥和 SCM 凭据由
本机凭据解析器在运行时取得，不写入任务、Matrix 消息或日志。

### 4.2 内部 seams 与 adapters

Bridge 的外部接口保持小而稳定，以下变化点作为实现内部 seams：

| Seam | 生产 Adapter | 测试 Adapter | 职责 |
|---|---|---|---|
| `RoomPort` | Matrix Client-Server Adapter | In-memory Room Adapter | 同步消息、发送提及和可观察摘要 |
| `GovernedTaskPort` | RepoMesh MCP/HTTP Adapter | In-memory Task Adapter | 验证任务、领取执行、提交控制请求 |
| `CodingSessionPort` | 现有 Runner Driver/Profile Adapter | Scripted Driver Adapter | 启动、继续、取消本地 CLI 会话 |
| `ExecutionEventPort` | Runner Event Sink Adapter | Recording Event Adapter | 发布有序、幂等、结构化执行事件 |
| `CredentialPort` | 本机凭据库/环境引用 Adapter | Fake Credential Adapter | 解析引用，避免秘密进入业务消息 |
| `BridgeStatePort` | SQLite 或受限本地状态 Adapter | In-memory State Adapter | Matrix cursor、事件 inbox、会话引用和恢复状态 |

这些 seams 不应暴露成 Bridge 的公共接口；调用方不需要理解 Matrix `/sync`、CLI JSON-RPC
或事件序列号。

## 5. 身份、房间与会话模型

### 5.1 一对一绑定

第一阶段采用最容易审计的绑定：

```text
一个 AgentTeams Worker
      ↕ 1:1
一个 Bridge 实例
      ↕ 1:1
一个 Coding CLI profile
```

例如：

```text
pricing-codex-worker  →  本机 Bridge A  →  Codex app-server
pricing-claude-worker →  本机 Bridge B  →  Claude stream-json
```

一个守护进程同时承载多个 Worker 可以作为后续优化，但每个 Worker 必须有独立状态目录、
Matrix token、会话映射和凭据范围，不能共享隐式全局状态。

### 5.2 会话键

建议把会话分为两类：

- **房间对话会话**：键为 Worker + Room，用于一般问答和长期协作；
- **正式任务会话**：键为 Worker + Task + Run，用于受治理的编码，必须绑定 worktree 和
  Context Grant。

普通对话会话不得自动继承正式任务的可写 worktree。正式任务完成后，可以把经过整理的
摘要写回房间会话，但不能把凭据或未授权文件内容混入房间上下文。

### 5.3 消息分类

Bridge 收到提及时先分类，不直接执行：

| 消息类型 | 行为 |
|---|---|
| 一般问题 | 在只读、无仓库写权限的对话会话中回答 |
| 任务引用 | 向 RepoMesh 验证 Task、Worker 和房间绑定，成功后进入正式任务会话 |
| 任务内追问 | 仅在发送者有权回答且 Run 正在等待输入时恢复会话 |
| 取消/暂停 | 走 RepoMesh 控制动作，不能只杀本地进程后不记状态 |
| 未授权“直接改代码” | 拒绝执行并提示先创建或批准 RepoMesh Task |
| 其他 Agent 的状态消息 | 可作为对话上下文，但不改变 RepoMesh Task 状态 |

## 6. 核心流程

### 6.1 注册与启动

1. RepoMesh 或管理员在 AgentTeams 创建 `containerManaged: false` Worker；
2. Worker 加入一个 Repository Team，Controller 创建 Matrix 身份和房间关系；
3. 管理员在本机完成一次安全 Enrollment，取得短期注册码或凭据引用；
4. Bridge 验证 AgentTeams Controller、Matrix 和 RepoMesh 的身份绑定；
5. Bridge 保存最小本地状态后开始 Matrix 增量同步；
6. Bridge 宣告 ready；如果 Bridge 离线，AgentTeams 不伪造容器 ready 状态。

Enrollment 具体凭据交换需要在实施前单独冻结契约。不得要求用户把 Matrix token 或模型
密钥粘贴到团队房间。

### 6.2 房间对话

1. Matrix Adapter 收到带 Worker 提及的事件；
2. inbox 用 Matrix event id 去重，并校验房间是否在 allowlist；
3. Bridge 把清洗后的消息送入该 Room 的本地 CLI 会话；
4. CLI 输出被翻译为可观察消息，不发送内部推理；
5. outbound 使用稳定 transaction id 发回原房间；
6. Bridge 保存 Matrix cursor 和 native session id，重启后继续。

### 6.3 正式编码任务

1. Repository Leader 在 Team 房间提及 Worker，并附带 RepoMesh Task 引用；
2. Bridge 调用 RepoMesh，校验 Worker 是 assignee，Task Spec 已批准，Team 与 Repository 匹配；
3. RepoMesh 创建或返回同一 in-flight Run，准备独立 worktree 和只读 Context；
4. Bridge 取得 Runtime v1 RunnerTask，使用现有 profile 启动本地 CLI；
5. Driver 的可观察事件被同时投影为：
   - Runner 有序事件，写回 RepoMesh；
   - 房间可观察摘要，供团队了解进展；
6. 只有路径检查和测试通过后才创建 commit；
7. 终态写入 RepoMesh 后，Bridge 在房间发布结果摘要和证据引用；
8. Repository Leader 继续审查，Bridge 不代替 Leader 批准自己的结果。

### 6.4 问题与恢复

1. CLI 发出需要输入或权限的协议事件；
2. Bridge 先应用 RunnerTask deny 规则；被禁止的工具或路径直接拒绝，不能询问房间绕过；
3. 真正需要人或 Leader 判断的问题变成 RepoMesh `input_required` 控制请求；
4. Bridge 在授权房间发送结构化问题，附带短期可回答引用；
5. 只有有权限的发送者回答才会被接受；
6. RepoMesh 记录决策后，Bridge 使用 native session id 恢复同一 CLI 会话；
7. 重复回答、过期回答和其他房间的回答均被幂等拒绝。

## 7. 房间可见性策略

房间输出的原则是“可观察，不泄露内部推理”。

### 7.1 可以发送

- 已接收任务、开始执行、暂停、恢复和终态；
- 当前阶段，例如读取、修改、验证、等待输入；
- 工具名称和经过脱敏的目标摘要；
- changed files、测试命令与退出状态；
- blocker、需要回答的问题、commit 和交付证据引用；
- 简短的结果说明。

### 7.2 不可以发送

- chain-of-thought、隐藏分析或供应商原始 reasoning 字段；
- 系统提示、完整 Context Bundle、密钥或凭据值；
- 未脱敏的环境变量、HTTP header、访问 token；
- 可能包含秘密的整段 stdout/stderr；
- 未经授权的源文件全文；
- CLI 原始协议帧。

建议定义版本化的 `repomesh.room-observation.v1` 消息，而不是让每个 Driver 拼自然语言。
Matrix 文本只是该消息的显示投影，结构化事实仍落 RepoMesh。

## 8. 安全与治理约束

### 8.1 执行门禁

Bridge 不能因为 Matrix 中出现“请修改代码”就打开写权限。正式执行至少需要：

- Worker 身份与 Matrix sender/recipient 绑定有效；
- Worker 是 Task assignee；
- Project、Repository 和 Team 拓扑一致；
- Task Spec 已批准；
- Context Grant 未过期且绑定同一 Run；
- allowed paths、denied paths、tools 和 network policy 均来自 RepoMesh；
- worktree 已由平台准备且位于允许的 workspace root 下。

### 8.2 本机隔离

- 每个 Worker 使用独立 OS 用户或至少独立状态目录；
- 每个 Run 使用独立 worktree；
- Bridge 不把整个用户主目录暴露给 Coding CLI；
- 模型、SCM 和 Matrix 凭据分开授权；
- 不使用 CLI 原生全局 bypass flag 关闭协议权限回调；
- Bridge 退出时应终止它启动的整个 CLI 进程组；
- 本地状态文件限制权限，并支持凭据轮换和 Worker 撤销。

### 8.3 幂等与恢复

至少保留三层稳定键：

| 层 | 幂等身份 |
|---|---|
| Matrix 入站 | room id + event id |
| CLI 对话回合 | Worker + native session + Matrix event id |
| 正式执行 | RunnerTask idempotency key + event sequence |

Bridge 重启后先恢复 Matrix cursor、inbox 和 active session，再继续消费；不能通过重新读取整段
timeline 来猜哪些任务尚未执行。

## 9. 与现有代码的复用关系

建议复用：

- `src/repomesh_runner/drivers/`：协议监督、权限回调、终态判断；
- `src/repomesh_runner/profiles.py`：Claude Code、Codex、Kimi profile；
- `src/repomesh/integrations/runner/`：RunnerTask 投影、worktree、Context 物化与事件网关；
- `src/repomesh/integrations/agentteams/matrix.py`：Matrix transaction id 和提及格式；
- `src/repomesh/modules/collaboration`：角色方向、房间路由和投递记录；
- AgentTeams `containerManaged: false` Worker 与 Team reconciliation。

不建议复用或扩张：

- 不把 AgentTeams Manager 原生 `plan.md` 当 RepoMesh Project 真相；
- 不复制 CoPaw/Hermes 各自的一整套 Matrix、同步和任务状态实现；
- 不让 Bridge 直接写 task_orchestration 数据表；
- 不让业务模块导入 AgentTeams、Matrix 或 CLI 实现；
- 不修改 Runtime v1 的既有语义来偷偷接收 Matrix 凭据。

实验分支 `feat/agentteams-external-cli-runtimes` 已完成过远程成员 E2E，证明方向可行；实施时
应审计并提取可复用设计，不应未经当前契约核对就整体合并。

## 10. 分阶段实施路线

### 阶段 0：冻结契约与 ADR

- 新增 ADR，明确 Bridge 是独立进程，不改变 Runtime v1；
- 定义 External Worker Enrollment、Room Observation 和控制请求契约；
- 明确 Matrix 身份、RepoMesh AgentPrincipal 和本机实例的绑定与撤销流程；
- 补架构依赖测试，确保业务模块只依赖 contracts。

验收：契约样例可机读；没有秘密字段；错误和重试语义明确。

### 阶段 1：只读房间成员 MVP

- 支持一个外部 Worker、一个房间、一个 CLI profile；
- 接收提及、维护 native session、发送回答；
- 禁止仓库写入和工具执行；
- 支持 Matrix 去重、断线重连和 Bridge 重启恢复。

验收：重复 Matrix 事件只产生一个 CLI 回合；重启后可继续同一会话；未提及消息不触发执行。

### 阶段 2：单任务受治理编码

- 接入 RepoMesh Task 验证和 Runtime v1 队列；
- 复用 worktree、Context、permissions、Driver、测试和 commit；
- 把终态证据同时写入 RepoMesh 和房间摘要；
- 首先支持 Codex，再接 Claude Code；Kimi 使用同一接口验证第三个 Adapter。

验收：无批准 Task 不能改代码；路径或测试失败不产生 commit；相同任务重投不重复执行。

### 阶段 3：问题、回答与会话恢复

- 把 `input_required` 投影成房间问题；
- 校验回答者权限和房间；
- 支持取消、暂停、恢复以及原生 session resume；
- 超时后保持 blocked，不默认批准。

验收：错误房间、错误角色、重复或过期回答不能恢复任务；正确回答恢复同一原生会话。

### 阶段 4：多 Worker 与生产硬化

- 支持多个 Bridge 实例和多种 CLI profile；
- 增加安装器、systemd/Windows Service 启动方式和健康检查；
- 增加凭据轮换、Worker 撤销、审计与 OTLP 观测；
- 完成 Compose/Helm 与本机 Bridge 的联调文档；
- 运行真实 Matrix、真实 CLI、真实 Git 仓库的端到端套件。

验收：单个 Bridge 故障不影响其他 Worker；撤销后不能再收任务或发消息；并行任务 worktree
互不污染。

## 11. 测试策略

测试只穿过 Room-Native Coding Agent 的外部接口，不以目录结构为验收对象。

### 11.1 行为测试

- 提及触发、非提及忽略；
- Matrix 事件重复、乱序和重连；
- Worker、Room、Task、Repository 绑定不匹配；
- 普通聊天不得获得写权限；
- Task 重投与 Bridge 重启的 at-most-once；
- CLI 成功、失败、超时、中断和 input-required；
- allowed/denied paths 与 tools 的优先级；
- 测试失败不创建 commit；
- 输出脱敏与禁止 reasoning 投影；
- 回答授权、过期和恢复原会话。

### 11.2 Adapter 契约测试

- Matrix Adapter 与 in-memory Room Adapter 运行同一组行为契约；
- RepoMesh HTTP/MCP Adapter 与 in-memory Task Adapter 运行同一组授权及幂等契约；
- Codex、Claude Code、Kimi 继续使用现有 scripted process 与真机 smoke；
- 本地状态 Adapter 验证 crash-before-write、write-before-send 和重复启动恢复。

### 11.3 端到端验收场景

```text
人类在 Team 房间 @Codex Worker
  → Codex Worker 直接回复并保持会话
  → Repository Leader 分配已批准 Task
  → 同一 Worker 获取独立 worktree
  → Codex 执行并发布可观察进度
  → Codex 提问，Leader 在房间回答
  → 原会话恢复
  → 测试通过并创建 commit
  → RepoMesh 记录结构化证据
  → Worker 在房间发布结果摘要
  → Leader 审查，Worker 不能自批
```

## 12. 主要风险与处理

| 风险 | 处理 |
|---|---|
| 聊天命令绕过 Task 审批 | 对话与正式任务使用不同会话和能力；写操作必须有 RepoMesh Grant |
| Matrix 重放造成重复执行 | event inbox + stable turn key + RunnerTask idempotency key |
| 多身份共进程导致上下文泄漏 | MVP 一 Worker 一实例；状态与凭据目录物理隔离 |
| 原始 CLI 输出泄密 | 结构化 observation allowlist；默认不发送 stdout/stderr 全文 |
| Bridge 离线但 Team 显示可用 | 单独建 external heartbeat/readiness；不把 CR 存在等同于在线 |
| Agent 在房间自称完成但 Runner 未完成 | RepoMesh 只接受 Runner 终态与证据，房间文本不推进 Task |
| Driver 实现被 Bridge 复制后漂移 | 提取稳定 Coding Session interface，Bridge 与 Runner 共用实现 |
| Runtime v1 被隐式破坏 | Bridge 独立契约；需要改变 Runner 语义时新建 v2 |

## 13. 待冻结决策

实施前需要形成 ADR 的决策包括：

1. Bridge 是每个 Worker 一个进程，还是一个 supervisor 管多个隔离子进程；
2. 外部 Worker 的 Matrix token 通过一次性 Enrollment、AgentTeams 存储还是专用凭据提供器取得；
3. 房间对话 native session 只保存在本地，还是把不含秘密的 session reference 持久化到
   RepoMesh Agent Runtime；
4. Room Observation 使用 Matrix 自定义事件，还是结构化 JSON 文本加显示投影；
5. `input_required` 的授权回答者是 Repository Leader、显式 Human Grant，还是两者；
6. MVP 先支持 Windows 本机还是 Linux systemd；
7. 实验分支中哪些实现可以复用，哪些必须按现有 Runner 契约重写。

## 14. 最终建议

建议采用“外部托管 Worker + 独立 Bridge + 现有 Runner 治理”的组合，不新增
`runtime: codex`、`runtime: claude-code` 等 AgentTeams 原生 runtime。

第一阶段先证明身份与对话：让一个 Codex Worker 在真实 Team 房间中可被提及、可连续回答、
可重启恢复，但完全没有仓库写权限。第二阶段再接入 RepoMesh Task 和现有 Runner Driver，
形成单任务的受治理闭环。这样把最难的两件事——房间身份和代码治理——分别验证，再在稳定
接口上组合，避免一次性把 Matrix、CLI、Git、权限和任务状态耦合成不可测试的大实现。

最终产品体验应是：用户和 Agent 看到的是一个能说话、能被追问、能展示工作进度的 Coding
Worker；RepoMesh 看到的仍是一个只能在已批准 Task 和隔离 worktree 中执行、必须用测试和
commit 证明结果的受治理执行者。

## 15. 改动范围、时间与成本评估

### 15.1 结论

这是一个**中大型但边界可控的跨模块功能**，不是 AgentTeams 或 RepoMesh 的重写。

- 不需要修改 AgentTeams Go Controller 的核心 reconciliation；
- 不需要新增 CRD runtime 枚举；
- 不应修改冻结的 Runtime v1 语义；
- 新代码主要集中在独立 Bridge 模块；
- RepoMesh 需要在 Agent Runtime、Collaboration、AgentTeams projection、composition root 和
  测试中增加小到中等规模的契约与接线；
- 最大的不确定性不是 CLI 启动，而是 Enrollment、Matrix 重放、会话恢复、授权回答和跨平台
  运维。

如果只做演示，已有实验分支可以把时间压到数天；如果要进入主分支并达到可长期运行的质量，
建议按 **33–51 工程人日** 预算。单人约 7–11 周，两名熟悉代码库的工程师并行约 4–7 个
日历周。若再要求 Windows/Linux 安装器、凭据轮换、生产监控和多 Worker 隔离，建议保留
8–12 周的产品化窗口。

以上不包含等待 AgentTeams 上游评审的时间；按本仓 first-party fork 落地则不依赖上游合并。

### 15.2 历史实现基线

实验分支 `feat/agentteams-external-cli-runtimes` 已给出真实工作量基线：

- 12 个功能/修复提交；
- 38 个文件；
- 约 9,613 行新增、136 行删除；
- Bridge core、supervisor、dedup、session store、Claude Code/Codex Driver 与 projector；
- 超过一百项远程成员测试，并完成真实 Matrix + Claude Code/Codex E2E。

这证明方案不是研究性猜想，也说明“只加一个开关”远远不够。该分支重复实现了部分 CLI
Driver 与资产投影；本报告方案复用现有 `repomesh_runner` Driver，可以减少约一部分
runtime-specific 代码，但要增加 RepoMesh Task、Context、worktree、Runner event 和
input-required 的正式集成。因此总工作量不会缩成小补丁。

### 15.3 分阶段人日估算

| 工作包 | 主要产出 | 估算 |
|---|---|---:|
| 契约与 ADR | Enrollment、Room Observation、身份绑定、重试与错误语义 | 2–3 人日 |
| 外部 Worker projection | `containerManaged: false` 投影、读取、冲突检查和契约测试 | 2–4 人日 |
| Bridge 核心 | Matrix inbox/outbox、邀请加入、cursor、dedup、turn ledger、状态恢复 | 6–9 人日 |
| Coding Session 复用 | 从 Runner 提取稳定会话 interface，接 Codex/Claude profile 和 observer | 4–6 人日 |
| RepoMesh 治理接入 | Task 验证、worktree、Context、权限、Runner event、commit 结果 | 6–9 人日 |
| 问题与恢复 | input-required、房间授权回答、resume、取消和超时 | 4–7 人日 |
| 安全与可观察性 | 脱敏、凭据引用、health/readiness、日志和 trace | 3–5 人日 |
| 安装与跨平台 | Windows Service 或启动脚本、Linux systemd、配置诊断 | 3–5 人日 |
| 集成测试与真实 E2E | Matrix、CLI、Git、重启、重复事件、多 Worker 并行 | 3–5 人日 |
| **合计** | 可进入主分支并稳定演示的完整方案 | **33–53 人日** |

不同工作包可以部分并行，但契约、Bridge core 和 Coding Session interface 是前置路径，不能
简单按人数线性压缩。

### 15.4 三种交付档位

#### 档位 A：可演示原型

范围：一个外部 Worker、一个 Team 房间、Codex 或 Claude Code 二选一、手工配置、本地运行，
能收提及、执行一个 turn 并回房间。

- 时间：2–5 人日；
- 主要复用实验分支；
- 不承诺 RepoMesh Task 治理、完整重启恢复、凭据轮换或生产安全；
- 适合验证产品体验，不适合合入正式交付链。

#### 档位 B：受治理 MVP

范围：一个 CLI、一个外部 Worker，房间对话 + RepoMesh 正式 Task、独立 worktree、测试、
commit、事件回写、基本 dedup 与重启恢复。

- 时间：15–24 人日；
- 单人约 3–5 周，两人约 2–3 周；
- 可以进入主分支并用于受控演示；
- 暂不包含完整 input-required 对话、多 Worker supervisor 和双平台安装器。

#### 档位 C：产品化版本

范围：Codex + Claude Code、多 Worker、问题/回答/恢复、撤销与凭据轮换、Windows/Linux
安装、可观察性、故障注入和真实 E2E。

- 时间：33–53 人日；
- 单人约 7–11 周；
- 两名熟悉 RepoMesh/AgentTeams 的工程师约 4–7 个日历周；
- 加上验收缓冲和实际部署差异，按 8–12 周产品窗口规划更稳妥。

### 15.5 预计改动面

| 区域 | 改动级别 | 说明 |
|---|---|---|
| 新 Bridge 模块与契约 | 大 | 新能力主体，预计占新增代码的 50% 以上 |
| `repomesh_runner` Driver/Profile | 小到中 | 主要是提取稳定 Coding Session interface，避免复制 Driver |
| AgentTeams Python projection/client | 小 | 增加 `containerManaged` 字段和匹配检查；Go Controller 无需改动 |
| Agent Runtime | 中 | 外部会话引用、控制请求、执行绑定和结果投影 |
| Collaboration | 中 | Room Observation、回答授权、消息去重和路由 |
| Task Orchestration / Context | 小 | 复用既有门禁，只增加 Bridge 调用路径和契约测试 |
| Bootstrap / Settings | 小到中 | Bridge endpoint、Enrollment、Adapter 组合与健康检查 |
| Compose / 安装脚本 | 中 | 本机进程与平台的连接、诊断和跨平台启动 |
| 前端 | 可选，小 | 展示 external/online 状态、问题与回答；MVP 可先不改 |
| 数据库迁移 | 小到中 | 若 session reference、inbox 或控制请求持久化在 RepoMesh，需模块自有表 |

预计产品化版本会触及约 35–60 个文件。由于大部分是新增文件，真正修改已有核心逻辑的比例
较低；风险主要来自跨进程语义，而不是大面积改写现有业务模块。

### 15.6 明确不需要改动的范围

- AgentTeams Go Controller 的容器跳过逻辑已经存在；
- Team、Worker、Matrix 房间的基本 reconciliation 不需重写；
- Git mirror/worktree 管理可以直接复用；
- Claude Code、Codex、Kimi 的协议解析不应重写；
- RepoMesh Project、Specification、Task 的事实来源不变；
- Runner 的路径检查、测试和 commit 策略不变；
- Matrix 仍不是任务队列或进度事实库。

### 15.7 最高风险项

1. **Enrollment 与凭据交付**：外部 Worker 如何安全取得 Matrix、存储和 RepoMesh 调用凭据；
2. **重放与崩溃一致性**：首次 sync、timeline gap、ack 水位线和 turn ledger；
3. **会话双轨**：房间对话 session 与正式 Task session 必须隔离，防止权限串味；
4. **input-required 闭环**：谁能回答、回答从哪个房间来、如何恢复同一 native session；
5. **Windows 运行差异**：stdio 编码、进程组终止、路径与本机 CLI 登录态；
6. **现有执行面未收尾**：默认 Compose 没有 Runner 消费者，应决定 Bridge 是否同时成为本地
   Runner host，避免维护两个竞争消费者；
7. **实验分支与 main 漂移**：应提取设计、测试场景和可靠性实现，不建议整体 merge。

### 15.8 推荐排期与人员拆分

如果安排两名工程师：

```text
第 1 周：共同冻结契约和 ADR
         A：外部 Worker projection + Enrollment
         B：Bridge state + Matrix inbox/outbox

第 2 周：A：Coding Session interface + Codex
         B：dedup、邀请、重启恢复

第 3 周：共同接入 RepoMesh Task/worktree/Context/Runner events

第 4 周：input-required、取消、resume、脱敏与安全测试

第 5 周：Claude Code、多 Worker、安装脚本、真实 E2E

第 6–7 周：故障修复、跨平台验证、文档和验收缓冲
```

推荐先批准档位 B，而不是直接承诺档位 C。受治理 MVP 能最快回答三个关键问题：房间体验是否
真的有价值、Bridge 与 Runner 能否共用同一会话 interface、以及用户是否愿意在本机长期运行
外部 Agent。三个答案都成立后，再投入多 Worker、安装器和生产硬化成本。

## 16. 具体改动入口与 PR 顺序

### 16.1 第一个可运行目标

第一版只做：

```text
一个 Codex 外部 Worker
+ 一个已存在的 Repository Team 房间
+ 本机一个 Bridge 进程
+ 被 @提及时继续同一 Codex thread
+ Bridge 重启后不重放旧消息
+ 完全没有仓库写权限
```

选择 Codex 先行的原因：现有 app-server Driver 已能持续产生 text、tool、permission 和 session
事件，会话 thread id 也已验证；第一阶段可以只启用 text，屏蔽工具和正式执行。不要同时接
Claude Code，也不要先做多 Worker supervisor。

### 16.2 PR 1：补齐外部 Worker 投影

目标：RepoMesh 能创建或确保一个 `containerManaged: false` 的 AgentTeams Worker，并能读回和
检查该属性，Controller 仍保持零改动。

主要文件：

```text
src/repomesh/modules/agent_runtime/contracts.py
src/repomesh/modules/agent_runtime/ports/agent_team.py
src/repomesh/integrations/agentteams/control_plane.py
src/repomesh/integrations/agentteams/runtime_projection.py
tests/contracts/test_agentteams_integration.py
tests/integrations/agentteams/
```

改动：

1. `WorkerProjection` 增加 `container_managed: bool = True`；
2. `WorkerRuntimeRef` 增加观测字段；
3. AgentTeams client 的 create、get、match 全部携带或检查 `containerManaged`；
4. 新增一个显式 external Worker provisioning 用例，不把所有普通 Worker 默认改成 external；
5. 契约测试证明默认值仍是 managed，只有显式 external 才为 false；
6. 真实 smoke 证明 Controller 创建身份和房间但不创建容器。

此 PR 不创建 Bridge，不启动 CLI，不改数据库。

### 16.3 PR 2：冻结 Bridge v1 契约并建空骨架

目标：先固定身份、输入、输出和恢复语义，再写循环。

新增：

```text
contracts/agent-bridge/v1/
├── external-worker-enrollment.schema.json
├── room-observation.schema.json
└── README.md

components/repomesh-agent-bridge/
├── README.md
└── component.toml

src/repomesh_agent_bridge/
├── __init__.py
├── application.py
├── contracts.py
└── ports.py
```

外部 interface 只保留：

```python
class RoomNativeAgent:
    async def run(self, enrollment: ExternalWorkerEnrollment) -> None: ...
```

内部先定义 `RoomPort`、`CodingSessionPort`、`BridgeStatePort`，同时提供 in-memory adapters。
这一 PR 的测试只穿过 `RoomNativeAgent` interface，验证非法 Enrollment 和空运行生命周期。

### 16.4 PR 3：从实验分支提取 Matrix 与可靠性核心

目标：一个 fake Coding Session 能在真实语义下收提及、回消息、去重和重启恢复。

从 `feat/agentteams-external-cli-runtimes` 选择性提取：

- cursor 与首次同步基线；
- joined rooms 与受信邀请处理；
- bounded seen-set；
- `(task-or-thread, trigger-event)` turn ledger；
- session store；
- 确定性 Matrix transaction id；
- supervisor 的取消和进程收尾思路。

不要在这一 PR 提取该分支的 Claude/Codex Driver 和 projector；main 已有更新的 Runner Driver，
复制会产生两套协议实现。

新增主要实现：

```text
src/repomesh_agent_bridge/adapters/matrix.py
src/repomesh_agent_bridge/adapters/sqlite_state.py
src/repomesh_agent_bridge/inbox.py
src/repomesh_agent_bridge/supervisor.py
tests/agent_bridge/
```

验收：首轮不执行历史消息；同一 event 重放只执行一次；send 后 crash 再启动不产生重复房间
消息；只响应明确提及且房间在 Enrollment allowlist 内的事件。

### 16.5 PR 4：提取 Coding Session interface，先接 Codex 对话

目标：复用当前 Runner app-server Driver，而不是重新实现 Codex 协议。

主要改动点：

```text
src/repomesh_runner/drivers/base.py
src/repomesh_runner/drivers/app_server.py
src/repomesh_runner/profiles.py
src/repomesh_agent_bridge/adapters/coding_session.py
tests/runner/
tests/agent_bridge/
```

现有 `ProtocolDriver.execute(request, profile, observer)` 已有可观察事件和 native session id，
需要在不破坏 Runner 调用方的前提下，把“一个可恢复 turn”包装为稳定的
`CodingSessionPort` adapter。Bridge 只消费允许投影的 `TEXT` 和 `SESSION_STARTED`；
`THINKING`、`LOG` 和原始协议帧不得发送到房间。

第一版 permission policy 全部拒绝工具和写操作，只允许对话。验收是同一 Room 中第二次提及
能恢复同一 Codex thread，另一个 Room 不能串入该会话。

### 16.6 PR 5：接 RepoMesh 正式 Task

目标：把“会说话的 Codex Worker”升级为“受治理、能编码的 Codex Worker”。

Bridge 不直接创建 worktree，而是调用现有 `start_assigned_task`/Runner task source。需要决定
Bridge 是否直接作为该 external Worker 的 Runner consumer；建议是，避免再启动第二个本地
Runner 争抢同一 Worker 的任务。

复用：

```text
src/repomesh/integrations/runner/worker_execution.py
src/repomesh/integrations/runner/dispatch.py
src/repomesh/integrations/runner/task_projection.py
src/repomesh/integrations/workspace/git_worktree.py
src/repomesh/integrations/runner/gateway.py
```

Bridge 收到 Task assignment 后只传 Task id 和 Worker id；RepoMesh 继续派生 Run、Context、
permissions、worktree 和 idempotency key。Bridge 执行 Runtime v1 task，并把事件发回现有
Runner event sink。房间进度由 Driver observer 生成独立 Room Observation，不参与 Task 状态
推进。

验收：

- 普通聊天要求改代码会被拒绝；
- 非 assignee 不能启动；
- allowed path 之外的改动失败；
- 测试失败不创建 commit；
- 相同 Task 重投复用 in-flight Run；
- 房间里声称“完成”不能替代 Runner terminal event。

### 16.7 PR 6：问题、恢复与产品化

最后再补：

- `input_required` 到房间问题的投影；
- 回答者角色和房间授权；
- native session resume；
- 取消、暂停、超时；
- Claude Code adapter 验证；
- Windows Service/Linux systemd；
- Enrollment、凭据轮换、health/readiness、OTLP 和真实 E2E。

### 16.8 第一周建议动作

```text
Day 1：建立 ADR，冻结“Bridge 独立进程、不改 Runtime v1、Codex 先行”
Day 2：完成 WorkerProjection.container_managed 与 AgentTeams client 契约测试
Day 3：真实创建一个 external Worker，验证有 Matrix 身份、无容器、可进 Team
Day 4：建立 Bridge v1 骨架和 in-memory Room/State adapters
Day 5：用 fake Coding Session 跑通 mention → reply → dedup → restart 测试
```

第一周结束时应有一个没有代码权限、但接口和可靠性方向正确的房间 Agent。只有这个里程碑
通过，第二周才接真实 Codex；不要在第一周同时处理 Git、Context、测试或 commit。
