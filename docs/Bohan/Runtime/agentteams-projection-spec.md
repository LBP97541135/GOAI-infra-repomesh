# AgentTeams 资源投影规范 v0.1（ATP-01 契约基线）

- 日期：2026-08-04
- 状态：草案，待 runtime-integrations 与 orchestration Owner 评审后冻结
- 决策依据：ADR 0002、`docs/architecture/runtime-planes.md`、`docs/development/team-handoff.md` §5
- CRD 事实源：`components/agentteams/agentteams-controller/api/v1beta1/types.go`（v1.2.0）
- Owner：runtime-integrations（投影与适配）、orchestration（可见性输入）、platform（绑定持久化）

## 1. 目的与范围

定义 RepoMesh 产品控制面向 AgentTeams 运行时控制面投影资源的完整契约：映射哪些字段、
不映射哪些字段、投影流程、幂等规则、状态回读和存储凭据边界。

范围内：Worker / Team / Manager / Human 四种 CRD 的写方向投影、状态回读、
资源绑定持久化、对象存储授权面。

范围外：Go 侧的策略执行改造（ATG-01）、Runner 的任务传输与事件回传（RUN 系列）、
Matrix 消息协作语义（collaboration 模块）。

## 2. 映射总规则（八条，全部可测试）

1. **单向投影**。RepoMesh 计算期望状态并投影给 AgentTeams；AgentTeams 的资源和状态
   只作为运行观察回读，永远不是业务事实源。账本丢失时可以照着 RepoMesh 重建全部
   AgentTeams 资源，反向不成立。
2. **只投影业务推导的期望**。部署形态旋钮（`deployMode`、`backendRuntime`、
   `containerManaged`、`volumes`、`mounts`、`expose`、`channels`、`package`）属于
   bootstrap 部署配置，禁止进入投影类型。
3. **先持久化，后副作用**。任何投影命令发出前，CodingRun 与资源绑定必须已在同一
   事务中落库，命令经 outbox 至少一次投递。
4. **全链路幂等**。资源名确定性生成（§7），投影命令携带由绑定和 spec hash 派生的
   幂等键，重放产生相同结果。
5. **凭据只投影引用**（`credentialBindings` / `accessEntries`），永不投影值。
   回读状态中的敏感字段（`Human.Status.InitialPassword`）必须在适配层丢弃，
   不得进入事件、日志或数据库。
6. **AgentTeams 不得自行扩权**。skills 列表、房间成员、桶前缀权限都以投影为上限；
   投影层永不发送空 `accessEntries`（空值会触发 Controller 的默认授权，见 §6.3）。
7. **权限语义不走环境变量**。`spec.env` 只承载传输层配置（任务来源、事件端点）；
   Runner 不得将任何继承的环境变量（如 `AGENTTEAMS_YOLO`）解释为权限来源，
   权限模式只来自 RunnerTask。
8. **漂移可检测**。每次投影记录期望 spec hash；回读的 `status.specHash` 与期望
   不一致时产生审计事件并触发重投影（§7.3）。

## 3. 投影流程（端到端）

```text
1. 触发
   Task Orchestration 请求执行 / 项目成员变化 / 撤权
        |
2. 可见性计算（orchestration / context）
   生成不可变 VisibilitySnapshot：
   EffectivePermission = AgentPolicy ∩ ProjectMembership ∩ TaskSpec ∩ RunDelegation - ExplicitDeny
        |
3. 投影编译（agent_runtime，纯函数）
   (VisibilitySnapshot, TaskSpec, AgentProfile, 绑定记录)
     -> WorkerProjection / TeamProjection / HumanProjection
   同时计算 spec_hash = sha256(canonical_json(projection))
        |
4. 事务落库（platform）
   同一事务写入：CodingRun、AgentTeamsResourceBinding（资源名、kind、期望 spec_hash、
   状态 PROJECTED）、outbox 命令、StateEvent
        |
5. 投递（outbox publisher，至少一次）
   ensure_worker / ensure_team / ensure_human，idempotency_key = "atp:{binding_id}:{spec_hash}"
        |
6. 适配执行（integrations/agentteams）
   调用 Controller REST API，回读 RuntimeRef，剥离敏感字段，
   更新绑定为 OBSERVED 并记录 observed spec_hash / phase
        |
7. 对账循环（事件驱动 + 周期兜底）
   get_worker 比较 status.specHash 与绑定期望值：
   一致 -> 无操作；漂移 -> 重投影 + AuditEvent；资源消失 -> 重建或按策略告警
        |
8. 生命周期收尾
   Run 终态 -> state=Stopped（保留期后清理）
   普通撤权 -> 缩权后的新投影（新 spec_hash）
   紧急撤权 -> state=Stopped + 终止租约 + 吊销凭据引用，全部同步执行
```

投影编译（第 3 步）必须是无副作用纯函数，单独可测；副作用只存在于第 4-6 步。

## 4. 写方向字段映射

### 4.1 Worker（核心投影）

| 投影字段 | CRD 字段 | 来源 | 规则 |
| --- | --- | --- | --- |
| `name` | `metadata.name` | 绑定 ID | `rm-worker-{binding_id.hex}`，见 §7.1 |
| `model` | `spec.model` | Agent Profile / TaskSpec | 必填；执行时 driver 校验回显模型一致 |
| `model_provider` | `spec.modelProvider` | Agent Profile | 可选，网关模型路由名 |
| `image` | `spec.image` | 平台配置 | **必填**。RepoMesh Runner Worker 镜像，带 digest 引用；这是 Runner 进入 Worker 的载体 |
| `runtime` | `spec.runtime` | 固定值 | `repomesh-runner`（决策见 §10.1，实施见 `agentteams-runner-runtime-plan.md`） |
| `worker_name` | `spec.workerName` | 省略 | 默认不设，回落到 `metadata.name`；可读性优化推迟（§10.4） |
| `identity` / `soul` / `agents` | 同名 | 角色定义 | 角色提示注入，来自 Agent Directory |
| `skills` | `spec.skills` | VisibilitySnapshot | 允许集白名单；`find-skills`、`file-sync` 永不出现（§6.4） |
| `mcp_servers` | `spec.mcpServers` | VisibilitySnapshot | 全量声明式；Worker 镜像内不得存在宿主继承的 MCP 配置（codex `CODEX_HOME` 必须为空基线） |
| `channel_policy` | `spec.channelPolicy` | VisibilitySnapshot | 房间成员白名单增减，Matrix 路由由 RepoMesh 决定 |
| `resources` | `spec.resources` | TaskSpec | 编码任务按任务声明 CPU/内存 |
| `idle_timeout` | `spec.idleTimeout` | 不投影 | 留空即整体关闭 Controller auto-sleep（代码证据见 §10.3）；Worker 超时治理归 Runner idle watchdog 与 Task Orchestration |
| `state` | `spec.state` | Run 生命周期 | `Running` / `Sleeping` / `Stopped`，映射见 §5.3 |
| `access_entries` | `spec.accessEntries` | VisibilitySnapshot | 存储授权面，生成规则见 §6.3；**永不为空** |
| `credential_bindings` | `spec.credentialBindings` | Identity Access | 凭据引用 + 工具白名单，永不含值 |
| `env` | `spec.env` | 平台配置 | 仅传输配置（任务引用、事件端点、组织标识）；Controller 丢弃 `AGENTTEAMS_*` 冲突键的行为是可接受的 |
| `labels` | `spec.labels` | Runtime v1 元数据 | 见 §4.5 |

### 4.2 Team

| 投影字段 | CRD 字段 | 规则 |
| --- | --- | --- |
| `name` | `metadata.name` | `rm-team-{binding_id.hex}` |
| `description` | `spec.description` | 项目/Workstream 摘要 |
| `members` | `spec.workerMembers` | 引用已投影 Worker 的 `metadata.name`；恰好一个 `team_leader`（现有校验保留） |
| `human_members` | `spec.humanMembers` | 引用已投影 Human；来自项目成员中需进房间的人类 |
| `channel_policy` | `spec.channelPolicy` | 团队级房间白名单 |
| `peer_mentions` | `spec.peerMentions` | 由协作策略决定，默认关闭（RepoMesh 控制提问路由） |
| `heartbeat_every` | `spec.heartbeatEvery` | 现有字段保留 |
| — | `spec.admin` | 不单独投影；项目 PM 作为 `human_members` 成员进入 |

投影顺序约束：Team 引用的全部 Worker 绑定必须已处于 OBSERVED 状态，Team 投影才可发出。

### 4.3 Manager

现有 `ManagerProjection`（model / runtime / skills / soul / agents）字段足够。需要补充
`mcp_servers` 与 `config`（心跳间隔、闲置超时、通知通道）两项以对齐 CRD。

是否为 RepoMesh 部署 Manager 是待决事项（§10.2）。基线立场：部署**最小化 Manager**——
只保留房间协调与人类问答入口，任务管理类 skills（`task-coordination`、`task-management`、
`project-management`、`git-delegation-management`）从 skills 白名单中裁剪，因为任务
编排属于 RepoMesh Task Orchestration。

### 4.4 Human（当前完全缺失，必须新增）

| 投影字段 | CRD 字段 | 来源 | 规则 |
| --- | --- | --- | --- |
| `name` | `metadata.name` | `rm-human-{binding_id.hex}` | |
| `display_name` | `spec.displayName` | Identity Access 用户档案 | |
| `username` | `spec.username` | Identity Access | Matrix localpart |
| `email` | `spec.email` | Identity Access | |
| `permission_level` | `spec.permissionLevel` | 角色映射表 | 1=Admin / 2=Team / 3=Worker；由 identity_access 角色推导（§10.5） |
| `accessible_teams` / `accessible_workers` | 同名 | 权限交集输出 | Agent 可见性（§5.1 四类可见性第 1 类）的运行投影 |
| `identity_source` | `spec.identitySource` | SSO 配置 | 可选 |

**敏感字段规则**：`Human.Status.InitialPassword` 在适配层读取后立即丢弃，仅通过
一次性安全通道交付给对应人类；任何事件、日志、数据库不得记录。

### 4.5 关联元数据（labels）

Runtime v1 元数据（`contracts/runtime/v1/runtime-metadata.schema.json`）通过
`spec.labels` 携带，键使用统一前缀：

```text
repomesh.dev/schema-version = "runtime.v1"
repomesh.dev/organization-id = <uuid>
repomesh.dev/project-id      = <uuid>
repomesh.dev/task-id         = <uuid|absent>
repomesh.dev/run-id          = <uuid|absent>
repomesh.dev/correlation-id  = <uuid>
```

规则：labels 是关联和检索手段，**不是数据传输通道**；消费方（对账、观测）只用它
定位绑定记录，业务数据一律回 PostgreSQL 查询。Controller 系统标签
（`agentteams.io/*`）优先级更高的合并行为是可接受的，RepoMesh 前缀不与其冲突。

## 5. 读方向：状态回读

### 5.1 RuntimeRef 增补字段

`WorkerRuntimeRef` 现有 phase / runtime / room_id / matrix_user_id / message，增补：

| 字段 | CRD 来源 | 用途 |
| --- | --- | --- |
| `spec_hash` | `status.specHash` | 漂移检测（§7.3）的唯一手段 |
| `container_state` | `status.containerState` | 区分"Runner 活着但慢"与"容器已死" |
| `last_heartbeat` / `last_active_at` | 同名 | 卡死判定输入，供 Task Orchestration 决策恢复或重试 |
| `observed_generation` | `status.observedGeneration` | 确认 Controller 已处理最新期望 |

`TeamRuntimeRef` 增补 `leader_room_id`（`status.leaderDMRoomID`）与
`members: tuple[TeamMemberRuntimeRef, ...]`（`status.members[]`：name / role / phase /
ready / room_id / matrix_user_id / spec_hash / last_heartbeat）。聚合计数
（ready_workers / total_workers）保留，但故障定位以成员明细为准。

新增 `HumanRuntimeRef`：phase / matrix_user_id / rooms；不含 initial_password（§4.4）。

### 5.2 回读只更新绑定，不驱动业务

状态回读写入 `AgentTeamsResourceBinding` 的观察字段并产生 StateEvent；业务状态
（Run 的成败、任务的推进）只由 Runtime v1 事件驱动，禁止从资源 phase 推导业务结论。
例：Worker phase=Failed 只说明容器故障，Run 是否失败要看 Runner 事件或超时策略。

### 5.3 生命周期映射

| AgentTeams phase | 绑定状态 | RepoMesh 动作 |
| --- | --- | --- |
| Pending / Starting | PROVISIONING | 等待，超时告警 |
| Running（ready） | OBSERVED | 允许下发任务 |
| Updating | RECONCILING | 暂停新任务下发 |
| Sleeping | SUSPENDED | 仅当 RepoMesh 主动投影 Sleeping 时合法，否则视为漂移 |
| Stopping / Stopped | RETIRING / RETIRED | 校验与期望 state 一致，不一致即漂移 |
| Failed | DEGRADED | AuditEvent + 通知 Task Orchestration 决策 |

期望 state 的写方向映射：Run 活跃 → `Running`；等待人工输入且策略允许休眠 →
`Sleeping`（§10.6）；Run 终态或撤权 → `Stopped`。

## 6. 存储与凭据边界

### 6.1 三类存储空间

| 存储空间 | 所有者 | RepoMesh 态度 |
| --- | --- | --- |
| `agents/<name>/*`、`manager/*`、`shared/*`（含 `shared/tasks/`） | AgentTeams 运行时私有 | 不读、不写、不授权。等同于对方数据库 |
| Context Bundle 前缀（如 `repomesh-context/<bundle_version_id>/`） | RepoMesh Context 模块 | RepoMesh 写入不可变内容；Worker 只读 |
| Artifact 前缀（如 `repomesh-artifacts/<run_id>/`） | RepoMesh Runner 产出 | Worker 只写；RepoMesh 以 URI+SHA-256 引用入账 |

物理上共用一个 MinIO/S3 实例是 bootstrap 部署决策；逻辑边界以 bucket/前缀 + 凭据
scope 双重隔离。

### 6.2 自建组件（非映射）

Context Bundle 与 Artifact 存储通过 RepoMesh 自己的 object-store adapter 实现
（`integrations/` 下，实现 context 模块 `ports/workspace.py` 等端口），与 AgentTeams
的桶结构无关。本规范只约束授权面（§6.3）。

### 6.3 accessEntries 生成规则

```text
access_entries = (
  { service: "object-storage", permissions: ["read"],
    scope: { prefix: context_bundle_prefix(bundle_version_id) } },
  { service: "object-storage", permissions: ["write"],
    scope: { prefix: artifact_prefix(run_id) } },
)
```

- 由 VisibilitySnapshot 编译，每个 Run 的前缀唯一；
- **永不投影空 accessEntries**——Controller 对空值应用默认授权
  （`agents/<name>/*` + `shared/*`），恰好是必须避免的；
- 不授予 `agents/<name>/*`、`shared/*` 任何权限。

### 6.4 file-sync 双层关闭

AgentTeams Worker 的 file-sync 机制（工作区整体镜像到桶）对 RepoMesh coding Worker
必须关闭，理由：绕过 `allowed_paths`/`denied_paths` 白名单、绕过 Context 可见性
（§5.3 隔离要求）、且无必要（worktree 从 base SHA + patch artifact 可重建）。

执行在两层，互为保险：

1. 镜像层：RepoMesh Runner Worker 镜像不包含 `file-sync` / `find-skills` 技能；
2. 凭据层：accessEntries 不含 `agents/<name>/*` 写权限，即使技能存在也写不进去。

### 6.5 Agent 间文件交换

需要跨 Agent 共享的文件必须通过 Context 模块 publish 成为带可见性声明的
ContextObject 版本，读取走权限交集并进访问审计。禁止以共享桶前缀直传文件。

## 7. 幂等、命名与对账

### 7.1 资源命名

沿用 `agentteams_resource_name(kind, resource_id)`：`rm-{kind}-{uuid.hex}`，
kind ∈ {manager, worker, team}，新增 human。`resource_id` 是**绑定 ID**（非 run_id、
非 agent_id）：同一逻辑 Worker 因撤权重建时产生新绑定、新资源名，旧资源按保留期清理。

### 7.2 幂等键

```text
idempotency_key = "atp:{binding_id}:{spec_hash}"
```

- 同一绑定同一 spec 重放 → 同一键 → Controller apply 语义幂等；
- spec 变化 → 新 hash → 新键，旧命令重放不会回滚新状态；
- `spec_hash = sha256(canonical_json(projection))`，canonical 序列化规则
  （字段排序、空值省略）由投影编译函数拥有并配契约测试。

### 7.3 漂移对账

- 事件驱动为主（状态回读入 inbox），周期轮询兜底；
- `status.specHash != binding.expected_spec_hash` → 记录 AuditEvent → 重投影
  （同一 spec_hash 幂等重放）；连续 N 次不收敛 → DEGRADED 并上报；
- 资源在 Controller 侧消失 → 依据绑定重建（Controller 重启/替换场景，
  runtime-planes.md 的要求）；
- 出现绑定之外的 `rm-*` 资源 → 只告警，不自动删除（防止误删并行环境）。

## 8. 明确不映射清单

| CRD 字段 | 理由 |
| --- | --- |
| `deployMode` / `backendRuntime` / `containerManaged` / `serviceEnabled` | 部署形态，bootstrap 配置 |
| `volumes` / `mounts`（OSS 卷细节） | 保留字段且开源 pod backend 不支持；Context 物化在 Runner 内完成 |
| `expose` | 编码 Worker 无入站端口需求；调试用途另行评估 |
| `channels`（DingTalk） | 非目标协作通道 |
| `package` | 工作区打包交付与 Context Bundle 机制冲突 |
| `remoteSkills`（Nacos） | Skill 交付走镜像内置 + 投影白名单；远程注册表引入新的扩权面 |
| `shared/tasks/*` 任务树 | AgentTeams Manager 的聊天式任务管理，与 Task Orchestration 事实源冲突 |

本清单的作用是防止后续开发以"补全映射"为由把部署旋钮和越权通道引入投影层；
新增映射字段需修订本规范并说明其业务推导来源。

## 9. 与现有代码的差距（实施清单）

| 位置 | 工作 |
| --- | --- |
| `modules/agent_runtime/ports/agent_team.py` | WorkerProjection 增补 §4.1 字段；TeamProjection 增补 human_members / channel_policy / peer_mentions；新增 HumanProjection、AccessEntry、CredentialBinding、McpServerProjection；RuntimeRef 按 §5.1 增补；`agentteams_resource_name` 支持 human |
| `integrations/agentteams/control_plane.py` | 新字段的 REST 映射；Human 资源端点；敏感字段剥离；specHash 回读 |
| `persistence` + `migrations` | `agentteams_resource_binding` 表（资源名、kind、期望/观察 spec_hash、状态机、时间戳）；注意与 `codex/agent-identity-authorization` 分支的 `20260803_0003/0004` 迁移协调，避免重复建表 |
| `modules/agent_runtime/application` | 投影编译纯函数 + spec_hash 规范化序列化 |
| `bootstrap` | 对账循环装配与配置 |
| 测试 | 编译纯函数单测、适配器契约测试、幂等重放测试、敏感字段剥离测试、空 accessEntries 防御测试 |

依赖说明：`codex/agent-identity-authorization` 分支已含 WorkerProjection 的
soul / agents / mcp_servers / channel_policy 与 `TeamRuntimeRef.leader_room_id`，
该分支合入后本清单相应收窄。

## 10. 待决事项

1. **image 挂靠 vs Fork 新增 runtime**：**已决策（2026-08-04）**——走 Fork 后
   新增 `repomesh-runner` runtime（路线二），实施计划与验证阶梯见
   `agentteams-runner-runtime-plan.md`；image 挂靠降级为 Fork 流程受阻时的
   临时回退。
2. **Manager 是否部署**：基线为最小化 Manager（§4.3）；若验证 Controller 的
   CR 驱动路径可完全脱离 Manager 运行，则降级为不部署。
3. **idleTimeout 与心跳**：**已确认（2026-08-04，代码证据）**——休眠判定
   （`auto_sleep_controller.go`）在 `spec.idleTimeout` 为空时永不触发；
   `lastActiveAt` 由 Matrix AppService 按消息活动写入，与进程心跳无关；
   `lastHeartbeat` 仅 Edge 部署模式使用。结论：编码 Worker 不投影
   `idle_timeout`，无需实现心跳协议（§4.1 已更新）。
4. **workerName 可读性**：Matrix 房间中 `rm-worker-<hex>` 对人类不友好；
   是否投影 task_key 派生的可读 workerName，取决于唯一性与审计需求评估。
5. **permission_level 映射表**：identity_access 角色到 1/2/3 的映射由该模块
   Owner 定义并放入其 contracts。
6. **Sleeping 的使用**：`input_required` 等待人工时是否投影 Sleeping 省资源，
   取决于唤醒延迟实测。
