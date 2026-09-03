# AgentTeams Worker 启动与物化失败复盘

日期：2026-08-30  
范围：RepoMesh 需求物化、AgentTeams Controller、Matrix、MinIO、CoPaw Worker

## 1. 结论摘要

本次任务没有真正进入 Worker 开工状态。问题不是单一故障，而是多次恢复和手工重建后，运行时配置、对象存储数据、容器网络和 Worker 镜像契约出现了漂移。

最终确认的阻塞点是：

```text
openclaw.json not found in MinIO for worker agt-worker-c6bd847ae220
```

Worker 容器已经能够启动并访问 MinIO，但 Controller 没有为恢复后的 CoPaw Worker 重新写入它实际需要的初始化配置，因此 Worker 一直等待配置，任务没有被消费。

## 2. 业务表现

先后出现过以下现象：

- 物化接口返回 HTTP 503，提示执行平面没有团队房间。
- Controller 重启后，原有 Worker 和 Team 资源丢失，需要手工恢复。
- 任务消息已经成功写入 Matrix，但 Worker 没有消费，任务停留在 `assigned`。
- 前端曾显示扫描或任务长期处于 `0 / ?`。
- CoPaw 容器显示 `Up`，但没有真正进入工作循环。
- 需求分析曾出现 LLM 未配置、LLM 返回 HTML、`float('high')` 等独立问题。

## 3. 关键时间线

### 3.1 需求分析链路

- 模型配置指向 `https://www.vibeapi.cn/v1`，模型为 `gemini-3-flash`。
- 供应商接口曾返回 HTML 页面，服务端按 JSON 解析，产生 `LLM gateway returned invalid JSON`。
- 分析结果中的 `confidence: "high"` 被直接转换为浮点数，产生 `could not convert string to float: 'high'`。
- 该问题已通过容错解析修复并有定向测试。

### 3.2 物化链路

- 物化任务依赖 AgentTeams Controller 返回团队房间。
- Controller 重启后，内存中的 Worker/Team 资源不再完整，导致执行平面不可用。
- 重新注册了 4 个 Team、4 个 Leader 和 4 个 Worker。
- Matrix 房间已创建，任务 Collaboration 消息状态为 `delivered`，因此问题不在消息投递。

### 3.3 Worker 启动链路

- `agentteams/copaw-worker:local` 实际是占位镜像，Entrypoint 为 `sleep 3600`，容器虽运行但不会工作。
- 切换到真实 CoPaw 镜像后，入口脚本要求旧版环境变量 `HICLAW_WORKER_NAME`，而 Controller 原本只注入 `AGENTTEAMS_WORKER_NAME`。
- Controller 已增加 `HICLAW_*` 与 `AGENTTEAMS_*` 的兼容变量映射。
- Worker 随后暴露出 DNS 问题：无法解析 `fs-local.agentteams.io`。
- 为 Controller 补充 Docker 网络别名后，Worker 可以访问 MinIO。
- MinIO 中原本不存在 Controller 重新生成的 Worker 专属账号，随后修复了账号授权和密钥漂移。
- MinIO 中缺少 `agentteams-storage` 桶，已创建该桶。
- 桶创建后，Worker 继续因缺少 `openclaw.json` 等初始化对象而等待。

## 4. 已确认的根因

### 4.1 使用了占位 Worker 镜像

资源初始指向：

```text
agentteams/copaw-worker:local
```

该镜像的容器命令只是休眠，不包含真实 CoPaw 工作循环。这造成了“容器运行但没有开工”的假象。

### 4.2 新旧环境变量契约不兼容

Controller 使用 `AGENTTEAMS_*` 变量，部分旧版 CoPaw 镜像只识别 `HICLAW_*` 变量，缺少兼容映射时会直接退出：

```text
HICLAW_WORKER_NAME: HICLAW_WORKER_NAME is required
```

兼容映射已加入 `worker_env.go`，并补充了回归测试。

### 4.3 手工恢复后 Docker 网络别名缺失

Worker 配置使用：

```text
http://fs-local.agentteams.io:9000
```

但恢复的 Controller 容器没有带上该网络别名，导致 DNS 解析失败。该别名已在当前运行网络中补回。

### 4.4 Controller 与 MinIO 凭据发生漂移

重启和恢复过程中，Controller 为 Worker 生成的访问密钥与 MinIO 中保存的账号不一致，先后出现：

- Access Key 不存在
- request signature 不匹配

当前 c6 Worker 的专属账号已按当前 Worker 密钥重新授权。

### 4.5 MinIO 数据不完整

恢复后的 MinIO 中缺少：

- `agentteams-storage` 桶
- `agents/<worker>/openclaw.json`
- `AGENTS.md`
- `SOUL.md`
- `HEARTBEAT.md`
- `mcporter` 配置

桶已创建，但初始化对象仍未生成。

### 4.6 Controller 配置播种分支与实际镜像需求不一致

当前 Controller 对 CoPaw/QwenPaw 路径主要部署 `runtime.yaml`；实际使用的 CoPaw Worker 启动流程读取 `openclaw.json`。因此资源状态可以显示 `Running`，但 Worker 仍然无法完成启动。

此外，原有逻辑在容器创建前等待 `openclaw.json`，而配置播种又依赖后续的成员/团队协调，形成恢复场景中的顺序死锁。该等待已移除，但完整的配置播种分支仍需修复。

## 5. 已完成的改动和操作

### 代码改动

- `worker_env.go`
  - 增加旧版 `HICLAW_*` 环境变量兼容映射。
- `worker_env_test.go`
  - 增加兼容变量断言。
- `member_reconcile.go`
  - 移除创建容器前对 `openclaw.json` 的阻塞等待，避免恢复场景死锁。
- 相关构建和定向 Go 测试已执行并通过。

### 运行环境操作

- Controller 已重启并通过：

```text
GET http://127.0.0.1:8090/healthz -> 200 OK
```

- c6 团队已恢复为 `Active`。
- c6 Worker 和 Leader 已切换到真实 CoPaw 镜像。
- Controller 网络别名已补回。
- 当前 Worker 专属 MinIO 账号已重新授权。
- `agentteams-storage` 桶已创建。
- 临时验证 Worker 已删除。

## 6. 当前状态

### Controller

- 状态：运行中
- 健康检查：通过
- 模式：embedded
- Docker Socket：已挂载

### Team

- Team：`rm-team-c6bd847ae2204d94a3c0008912a14d36`
- 状态：`Active`
- Leader：已注册，容器运行中
- Worker：已注册，容器运行中
- Matrix Team 房间：已存在

### Worker

- 容器状态：`Up`
- 网络连接：已恢复
- MinIO 账号认证：已恢复
- 初始化配置：缺失
- 实际工作循环：尚未启动

### RepoMesh 任务

- Matrix 投递：已成功
- Worker 消费：未发生
- 任务不能确认已进入 `running`
- 本次不能宣称物化链路已经完成

## 7. 尚未解决的问题

1. Controller 需要为恢复后的 CoPaw Worker 重新执行完整配置部署。
2. CoPaw Worker 需要得到实际存在的 `openclaw.json`，而不是只得到 `runtime.yaml`。
3. 初始化配置部署需要具备幂等性：对象已存在时保留必要用户配置，不存在时自动补齐。
4. Team/Worker 重启后需要自动校验存储桶、专属账号、网络别名和配置对象。
5. RepoMesh 物化接口需要在执行平面未就绪时给出可操作的状态，而不是只返回通用 503。
6. 前端需要区分：消息已投递、Worker 已连接、Worker 已消费、任务已执行，避免只显示“运行中”。

## 8. 推荐修复顺序

### 第一优先级：修复配置播种

- 明确 `copaw` Worker 的配置契约。
- 在 Controller 的成员协调路径中调用统一的配置部署服务。
- 为 CoPaw Worker 写入并校验 `openclaw.json`、`AGENTS.md`、`SOUL.md`、`HEARTBEAT.md` 和 mcporter 配置。
- 配置写入成功后再报告 Worker Ready。

### 第二优先级：补恢复自愈

- Controller 启动时重新校验 embedded 模式的网络别名。
- 校验目标桶是否存在，不存在时幂等创建。
- 校验 Worker 专属 MinIO 用户和策略。
- 校验关键配置对象，不存在时重新生成。

### 第三优先级：补端到端验证

至少验证以下状态转移：

```text
Team Active
  -> Worker container running
  -> Worker config readable
  -> Worker Matrix connected
  -> Collaboration message consumed
  -> RepoMesh task running
```

任何一步失败，都应在 Controller、Worker 和 RepoMesh 观测中记录明确原因。

## 9. 安全与数据说明

- 本文不记录 API Key、GitHub Token、Matrix Token、密码或完整访问凭据。
- 未清空 RepoMesh PostgreSQL 数据。
- 未删除已有 Matrix 房间。
- 未删除已有 MinIO 对象。
- 只创建了缺失的 `agentteams-storage` 桶，并更新了目标 Worker 的专属存储账号授权。
- 运行中的 Controller 曾做容器提交备份，便于回滚当前运行态。

## 10. 验收标准

后续修复完成后，应同时满足：

- `agt get teams <team>` 返回 `phase=Active`。
- Worker 容器为 `running`，且日志不再出现 `openclaw.json not found`。
- MinIO 中存在目标 Worker 的初始化配置对象。
- Worker 成功加入个人房间和团队房间。
- 向原有 Team 房间发送测试任务后，Worker 日志出现消费记录。
- RepoMesh 中对应任务从 `assigned` 进入 `running` 或明确的失败状态。
- 重启 Controller 和 Worker 后，以上状态可以自动恢复，而不需要手工创建资源。

## 11. 2026-08-31 后续复盘：真正的根因与干净复位

在保留恢复操作的前提下，经多次诊断和一次干净复位，确认 08-30 复盘中"手工恢复造成漂移"的底层原因来自三点，全部与执行面镜像/凭据生成方式有关。

### 11.1 真正根因

1. **旧 Controller 二进制把 MinIO root 密码注入为 scoped 用户密钥**
   旧容器里的 Controller 二进制在生成 worker/manager 的 MinIO 凭据时，把 root 密码 `AGENTTEAMS_MINIO_PASSWORD` 直接写进 scoped 用户（`EnsureUser`）。Manager 启动时 `mc alias set` 用随机 scoped 密码认证，与 MinIO 实际保存的 root 密码不一致，出现 `signature mismatch` 崩溃循环；Worker 侧则出现 Access Key 不存在/签名不匹配。这解释了"容器 Up 但 Worker 不消费任务"与"Manager 反复崩溃"。
   新二进制的 `EnsureUser` 幂等：用户已存在则用持久化凭据（`/data/worker-creds/<name>.env`，即 `FileCredentialStore`）中的同一个随机密码更新，Manager env 与 MinIO 用户始终自洽。

2. **`docker commit` 生成的镜像丢失 ENTRYPOINT**
   之前为保留诊断现场做过容器提交（快照镜像）。`docker commit` 生成的镜像不携带正确的 ENTRYPOINT/CMD，直接复用会导致容器以错误命令启动，进一步放大状态漂移。

3. **CRLF 行尾污染**
   Windows 下编辑过的 `*.sh`/`*.conf`/Dockerfile 若被写成 CRLF，会破坏容器内 shebang 与配置解析；`core.autocrlf=false` 时不会自动纠正。`.gitattributes` 已约束 `*.sh`/`*.conf`/Dockerfile 为 LF。

### 11.2 干净复位结果

执行完整复位（删旧容器 + 删 `agentteams-data` 卷），用当前源码重建 embedded 镜像（`clean-20260831-fix`，从源码编译 controller/agt）后重跑安装脚本，最终全部通过：

- 安装脚本完整跑通（非交互就地升级，退出码 0，输出登录横幅）。
- 基础设施就绪：Tuwunel 2s、MinIO 0s、Higress 22s、Manager 容器 18s，无崩溃循环。
- Manager CR：`phase: Running`、`welcomeSent: true`。
- Higress：`manager` 与 `worker-e2e` 两个 consumer 均存在，`default-ai-route` 已授权两者（key-auth）。
- Worker `e2e`：`phase/state: Running`、`containerManaged: true`，容器重建后由 Manager 自动恢复。
- 端到端：Manager 在 Matrix DM 中向 worker 发送 `E2E verification...PONG`，worker 回复 `PONG`（完整链路 Matrix → Worker → Higress gateway 模型调用 → 响应）。

凭据持久化验证：重跑脚本重建容器后，MinIO scoped 用户密码、worker Matrix token、网关 key 全部保持不变（`/data/worker-creds` 与 env 文件自洽）。

### 11.3 遗留问题（产品级优化，不阻塞当前可用）

- K8s 模式（`AGENTTEAMS_RUNTIME=k8s`）下 Manager 每次启动都无条件全量 `mc mirror`（约 1.8G / 2.7 万文件），首次启动 + CoPaw 拉起耗时 5-8 分钟，超过安装脚本 300s 默认就绪等待。已用 `AGENTTEAMS_READY_TIMEOUT=900` 覆盖通过；建议后续让 K8s 分支在 workspace 已初始化时跳过全量 mirror（改用增量同步）。
- 安装脚本非交互重跑需要显式设置 `AGENTTEAMS_UPGRADE_KEEP_ALL=1` 与 `AGENTTEAMS_NON_INTERACTIVE=1`，否则会在 `Read-Host` 处卡住或直接取消（详见 `docs/clean-startup-guide-20260831.md` 第 10 节）。
- Tuwunel 按 `txn_id` 全局幂等：重复使用同一 `txn_id` 发送消息会返回旧事件，端到端验证脚本应使用唯一 txn id。