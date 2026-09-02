# RepoMesh 本地 CLI 一键拉起与开工就绪门禁需求

> 日期：2026-08-29  
> 状态：需求草案，待评审  
> 适用范围：RepoMesh Console、本机 Local Launcher、Room-Native Agent Bridge、发现链 Materialize  
> 背景基线：现有“本地 CLI”页面与 `scripts/start-local-cli.ps1` 已提供命令式启动入口，但尚未形成产品可观察的一键启动和开工门禁

## 1. 背景与问题

RepoMesh 已能通过本机 PowerShell 脚本拉起多个 External Codex 成员，但当前 Console 的“本地 CLI”页面只展示并复制命令，浏览器本身不会启动进程，也不知道命令是否执行成功。

发现链在计划生成后仍允许用户直接执行“物化开工”。此时系统没有检查计划涉及的 Repository Leader 和 Worker Bridge 是否已经启动并完成以下准备：

- enrollment 与 RepoMesh binding 一致；
- Matrix 已连接，授权房间已经确认；
- 真实 Codex 会话已通过 readiness 检查；
- Repository Leader 已进入 leader lane，且没有代码 workspace；
- Worker 已进入 governed lane，且 workspace root 与控制面一致。

这会产生“页面显示已经开工，但本地成员没有行动”的假开工。尤其是 Leader 计划通知具有幂等性，Bridge 又不回填启动前的历史消息；若 Materialize 发生时 Bridge 尚未运行，后启动的 Bridge 可能永久错过通知。

## 2. 目标

提供一个完整的“启动并检查本地 CLI”产品闭环：

1. 操作者可以从 Console 发起固定的一键启动操作；
2. 本机启动器拉起本机已配置的 External Codex 成员；
3. RepoMesh 能观察每个 Bridge 是否真实就绪，而不是只相信 PID 或按钮点击结果；
4. 计划涉及的所有 External Leader/Worker 就绪前，Materialize 不得创建或派发执行任务；
5. 页面明确显示未就绪成员、失败阶段和可执行的恢复动作；
6. 保留 PowerShell 命令作为本机启动器不可用时的降级入口。

一句话目标：**先点名确认本地成员全部到岗，再允许开工。**

## 3. 非目标

本需求不包括：

- 将 Organization Leader 本地化；
- 在浏览器中读取或展示 Matrix、RepoMesh、模型等凭据；
- 允许网页执行任意 PowerShell、命令行或用户输入的可执行文本；
- 用 PID 文件替代 Bridge 自身的就绪报告；
- 自动修复 enrollment、binding、Team 或数据库记录；
- Linux/POSIX 本机启动器；首期真实宿主仍为 Windows；
- 替换既有 AgentTeams managed member 的容器 readiness；
- 为普通聊天或 Matrix 文本赋予任务真相源地位。

## 4. 角色与术语

### 4.1 操作者

已登录 RepoMesh Console 的本地管理员，负责启动本地成员、查看状态和触发 Materialize。

### 4.2 Local Launcher

运行在操作者 Windows 宿主机上的受限本机启动器。它只暴露固定操作，不接受任意命令、路径或凭据。

### 4.3 Bridge readiness

Bridge 完成 RepoMesh preflight、Codex readiness、状态恢复和 Matrix 房间连接后，向 RepoMesh 报告的短租约状态。只有租约未过期且角色能力与绑定一致，才算 `ready`。

### 4.4 Materialize readiness gate

发现链执行 Materialize 前的 fail-closed 门禁。它只约束计划实际使用的 External Repository Leader 和 Worker；managed member 沿用自己的运行时 readiness，不得被误判为缺少 Bridge。

## 5. 目标用户流程

```text
打开“本地 CLI”页面
  → Console 探测 Local Launcher
  → 点击“启动并检查本地 CLI”
  → Local Launcher 固定读取本机 roster/enrollment/env
  → 逐个拉起 Bridge/Codex
  → 每个 Bridge 完成自身 startup gate
  → Bridge 向 RepoMesh 上报短租约 readiness
  → Console 显示 1/6、2/6 … 6/6 已就绪
  → Issue 的 Materialize 门禁重新核验计划成员
  → 全部就绪才允许开工
```

如果 Local Launcher 不可用：

```text
页面显示“未连接本机启动器”
  → 提供现有 PowerShell 命令的复制入口
  → 操作者手动启动
  → Console 仍通过 RepoMesh readiness 查询确认最终结果
```

## 6. 功能需求

### FR-01：本机启动器探测

- Console 必须能区分 `launcher_unavailable`、`stopped`、`starting`、`ready`、`degraded`、`stopping`、`failed`。
- 探测失败不得显示为“CLI 启动失败”；应明确说明是“本机启动器未连接”。
- 本机启动器不可用时，页面仍须保留 DryRun、启动和停止命令的复制入口。

### FR-02：一键启动

- Console 提供主操作“启动并检查本地 CLI”。
- Local Launcher 只允许启动预先配置的 roster；网页不得传入命令行、脚本路径、credential env 或任意成员定义。
- 启动操作必须幂等：已运行的同一成员不得重复拉起第二个 Bridge。
- 每个成员必须继续使用独立 enrollment、Matrix 身份、state、codex-home、PID 和日志。
- Repository Leader 不得获得 `--workspace-root`；Worker 必须使用与控制面一致的统一 workspace root。

### FR-03：逐成员启动结果

页面至少展示：

- 成员显示名与 Agent ID；
- RepoMesh 角色；
- 本机进程状态；
- Bridge readiness 状态；
- 最后一次成功报告时间；
- 失败阶段与脱敏后的错误摘要；
- 日志文件位置，不展示日志中的凭据或私密协议帧。

启动器返回“进程已创建”不能等同于“成员已就绪”。

### FR-04：Bridge readiness 上报

- Bridge 仅在以下步骤全部成功后首次报告 `ready`：
  1. 本地 enrollment 校验；
  2. RepoMesh binding preflight；
  3. 真实 Codex session readiness；
  4. 本地状态恢复；
  5. Matrix sync/授权房间启动；
  6. 角色能力组装完成。
- 首次 readiness 上报失败时，Bridge 不得打印“bridge ready”并继续伪装为可开工。
- Bridge 必须周期续租；网络瞬时失败按下一周期重试，不为一次失败重复启动进程。
- 租约过期后，RepoMesh 自动将该成员视为 `offline`。
- Bridge 正常停止时可做 best-effort 下线报告；系统正确性仍以租约过期为准。

### FR-05：readiness 身份与能力校验

- readiness 请求必须使用成员自己的 external-member token；token 派生的 Agent ID 必须与请求主体一致。
- 服务端必须核对 Agent Directory 中的角色、状态和 AgentTeams resource binding。
- `repository_leader` 必须报告 leader lane 开启、governed lane 关闭。
- `worker` 必须报告 governed lane 开启；其任务启动仍由 RepoMesh 再次校验 Task、assignee、状态与权限。
- 房间消息、PID 文件或浏览器状态不得单独作为 readiness 真相。

### FR-06：开工前门禁

- Materialize 在创建或派发执行任务之前，必须读取本轮计划对应拓扑中的成员集合。
- 仅 `containerManaged:false` 的 Repository Leader/Worker 需要 Bridge readiness；managed member 走既有 runtime readiness。
- 任一必需 External member 未就绪时，Materialize 必须 fail-closed。
- 门禁失败不得创建 Worker coding Task、Runner dispatch 或发送开工通知。
- 已安全创建但尚未派活的幂等拓扑可保留；重试不得重复创建 Team、Task 或通知。
- 失败响应建议使用 HTTP 409，结构化错误码为 `external_members_not_ready`。

建议响应形状：

```json
{
  "detail": {
    "code": "external_members_not_ready",
    "message": "3 local CLI members are not ready",
    "members": [
      {
        "agentId": "uuid",
        "role": "repository_leader",
        "status": "offline",
        "reason": "readiness lease expired"
      }
    ]
  }
}
```

### FR-07：Materialize 前端表现

- Materialize 确认框必须显示“本地 CLI 就绪检查”。
- 有未就绪成员时，不得只依赖按钮 disabled；页面须展示具体成员和恢复入口。
- 页面提供“启动并重新检查”及“仅重新检查”操作。
- 全部成员就绪后，页面显示检查时间，并允许用户确认 Materialize。
- 服务端仍必须在真正执行时再次检查，防止页面检查与点击之间租约过期。

### FR-08：停止与单成员恢复

- Console 支持停止全部本地成员，以及对失败成员执行单独重启。
- 停止前必须按 PID 和固定命令特征复核进程身份，避免误杀其他 Python/Codex 进程。
- 停止后页面应等待 readiness 租约失效或收到下线确认，不得立即伪造 `offline`。

### FR-09：安全约束

- Local Launcher 只监听 loopback 地址。
- 启动器只接受固定操作：`status`、`start`、`stop`、`restart member`。
- 所有写操作必须拒绝不受信任的 Origin，并要求会触发 CORS preflight 的固定自定义请求头。
- Local Launcher 不返回 credential env、token、auth.json 内容或完整进程环境。
- Console 不得向 RepoMesh 服务端上传本机凭据或个人文件。
- Local Launcher 不接受网页传来的可执行文本、脚本路径、工作目录或任意文件路径。

### FR-10：幂等、重试与审计

- 启动键按本机 roster 版本和成员 Agent ID 稳定派生。
- readiness 续租是同一成员/实例的幂等 upsert。
- Materialize 门禁失败应记录脱敏审计事件，包括 Issue、计划版本、阻塞成员和检查时间。
- 不记录 token、THINKING、协议帧、私有绝对路径或未脱敏 stdout/stderr。

## 7. 状态模型

### 7.1 Launcher 状态

| 状态 | 含义 |
|---|---|
| `launcher_unavailable` | 浏览器无法连接本机启动器 |
| `stopped` | 启动器可用，但目标成员均未运行 |
| `starting` | 至少一个成员正在启动，尚未全部 ready |
| `ready` | 本轮需要的成员全部具有有效 readiness 租约 |
| `degraded` | 部分成员 ready，部分成员失败或离线 |
| `stopping` | 停止操作进行中 |
| `failed` | 固定启动操作本身失败，且没有可继续等待的进程 |

### 7.2 成员状态

```text
stopped → process_starting → bridge_starting → ready
                      └──────────────→ failed
ready → stale → offline
ready → stopping → stopped
```

`process_starting` 只说明进程存在；只有 `ready` 才能通过开工门禁。

## 8. Interface 草案

本节用于冻结调用者必须知道的最小 interface，具体路径和字段可在实现评审时校正。

### 8.1 Local Launcher interface

```text
GET  /v1/status
POST /v1/members/start
POST /v1/members/{agentId}/restart
POST /v1/members/stop
```

调用者只提交固定操作，不提交命令、路径或凭据。生产 adapter 操作 Windows 进程；测试 adapter 使用 memory fake。

### 8.2 Bridge readiness interface

```text
POST /api/v1/runtime/v1/external-members/{agentId}/readiness
GET  /api/v1/runtime/v1/external-members/readiness?agentId=...
```

写端由成员自身 token 鉴权；读端供本地管理员、Console 和 Materialize gate 使用。readiness 是短租约事实，不是永久在线状态。

### 8.3 Materialize gate interface

业务调用者只问一个问题：

```text
require_ready(member_ids) → ready | blocking_members[]
```

该 interface 隐藏 Agent Directory、AgentTeams `containerManaged` 判断、租约存储和角色能力校验，Repository Intelligence 不得读取 Agent Runtime 的数据库表。

## 9. 验收标准

### AC-01：真正的一键启动

- Local Launcher 已运行时，用户只点击一次即可拉起配置中的所有本地成员。
- 页面逐成员展示从进程启动到 Bridge ready 的变化。
- 重复点击不会产生重复 Bridge 进程。

### AC-02：未启动不得开工

- 所有 External Bridge 均停止时，Materialize 返回结构化 409。
- 不创建 Worker Task、Runner dispatch 或开工通知。
- 页面明确列出未启动成员。

### AC-03：部分启动不得开工

- 5/6 ready 时仍然拒绝 Materialize，并准确指出第六个成员。
- 启动缺失成员并续租后，不刷新整个发现链即可重新检查并开工。

### AC-04：角色能力正确

- Leader 带 workspace/governed lane 的 readiness 被拒绝。
- Worker 没有 governed lane 的 readiness 不能通过开工门禁。
- managed member 不因缺少 Bridge heartbeat 被错误阻塞。

### AC-05：租约失效

- ready Bridge 被强制终止后，租约到期，页面变为 offline。
- 租约到期后的 Materialize 必须拒绝。
- 旧实例晚到的续租不能覆盖新实例的状态。

### AC-06：启动失败诚实展示

- enrollment、binding、Codex readiness、Matrix 启动任一失败时，页面显示对应阶段。
- 不显示“已就绪”，不发送任务，不泄露凭据或原始 stderr。

### AC-07：启动器不可用时可恢复

- Local Launcher 未安装或未运行时，页面显示明确说明并保留复制命令。
- 操作者手动启动后，RepoMesh readiness 仍能自动变绿。

### AC-08：端到端顺序

- 六个 Bridge 先 ready，再执行 Materialize。
- Leader 收到首次计划通知并提交计划；Worker 自动接单。
- 全程无需人工输入 Task UUID，也不通过脚本、curl 或数据库写入替代业务步骤。

## 10. 交付切片建议

1. **R1：契约与存储**——冻结 readiness schema、状态枚举、租约规则及 memory/PostgreSQL adapter 行为测试。
2. **R2：Bridge 报告**——首次 ready、周期续租、退出下线及 HTTP/memory 两个 adapter。
3. **R3：Materialize 门禁**——区分 external/managed，加入结构化 409 和幂等重试测试。
4. **R4：Local Launcher**——loopback 固定操作、现有 PowerShell 启停逻辑复用、安全与进程身份检查。
5. **R5：Console**——一键启动、逐成员状态、Materialize 预检和命令降级入口。
6. **R6：活体验收**——六实例 soak、杀进程租约失效、5/6 拒绝、6/6 开工及无历史通知丢失。

## 11. 待裁决事项

1. readiness 续租周期与过期时间。建议起点：每 15 秒续租，45 秒过期。
2. Local Launcher 固定端口及允许的 Console Origin 列表。
3. 首次安装/启动 Local Launcher 的产品入口：由平台启动脚本托管，还是由单独安装器注册。
4. Materialize 门禁放在 runtime projection 之后、任务创建之前；是否允许保留已幂等创建的空拓扑。
5. 首次尚未创建 Team/binding 的新环境如何完成“先 Bridge、后 Materialize”：需要独立的成员 provision/binding 准备流程，不能让门禁形成循环依赖。
6. 是否把 readiness 只用于本地 External Codex，还是作为所有 External member 的通用能力。

## 12. 与既有验收范围的关系

原 Room-Native Bridge 冻结验收标准明确没有要求 External Agent 的平台 heartbeat/在线状态。本需求新增了一个有接收端、短租约和正式开工消费者的 readiness 能力，因此属于新的产品增量，不能通过改按钮文案冒充完成。

该增量仍保持既有不变量：

- RepoMesh 是任务与权限真相源；
- Matrix 只负责通知和展示；
- AgentTeams/Bridge 状态不是业务任务状态；
- Leader 不接触代码 workspace；
- Worker 仍需通过 RepoMesh/Runner 的身份、路径、测试和提交治理。
