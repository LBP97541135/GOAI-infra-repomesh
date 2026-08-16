# 仓库原生交付技术方案 v0.1（Repository-Native Delivery Tech Design)

- 文档状态：Draft，随 PRD 评审同步修订
- 更新日期：2026-08-11
- 上游文档：`docs/repository-native-delivery-prd-v0.1.md`（编号引用均指该文档）
- 关联代码：`src/repomesh/api/scm_webhook.py`、`src/repomesh/integrations/scm/github_auth.py`、
  `src/repomesh/api/read_models/`（CONS-03 交付读模型）
- 结论先行：`@RepoMesh` 不依赖 GitHub 的 mention 机制触发；实现方式是独立 GitHub App
  订阅 `issue_comment`/`issues` webhook + 应用内文本命令解析。仓内 delivery 线已具备
  webhook 验签、App 凭据签发、观测幂等入库三块基础设施，本功能以复用为主。

## 1. 触发机制：mention 的技术真相

GitHub 不存在「被 @ 时通知 App」的事件类型。`@dependabot rebase`、`@copilot` 等仓库
原生 Agent 的通用实现是：

1. GitHub App 安装到仓库后订阅 webhook，收到安装范围内**所有** Issue / 评论事件；
2. 应用侧对 `comment.body` / `issue.body` 做命令解析，匹配到 `@RepoMesh <命令>` 才处理；
3. mention 文本本身只是给人看的约定，触发与它是否渲染为链接无关。

### 1.1 事件订阅

| 事件 | 用途 |
| --- | --- |
| `issue_comment` (created) | 主命令入口（Issue 与 PR 评论共用此事件） |
| `issues` (opened) | Issue 正文首行含命令时视为首条命令 |
| `pull_request` | 阶段 3 `review`/`fix` 预留；MVP 仅记录 |
| `installation` / `installation_repositories` | 维护「App 可见仓库」授权池（PRD §8） |

订阅之外的事件一律持久化后标记 ignored，不报错（与现有 delivery webhook 行为一致）。

### 1.2 命令解析规则

- 正则骨架：`(?im)^\s*@repomesh\s+(ask|plan|approve-scope|work|status)\b(.*)$`，
  大小写不敏感；`plan:vN` 版本引用单独提取。
- 解析前剔除 markdown 引用块（`>` 前缀行）与围栏/行内代码——否则引用他人评论或粘贴
  文档片段会误触发（PRD §9.3「模糊命令不触发写入」的前置条件）。
- `sender.type == "Bot"` 的事件直接丢弃，防自触发死循环（PRD §9.3）。
- `action == "edited"` 直接丢弃：评论编辑不重放命令（PRD §9.3）；需要新命令就发新评论。
- 无法解析出命令的 `@RepoMesh` mention 按 `ask` 处理（PRD §9.3 默认只读）。

### 1.3 mention 渲染的产品化注意项

App 的机器人账号 login 为 `<app-slug>[bot]`（如 `repomesh[bot]`），**不会出现在用户输入
`@` 时的自动补全列表中**。dependabot 体验好是因为它是 GitHub 内置特殊账号。建议：

- 注册 `RepoMesh` 组织/用户占位，使 `@RepoMesh` 渲染为可点击链接（纯 UX，非功能依赖）；
- Control Comment 与文档中始终展示规范写法 `@RepoMesh <命令>`，靠模仿降低输入门槛。

## 2. GitHub App 配置

按 PRD §17 使用**独立 App、独立 Webhook Secret、独立路由**，与现有 Delivery App
（`REPOMESH_GITHUB_APP_ID`）完全隔离。

| 项 | 值 |
| --- | --- |
| Webhook URL | `POST /api/v1/repository-interactions/github/webhook` |
| 权限 | Issues: write（评论）、Checks: write（Check Run）、Contents: read（读 `.repomesh/agent.yml`）、Metadata: read、Pull requests: read（阶段 3 提 write） |
| 新增配置 | `REPOMESH_REPOSITORY_AGENT_ENABLED`（默认 false）、`REPOMESH_REPOSITORY_AGENT_APP_ID`、`REPOMESH_REPOSITORY_AGENT_PRIVATE_KEY_FILE`、`REPOMESH_REPOSITORY_AGENT_WEBHOOK_SECRET` |

开关为 false 时组合根不挂载路由、不注册后台 worker——满足 PRD §23「关闭即无痕」。

## 3. Webhook 入站：复用 `scm_webhook.py` 骨架，改异步

现有 `/delivery/github-webhook`（`src/repomesh/api/scm_webhook.py`）已示范：
`X-Hub-Signature-256` HMAC 校验（`verify_github_webhook`）→ JSON 解析 →
以 `X-GitHub-Delivery` 为 `external_id` 幂等入库 → 重放返回 `duplicate: true`。
新入口沿用该结构，仅两处不同：

1. **验签 secret 独立**：使用 `REPOMESH_REPOSITORY_AGENT_WEBHOOK_SECRET`。
2. **持久化后立即 202**（PRD §17/§18.2，P95 < 2s）：现有 delivery webhook 是同步处理完
   才返回；本入口的 `plan` 命令挂着仓库发现 + LLM，处理耗时数十秒起，必须改为
   「验签 + 原始事件落库 + 入队」后返回，业务处理交给后台 worker。

```text
POST /api/v1/repository-interactions/github/webhook
  → 验签失败 401 / 未配置 503 / 非 JSON 400（同现有语义）
  → INSERT repository_interaction.inbound_events (provider_event_id 唯一)
  → 冲突 → {accepted: true, duplicate: true}
  → 202 {accepted: true, interaction_id}
后台 worker（轮询或 LISTEN/NOTIFY）
  → 过滤（§1.2）→ 解析 RepositoryCommand → 鉴权快照 → 策略读取 → 分派命令
```

## 4. 命令处理管线

```text
RepositoryCommand 落库（repo + thread + comment_id + revision 唯一）
  → ActorAuthorizationSnapshot：
      GET /repos/{owner}/{repo}/collaborators/{username}/permission
      记录 login、node_id、角色（admin/maintain/write/triage/read）、配置版本、判定结果；
      事后权限变化不改写历史（PRD §12.4）
  → 策略读取：从默认分支解析出的固定 commit SHA 读 .repomesh/agent.yml（PRD §14.1）；
      enabled=false / 命令不在 modes / 角色低于 work_requires_role → 失败关闭，回复原因
  → 分派：
      ask               只读问答（可选接 LLM），直接回复，不建 WorkRequest
      plan <需求>       建 WorkRequest(RECEIVED→DISCOVERING)
                        → repository_intelligence 契约执行发现（授权池交集见 PRD §8）
                        → 生成不可变 plan vN → AWAITING_SCOPE_CONFIRMATION
                        → 回复候选仓库 + 证据 + 缺权限阻塞项 + 确认命令
      approve-scope plan:vN  CAS 校验 vN 仍为最新 → SCOPE_CONFIRMED；过期返回最新版本号
      work plan:vN      幂等键 (work_request_id, confirmed_plan_version)
                        → 创建 Project、冻结 Specification、物化 ExecutionPlan
                        → 进入现有 AgentTeams/Runner/Validation/Delivery 闭环
                        → WorkRequest 置 MATERIALIZED，写 linked_project_id/linked_delivery_id
      status            查交付读模型 GET /deliveries/{linked_delivery_id}（CONS-03）
                        → 渲染为评论文本；不读历史评论推断状态（PRD §10.2）
```

`status` 是现成红利：交付读模型已实现 phase 推导、任务 6 态、门禁 4 态的唯一映射
（`docs/contracts/delivery-read-model-v0.1.md` §5），Control Comment 内容即该聚合的
文本投影，禁止在本模块重新实现任何状态映射。

## 5. 出站回复：Outbox + Control Comment upsert

### 5.1 凭据

复用 `GitHubAppTokenProvider`（`src/repomesh/integrations/scm/github_auth.py`）：
App JWT 签发、installation 解析、token 缓存刷新逻辑原样可用，组合根另实例化一份
（新 App 的 app_id / private key，permissions 按 §2 收窄）。Coding Agent 依然拿不到
任何 GitHub 凭据（PRD §14.2）。

### 5.2 Control Comment（PRD §16.1）

- 每 Thread 一条可更新评论：首次 `POST /repos/{o}/{r}/issues/{n}/comments`，
  之后 `PATCH /repos/{o}/{r}/issues/comments/{comment_id}`；comment_id 存
  RepositoryThread。
- 评论尾部埋机器标记 `<!-- repomesh:thread=<uuid>;revision=N -->`。评论被删或
  comment_id 失效时，列出 Issue 评论按标记恢复绑定，找不到则重建——标记仅用于恢复
  绑定，不是事实源。
- `revision` 单调递增：Outbox 重试投递旧 revision 时先 `GET` 现有评论，已达标则标记
  完成不重发，防乱序覆盖与重复评论（PRD §20.3.3「回复成功后崩溃不重发」）。

### 5.3 RepositoryReplyOutbox（PRD §12.5）

所有外部回复（确认评论、状态更新、Check Run、错误通知）先落 Outbox 再投递，
键为 `interaction_id + reply_kind + revision`（唯一约束）。GitHub 不可用时指数退避重试，
RepoMesh 状态照常推进（PRD §10.3）。仓内 platform outbox 模式可参照。

### 5.4 Check Run（PRD §16.2）

`POST /repos/{o}/{r}/check-runs`，`name = "RepoMesh Delivery"`，`head_sha` 用候选 PR 分支
Head，`external_id` 放 thread/delivery id，`details_url` 指向控制台。纯投影，删除不影响
业务状态。

## 6. 幂等四层键落库（PRD §13）

| 层 | 键 | 实现 |
| --- | --- | --- |
| 入站 | provider + installation_id + provider_event_id | `X-GitHub-Delivery` GUID，唯一索引；重放返回 duplicate（同现有 SCM observation） |
| 命令 | repository_id + thread_id + comment_id + command_revision | RepositoryCommand 唯一约束；评论编辑不产生新 revision |
| 执行 | work_request_id + confirmed_plan_version | 唯一约束 + Thread 级「最多一个活跃 Delivery」状态机守卫；并发 `work` 仅一个成功，其余返回冲突 |
| 回复 | interaction_id + reply_kind + revision | Outbox 唯一约束 + §5.2 投递前查重 |

「两个用户同时批准不同计划版本」（PRD §13）：WorkRequest 行乐观锁，`approve-scope`
CAS 检查 `confirmed_plan_version` 未被写且 vN 仍为最新。

## 7. 模块落位与依赖边界

按 PRD §15 与 `AGENTS.md` 铁律：

```text
src/repomesh/modules/repository_interaction/     # 新业务模块，自有 schema + Alembic 迁移
├─ contracts.py      # RepositoryInteractionHandler、WorkRequest/Thread/Command 视图
├─ domain.py         # WorkRequest 状态机（PRD §11）、命令解析、策略判定
├─ application.py    # 管线编排（§4），依赖 ports
├─ ports.py          # RepositoryHostPort（评论/权限/Check/策略文件）、发现与物化端口
├─ infrastructure.py # PostgreSQL 仓储、Outbox worker
src/repomesh/integrations/repository_hosts/
├─ github.py         # webhook 解析、REST 调用、GitHubAppTokenProvider 装配
└─ in_memory.py      # 行为测试用（仿现有 mock coding adapter 模式）
src/repomesh/api/repository_interactions.py      # webhook 路由 + PRD §17 查询/reconcile 端点
```

- 跨模块只 import 生产方 `contracts.py`：发现走 `repository_intelligence`，物化走
  `project` / `specification` / `task_orchestration`，状态走 `api/read_models`。
  现有能力缺口（如「按需求文本+种子仓库发现」若未暴露契约）由生产模块先补契约。
- 本模块**不拥有** Project、Task、Run、Validation、Delivery 的任何状态，WorkRequest
  在 MATERIALIZED 后只保存关联 ID（PRD §11）。
- GitHub 细节（payload 结构、REST 路径、marker 格式）不出 integrations 层。

## 8. `.repomesh/agent.yml` 策略引擎（PRD §14）

- 读取：命令处理时通过 Contents API 取**默认分支当前 commit SHA** 下的文件，SHA 记入
  ActorAuthorizationSnapshot 的配置版本；同一 WorkRequest 生命周期内锁定该 SHA，
  PR 分支上的策略改动不影响当前 Run（PRD §14.2）。
- 校验：schema 版本 `version: 1`，未知字段拒绝；解析失败按「配置不明确」失败关闭。
- 执行面对接：`allowed_paths` / `denied_paths` 与 Specification 的
  `allowed_paths` / `forbidden_paths`（CONS-01）是同一条治理线——策略在 Prompt 之外由
  Runner 强制执行；Agent 修改 `.repomesh/**` 时 Runner 拒绝提交（PRD §20.2.6）。

## 9. 测试策略

- **契约测试**：RepositoryHostPort 的 in_memory 与 github 适配器共享同一套契约用例
  （仿现有 adapter contract tests）。
- **行为测试**：in_memory host 上覆盖 PRD §20 验收全表——重放去重、并发 work、
  评论删除恢复、权限拒绝、过期版本冲突、编辑不触发。
- **验签测试**：错误签名 401、未配置 503、非 JSON 400（对齐现有 scm_webhook 测试）。
- **架构测试**：repository_interaction 不 import bootstrap / 其他模块非 contracts 路径。
- **联调夹具**：`catbobyman/repomesh-e2e-*` 三仓已具备演示「跨仓发现 + 门禁」的数据形态，
  阶段 1 起可作为真实 GitHub 验收环境。

## 10. 实施切分（对齐 PRD §22 发布阶段）

| 批次 | 内容 | 依赖 |
| --- | --- | --- |
| T1 | App 注册、webhook 入站/验签/幂等落库、命令解析、`ask` + `status` + Control Comment（只读，PRD 阶段 0） | 交付读模型（已合并） |
| T2 | WorkRequest + `plan`：发现契约对接、候选范围/缺权限评论 | repository_intelligence 契约核对 |
| T3 | `approve-scope` / `work`：版本 CAS、物化进现有闭环，单仓 confirmed scope（PRD 阶段 1） | T2 |
| T4 | Check Run、多仓 scope、`review`/`fix`/`retry`/`cancel`（PRD 阶段 2-3） | T3 |

T1 端到端即可演示（真实 Issue 里 `@RepoMesh status` 返回读模型投影），建议以它验证
App/webhook/评论三件套后再动写路径。

## 11. 风险与未决项（技术侧补充）

1. **GitHub 用户 ↔ RepoMesh 身份映射**（PRD 待决策项 5）：鉴权快照绕不开。建议 MVP
   用 `agent_directory` 增加外部身份绑定表（provider + login + node_id → principal），
   未绑定用户按仓库角色判权、以 login 记审计，正式绑定机制另行契约。
2. **发现契约缺口**：`repository_intelligence` 现有契约是否支持「需求文本 + 种子仓库 +
   授权池过滤」的调用形态，动工前核对；缺则先补契约（§7 规则）。
3. **`ask` 的 LLM 依赖**：只读问答若接 LLM 需预算与超时约束（PRD §14.1 limits）；
   T1 可先实现为读模型/画像的模板化回答，不阻塞。
4. **Webhook worker 形态**：轮询 inbound_events 表 vs LISTEN/NOTIFY；建议先轮询
   （与 delivery reconcile 循环同款），量起来再优化。
5. **入口仓库 `intake_only`**（PRD §7.2）：数据模型 v0.1 预留字段，管理流程不实现。
