# RepoMesh 多仓库真实端到端验收报告（2026-08-10）

## 1. 验收结论

本次真实验收通过。用户只提交初始需求，后续由 RepoMesh 与 AgentTeams 完成项目拆解、仓库级分派、Worker 执行、Coding Agent 调用、测试、commit、跨仓库顺序释放、PR 创建、CI 观测、人工审批观测、Leader 治理放行和依赖顺序合并。

最终 ChangeSet 状态为 `delivered`。API 仓库先合并，Client 仓库在上游完成后合并，符合跨仓库依赖约束。

## 2. 测试范围与环境

- RepoMesh 分支：`codex/close-delivery-loop`
- GitHub App ID：`4543488`（私钥、安装 Token 与 Matrix Token 未写入本报告）
- 测试仓库：`LBP97541135/repomesh-e2e-api`
- 测试仓库：`LBP97541135/repomesh-e2e-client`
- AgentTeams 角色：Organization Leader、两个 Repository Leader、两个 Worker
- Coding 执行：外部 Coding Agent 经 Runner/Adapter 调用
- 交付约束：必需检查 `test`、至少一次审批、Leader 对当前 head SHA 的治理决策
- 仓库可见性：验收期间临时设为 Public 以验证分支保护，结束后均恢复 Private；匿名访问返回 `404`

## 3. 需求与执行对象

需求目标是对 API 定价结果增加 `discount_amount`，并让 Client 正确展示该字段。两个仓库必须分别修改、测试和提交，Client 依赖 API 的交付结果。

### 项目与计划

- Project ID：`33e51ae6-713c-4928-b1c9-4f1fdaa92cc8`
- Execution Plan ID：`34fbc214-2384-46e8-86b5-d98989a68581`
- Organization Leader ID：`fcc894a1-a094-41cb-9ac8-411790892ead`
- 执行计划最终状态：`completed`

### API 仓库任务

- Repository Leader Task：`01a65db9-1623-4f8f-b4df-9ef360fb1f1a`
- Worker Task：`fbe4e271-f146-40e0-be13-d689d8f55a62`
- Runner Run：`f142a828-0cf8-4eb8-b18b-0b85452009e3`
- Runner 结果：`succeeded`
- 候选 commit：`8deb466aff32990f7acf3858c61c045ebeaff335`

### Client 仓库任务

- Repository Leader Task：`4426a7d6-50e6-4e12-a77c-a0492f681d3d`
- Worker Task：`9e7af66a-4c4a-42f0-9665-c123b6519ecb`
- Runner Run：`5f42d89b-3498-4604-a9e3-72b330f2e2a6`
- Runner 结果：`succeeded`
- 候选 commit：`5fdafd67f25de54b1a67c16b1d7d7a7071030693`

## 4. 实际执行顺序

1. Organization Leader 创建项目并形成跨仓库计划。
2. API Repository Leader 接收仓库目标，拆成结构化 Worker Task。
3. API Worker 通过 MCP 领取任务，调用 Coding Agent 修改代码、运行测试并生成 commit。
4. API 任务成功后，调度器释放下一批 Client 仓库任务。
5. Client Repository Leader 与 Worker 重复仓库内闭环。
6. 两个候选 commit 形成 Validation Snapshot 与 ChangeSet。
7. 系统将候选分支推送至 GitHub 并创建两个 PR。
8. GitHub Actions 的 `test` 检查通过，GitHub 用户分别审批两个 PR。
9. Organization Leader 对两个当前 head SHA 分别记录 `READY` 治理决策。
10. Merge Gate 先允许 API 合并；API 合并后才允许 Client 合并。
11. 两个仓库合并完成，ChangeSet 进入 `delivered`。

## 5. GitHub 交付记录

### API

- PR：<https://github.com/LBP97541135/repomesh-e2e-api/pull/1>
- 交付分支：`repomesh/34fbc214/04250dab`
- 候选 head：`8deb466aff32990f7acf3858c61c045ebeaff335`
- GitHub Check Run：`93393871063`
- CI：`test` 通过
- Review：`approved`
- 治理决策：`READY`，绑定上述候选 head
- 合并 commit：`98ce57de44734aee507ed8e47f090e10c681852f`

### Client

- PR：<https://github.com/LBP97541135/repomesh-e2e-client/pull/1>
- 交付分支：`repomesh/34fbc214/8b6c0345`
- 候选 head：`5fdafd67f25de54b1a67c16b1d7d7a7071030693`
- GitHub Check Run：`93393926758`
- CI：`test` 通过
- Review：`approved`
- 治理决策：`READY`，绑定上述候选 head
- 上游依赖：API repository `04250dab-1628-44e6-be1c-4ceaa30d2304`
- 合并 commit：`aaaead8a97d6be4531a9912bebe0b14b3524002a`

### ChangeSet

- Validation Snapshot：`e2734345-ced5-40f4-944b-093471a40e22`
- ChangeSet：`cdacde2e-d635-4045-9bf5-8122389e9ead`
- 最终状态：`delivered`
- API repository 状态：`merged`
- Client repository 状态：`merged`
- Governance Decision 数量：2

## 6. 本轮发现并修复的问题

### AgentTeams 与 MCP

1. RepoMesh 使用了错误的 Matrix 发送身份，而实际 Team/DM 房间由 `@admin` 加入。改为使用正确的 Manager Matrix Token 后，Leader 与 Worker 消息恢复。
2. AgentTeams 为每个 Worker 生成独立 Gateway Key，RepoMesh 原先只接受一个 `REPOMESH_MCP_GATEWAY_TOKEN`，导致第二个 Worker 返回 401。现在兼容旧单 Token，并支持 `REPOMESH_MCP_GATEWAY_TOKENS` 多 Token 列表。
3. 为两个验收 Worker 增加声明式 `repomesh-task-control` MCP 配置。

### 数据与计划

1. Plan Snapshot Store 错误调用 `database.session()`；已改为项目真实接口 `database.sessions()`。

### GitHub 推送与交付

1. Git Smart HTTP 错用 Bearer 认证。现改为 GitHub App 安装 Token 要求的 Basic `x-access-token:<token>`。
2. 工作区继承 `remote.origin.mirror=true`，显式 refspec 推送仍被 Git 拒绝。推送命令现临时覆盖 `remote.origin.mirror=false`，不修改仓库持久配置。
3. Finalizer 重试会先创建新 Validation Snapshot，导致同一幂等键的 ChangeSet 指纹变化并抛出冲突。现先读取既有 ChangeSet、复用原快照，再用当前候选重新执行指纹校验；已验证连续 Finalize 两次不会重复创建 ChangeSet、快照或 PR。

## 7. 自动化验证结果

- 真实 GitHub 双仓库交付：通过
- API → Client 批次与合并顺序：通过
- Worker MCP 多身份鉴权：通过
- Runner 代码修改、测试、commit：通过
- GitHub App 推送、PR、CI 轮询与自动合并：通过
- 人工审批与 head-bound Leader 治理门禁：通过
- Finalizer 重试幂等回归：通过
- 全量 Pytest：`655 passed, 13 skipped, 1 warning`，耗时 `149.20s`
- Ruff：通过
- `git diff --check`：通过（仅有 Windows 行尾转换提示）
- Alembic：唯一 head 为 `20260810_0015`

Pytest 的一个 warning 来自 Starlette 对 `httpx + testclient` 的上游弃用提示，不是本轮逻辑回归。

## 8. 未阻塞验收的后续项

1. Matrix `/sync` 在本机环境偶发 `ReadTimeout`，当前会自动重试且未阻塞任务闭环；生产环境应增加日志降噪、连接指标与退避策略。
2. MCP Gateway Token 当前通过静态列表配置。企业部署应改为由 AgentTeams 控制面动态注册、轮换和吊销 Worker Key。
3. 前一次失败测试遗留计划 `120c6ffa-3b5d-4a91-9bc3-b5dabf2a9051`，暴露出缺少显式取消/归档旧计划的运维入口；不影响本次成功 ChangeSet。
4. 本次治理决策由受控验收脚本调用应用服务写入。后续应提供正式的 Organization Leader API/界面，并纳入身份鉴权和审计日志。

## 9. 停机状态

验收结束后已停止 RepoMesh API、PostgreSQL、AgentTeams Controller、Manager、Repository Leader、Worker 与本机 Runner。Docker 数据卷保留，未删除数据库或测试证据。
