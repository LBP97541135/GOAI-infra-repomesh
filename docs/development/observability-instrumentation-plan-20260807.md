# 可观测性埋点方案：闭环全景与接入点（2026-08-07）

- 作者：catmem
- 关联：`docs/architecture/runtime-planes.md`、`docs/architecture/governed-agentteams-flow.md`、
  `docs/development/closed-loop-gap-analysis-20260807.md`、
  队友的《多 Team 多 Agent 协作观测方案》（observability-plan.html，L1/L2/L3 三层）
- 状态：调研完成；**第 0 步（依赖 + 属性名契约 + tracer 装配）已实施**，见
  `src/repomesh_runner/telemetry.py` 与 `tests/test_telemetry.py`。
  `src/repomesh/modules/observability/module.toml` 仍为 `status = "planned"`

---

## 0. 结论速览

| 问题 | 结论 |
|---|---|
| 用 LoongSuite 还是 AgentScope Studio | 不是二选一。LoongSuite = 采集端，Studio = 展示端，接口是 OTLP |
| 采集端选型 | **只能用 LoongSuite**。AgentScope 的 `TracingMiddleware` 只对 AgentScope Agent 生效，本项目没有 |
| 展示端选型 | 短期用 Studio（零成本、认 GenAI 语义）；中期换自建 collector。埋点代码不变，只改 endpoint |
| 最小可用改动 | P1 规划侧 ~20 行 + P3 装 LoongSuite + P2 traceparent ~40 行 |

---

## 1. 工具背景

### 1.1 AgentScope Studio

阿里 AgentScope 配套的本地可视化工具，2025-08 开源，Apache-2.0，Node 实现（`@agentscope/studio`）。

- 能力：Projects/Runs 组织、运行时可视化、**OpenTelemetry Trace 视图**、评测统计、内置 copilot「Friday」
- **只接收 Trace**，没有 Metrics/Logs
- OTLP/HTTP 在 `3000`（与 Web UI 同端口），OTLP/gRPC 在 `4317`。
  **实测修正（见 `studio-verification-20260807.md`）**：3000/4317 只是默认值，端口冲突时
  会**静默漂移且不落盘**（本机实测漂到 3001），部署必须显式 `PORT=`；
  `OTEL_GRPC_PORT` 是改端口不是开关，gRPC 无条件启动
- 遵循 OpenTelemetry GenAI 语义约定 v1.38.0 + AgentScope 扩展约定
- **不绑定 AgentScope**：任何 OTLP Exporter 都能上报

识别三类 span：

| span | `gen_ai.operation.name` | 承载 |
|---|---|---|
| `chat {model}` | `chat` | 模型名、provider、采样参数、输入输出消息、finish_reason、token 数、prompt cache |
| `invoke_agent {name}` | `invoke_agent` | agent id/name/description、输入输出消息 |
| `execute_tool {name}` | `execute_tool` | 工具名、call_id、入参、返回值 |

串联靠 `gen_ai.conversation.id`。AgentScope v2 额外定义了 HITL/外部执行相关属性：
`agentscope.agent.reply_id`、`hitl_pending_tools`、`external_execution_pending_tools`、
`is_external_execution`、`agentscope.usage.cache_input_tokens`。

**版本坑**：doc.agentscope.io 上的 `agentscope.init(studio_url=...)` 和 `@trace_llm` / `@trace_reply`
装饰器是 v1 API。v2.0.5 的 `src/agentscope/__init__.py` 已无 `init()`，装饰器被
`agentscope.middleware.TracingMiddleware` 取代，TracerProvider 需自己用标准 OTel SDK 装配。

### 1.2 LoongSuite

阿里云开源的**可观测数据采集套件**（采集端，不是后端也不是面板），本质是 OTel 的发行版和插件集。

| 组件 | 用途 |
|---|---|
| `alibaba/loongcollector` | 节点级 agent：日志、Prometheus、eBPF |
| `alibaba/loongsuite-java-agent` | OTel Java Agent 定制发行版 |
| `alibaba/loongsuite-go` | Go 编译期插桩 |
| `alibaba/loongsuite-python` | OTel Python Contrib 定制版，含 LangChain/AgentScope/Dify instrumentation |
| **`alibaba/loongsuite-js`** | **Claude Code 与 OpenClaw 的 OTel 插件** |
| **`alibaba/loongsuite-pilot`** | **AI coding agent 本地采集器：发现 agent、装 hook/插件、归一化导出** |
| `alibaba/loongsuite-semantic-conventions-genai` | GenAI 语义约定扩展 |

Pilot 支持表覆盖了本项目全部 runtime：

| Agent | 集成方式 | Trace | Token | 工具调用 |
|---|---|---|---|---|
| Claude Code | Hook | ✅ | ✅ | ✅ |
| Codex | Hook | ✅ | ✅ | ✅ |
| OpenClaw | 插件注入 | ✅ | ✅ | ✅ |
| Qwen Code CLI | Hook | ✅ | ✅ | ✅ |
| Hermes Agent | 原生目录插件 | ✅ | ✅ | ✅ |

`loongsuite-js` 的 Claude Code 插件走两条路：Claude Code 自己的 hook 机制
（`UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop` / `SubagentStop` / `PreCompact`），
以及 `intercept.js` 通过 `NODE_OPTIONS=--require` 在进程内拦 HTTP 拿 token 用量和消息内容。

### 1.3 为什么 AgentScope SDK 用不上

- 全仓 grep `agentscope`，命中全是 `agentscope-ai/AgentTeams` 组织名
  （`src/repomesh/integrations/agentteams/upstream.toml:1`），无任何 import，`pyproject.toml` 无此依赖
- AgentTeams README 明写：不实现 agent 逻辑，只编排 agent 容器
- `src/repomesh/modules/agent_runtime/module.toml` 的
  `excludes = ["cli process execution", "provider wire protocols"]`

---

## 2. 一次完整闭环

以「给定价规则加一个折扣字段」为例。

### 阶段 A：规划（RepoMesh API 进程内，有 LLM 调用）

```
① POST /api/v1/discovery
   RepositoryDiscoveryService.discover(requirement)
   → DeepSeekClient.chat()                      ← LLM ×1
   → 候选仓库 + rationale/score

② POST /api/v1/confirmation
   ConfirmationService.confirm(candidate_repos, requirement)
   → for repo in candidates: self._llm.chat()   ← LLM ×N（每候选仓库一次）
   → REQUIRED/MAYBE/EXCLUDED + RepositoryPlan

③ POST /api/v1/integration
   PlanIntegrationService.integrate(plans)
   → self._llm.chat(temperature=0.1)            ← LLM ×1
   → IntegratedPlan：Engineering Spec + Contracts + Task DAG + _topological_batches()

④ POST /api/v1/bridge/materialize
   PlanExecutionBridge.materialize(plan)        ← 无 LLM，纯落库
   → SpecificationService.create(ENGINEERING) ×1 + create(CONTRACT) ×K
   → TaskOrchestrator.assign() ×M
```

### 阶段 B：派单（跨进程，进 AgentTeams）

```
⑤ TaskOrchestrator.assign() → _deliver_assignment()
   ├─ 校验 assignee.role is WORKER
   ├─ topology 找 team + room_id，缺失则 TaskDenied("Team runtime is not ready")
   ├─ AgentTeamsTaskPublisher.publish()
   │     teams/{team}/shared/tasks/{task}/{spec.md, meta.json, manifest.json}
   │     原子替换 → 读回 → 校验 SHA-256          ← 验不过不许通知
   └─ _send_assignment() → Matrix 消息

⑥ Worker 容器（OpenClaw / QwenPaw / Copaw）收到 Matrix 消息
   → MCP: repomesh-task-control.start_assigned_task
   → POST /api/v1/mcp/worker  {task_id, worker_agent_id, adapter_id="claude-code"}
```

### 阶段 C：治理校验与准备

```
⑦ StartAssignedWorkerTask.execute()             ← 治理闸门，全部 fail-closed
   ├─ principal.role is not WORKER      → "coding execution is restricted to Worker"
   ├─ task.assignee_agent_id != worker  → "worker is not assigned to this task"
   ├─ states.start(task) → IN_PROGRESS
   ├─ BuildCodingAgentPackage           → Task Spec + allowed_paths
   ├─ ResolveAgentCapabilities          → tool_allowlist
   ├─ GitWorktreeManager.prepare()      → 隔离 worktree + base_sha
   └─ ContextBundle.create()            → 冻结 allowed_tools / allowed_paths /
                                          denied_paths=(.git/**, .github/workflows/**) /
                                          expires_at = now + 4h
   任一步异常 → states.block() + 向 Leader 报 BLOCKED

⑧ DispatchWorkerTask.execute()
   ├─ GetExecutionContextGrant
   ├─ grant.base_sha != workspace.base_sha → 拒绝并释放 worktree
   ├─ RunnerTaskProjector.project()        → RunnerTask（Runtime v1 信封）
   ├─ materializer.materialize()           → .repomesh/context/manifest.json
   └─ RunnerControlGateway.enqueue()       → 落 Postgres 队列
```

### 阶段 D：执行（repomesh-runner 容器 + CLI 子进程）

```
⑨ runner serve loop（src/repomesh_runner/main.py）
   └─ HttpLongPollTaskSource → GET /api/v1/runtime/runner-tasks/next
      → ledger.seen(idempotency_key)? 是则跳过（重启 ≠ 第二次执行）

⑩ ExecuteRunnerTask.execute()
   ├─ 发 runner.accepted (sequence=1)
   └─ DriverExecutor.execute()
      ├─ WorkspaceContextVerifier
      ├─ get_profile("claude-code") → STREAM_JSON family
      ├─ resolve_binary(("claude",))            → binary_not_found
      ├─ _resolve_workspace()                   → workspace_escape containment 检查
      ├─ AllowlistPermissionPolicy(permissions) → 逐次工具调用 allow/deny
      └─ StreamJsonDriver.execute()
         └─ SubprocessFactory.spawn()           ← claude 子进程在此运行
            解析 stream-json → DriverEvent:
            TOOL_USE / TOOL_RESULT / TEXT / THINKING / PERMISSION_REQUEST

⑪ _collect_evidence()                           ← 证据闸门
   ├─ _changed_files()
   ├─ _changed_path_violation() → 越界 → FAILED "changed_path_denied"
   ├─ _run_test_commands()      → 非 0 → FAILED "test_command_failed"
   └─ 全绿且有改动 → _commit_changes()          ← 只有验证过才 commit
```

### 阶段 E：回写与交付

```
⑫ 发 runner.completed/failed/interrupted/input_required (sequence=2)
   event_id = uuid5(run_id, attempt, sequence, type)   ← 确定性，重投递免费
   → HttpEventSink POST /api/v1/runtime/runner-events（Idempotency-Key 头）

⑬ RunnerControlGateway.receive_event() → _write_back()
   → task.report(status, evidence{summary, changedFiles, testResults, commitSha, runId})
   → Postgres（乐观锁 expected_version）

⑭ Repository Leader 审 diff → Organization Leader 跨仓库集成 → ChangeSet + PR
```

---

## 3. 三个埋点平面

| 平面 | 位置 | 用什么 | 改动量 |
|---|---|---|---|
| **P1 规划** | `src/repomesh/modules/repository_intelligence/application/discovery.py:39` | **手写 span** | ~20 行 |
| **P2 控制** | `src/repomesh/api/worker_mcp.py:41`、`integrations/runner/worker_execution.py:107`、`integrations/runner/dispatch.py:39` | **手写 span + traceparent 注入** | ~40 行 |
| **P3 执行** | runner 容器 + CLI 子进程 | **LoongSuite JS** + `DriverExecutor.observer` | 装插件 + ~60 行 |

### 3.1 P1：包住唯一的 LLM 出口

规划阶段三个 phase 都收敛到 `DeepSeekClient.chat()`，改一处即可。

**注意**：该方法是**裸 `httpx.post` 打 `/chat/completions`**，不走 OpenAI SDK。
LoongSuite Python 的零代码埋点 patch 的是 openai / langchain / agentscope，**认不出它是 LLM 调用**，
最多在 httpx instrumentation 下出现一个普通 HTTP span（无 model、无 token）。这一段必须手写。

```python
# src/repomesh/modules/repository_intelligence/application/discovery.py
_tracer = trace.get_tracer("repomesh.planning", "0.1.0")

class DeepSeekClient:
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
        with _tracer.start_as_current_span(
            f"chat {self._config.model}",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "deepseek",
                "gen_ai.request.model": self._config.model,
                "gen_ai.request.temperature": temperature,
                "gen_ai.input.messages": json.dumps(messages, ensure_ascii=False)[:32768],
            },
        ) as span:
            response = httpx.post(...)
            payload = response.json()
            usage = payload.get("usage", {})
            span.set_attributes({
                "gen_ai.usage.input_tokens": usage.get("prompt_tokens", 0),
                "gen_ai.usage.output_tokens": usage.get("completion_tokens", 0),
                "gen_ai.response.finish_reasons": [payload["choices"][0].get("finish_reason", "")],
                "gen_ai.output.messages": json.dumps(
                    payload["choices"][0]["message"], ensure_ascii=False
                )[:32768],
            })
            return str(payload["choices"][0]["message"]["content"])
```

**DeepSeek 响应里的 `usage` 字段当前被直接丢弃——这是免费的 token 数据，现在一行都没记。**

外层再包三个业务 span（`discovery` / `confirmation` / `integration`），
`confirmation` 为父，N 个 repo 的 `chat` 为子。

### 3.2 P2：把 run_id 焊进链路

`worker_mcp.py` 的 `tools/call` 起 span，`StartAssignedWorkerTask` 每个校验点记 span event，
最后把 `traceparent` 随 RunnerTask 入队：

```python
# src/repomesh/integrations/runner/dispatch.py，enqueue 之前
carrier: dict[str, str] = {}
TraceContextTextMapPropagator().inject(carrier)
# carrier["traceparent"] 随 RunnerTask 入队，runner 侧 extract 出来当 parent
```

**不做这步，Studio 里就是一堆孤立 session，回答不了「这个需求总共烧了多少钱」。**

### 3.3 P3：LoongSuite 装进 runner 镜像

`src/repomesh_runner/drivers/supervision.py:187` 的 `SubprocessFactory.spawn` 是
`{**os.environ, **spec.environment}` 合并后 `create_subprocess_exec`——**不过 shell，
LoongSuite 安装脚本写进 `~/.bashrc` 的 `alias claude=...` 永远不生效**，必须走
`SpawnSpec.environment` 注入：

```python
# src/repomesh_runner/executor.py，构造 DriverRequest 时
spec_env = {
    "NODE_OPTIONS": f"--require {INTERCEPT_JS}",
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_EXPORTER_OTLP_ENDPOINT": os.environ["REPOMESH_OTLP_ENDPOINT"],
    "OTEL_SERVICE_NAME": "repomesh-worker",
    "OTEL_RESOURCE_ATTRIBUTES": ",".join([
        f"repomesh.run_id={task.run_id}",
        f"repomesh.task_id={task.task_id}",
        f"repomesh.project_id={task.project_id}",
        f"repomesh.repository_id={task.repository_id}",
        f"repomesh.attempt={task.attempt}",
        f"repomesh.adapter={task.adapter_id}",
    ]),
    "traceparent": carrier["traceparent"],
}
```

hook 配置（`~/.claude/settings.json`）烘进 worker 镜像。

**实测注意**：`OTEL_RESOURCE_ATTRIBUTES` 的 Resource 属性 Studio 会入库但 **UI 完全不显示**——
关键关联 id（run_id/task_id 等）必须**同时**写成 span 属性才能在界面上看到
（线 B 的 `OtelDriverObserver` 已按此实现）。

同时 `build_default_executor`（`src/repomesh_runner/executor.py:476`）要补 observer——
**它现在没传 observer，`DriverExecutor` 的 observer 在生产里是彻底的 no-op**：

```python
return DriverExecutor(
    drivers={...},
    workspace_root=workspace_root,
    context_verifier=WorkspaceContextVerifier(),
    observer=OtelDriverObserver(),      # ← 现在缺这个
)
```

`DriverEvent` → GenAI span 的映射（三个 driver `stream_json` / `acp` / `app_server`
发的是同一组 `DriverEventKind`，见 `drivers/base.py:45`）：

| DriverEvent | span | 属性映射 |
|---|---|---|
| `SESSION_STARTED` | `invoke_agent {profile}` | `gen_ai.agent.name`；`gen_ai.conversation.id` ← `run_id` |
| `TOOL_USE` → `TOOL_RESULT` | `execute_tool {name}` | `gen_ai.tool.name` / `.call.id` ← `call_id` / `.call.arguments` ← `input` / `.call.result` ← `output` |
| `THINKING` / `TEXT` | span event | `gen_ai.output.messages` |
| `PERMISSION_REQUEST` | span 属性 | 对应 `agentscope.agent.hitl_pending_tools` |

`stream_json.py:280` 发 `{call_id, tool_name, input}`，`stream_json.py:297` 发
`{call_id, output}`，两边靠 `call_id` 配对，与 GenAI 约定一一对应。

---

## 4. 接完之后能看到什么

### 4.1 一条完整 trace

```
📋 changeset CS-001 "给定价规则加折扣字段"          trace_id=4bf92f...  总耗时 23m14s
│
├─ 🧠 discovery                                              1.8s
│  └─ chat deepseek-chat        in=3,204  out=412   ¥0.0021
│     └─ 候选: pricing(0.94) api(0.71) web(0.33)
│
├─ 🧠 confirmation                                           9.6s
│  ├─ chat deepseek-chat  repo=pricing   in=2,890 out=680  → REQUIRED
│  ├─ chat deepseek-chat  repo=api       in=2,750 out=520  → REQUIRED
│  └─ chat deepseek-chat  repo=web       in=2,610 out=190  → EXCLUDED
│
├─ 🧠 integration                                            4.1s
│  └─ chat deepseek-chat        in=5,120  out=1,840
│     └─ DAG: pricing.T1 → api.T2, batches=[[T1],[T2]]
│
├─ 📦 bridge.materialize                                     0.3s
│     specs=3  tasks=2
│
├─ 📮 task.assign  task=T1 repo=pricing                      1.2s
│  ├─ agentteams.publish   sha256=a3f9...  ✅ 读回校验通过
│  └─ matrix.send          room=!xKq...:matrix.local
│
├─ 🔐 mcp.start_assigned_task  task=T1                       2.4s
│  ├─ ✅ role=worker  ✅ assignee 匹配
│  ├─ worktree.prepare      base_sha=8c1d0f2  workspace=ws-7a3e
│  └─ context_bundle.create allowed_paths=[src/pricing/**]
│                           denied_paths=[.git/**, .github/workflows/**]
│                           expires_at=+4h
│
├─ 📤 runner.dispatch → enqueue                              0.1s
│
└─ 🤖 claude.session  run_id=9f2c...  (LoongSuite 采)      19m47s
   ├─ 👤 Turn 1
   │  ├─ 🧠 chat claude-sonnet   in=18,204 out=1,203   $0.084
   │  ├─ 🔧 Read  src/pricing/rules.py            ✅ allow    0.1s
   │  ├─ 🔧 Grep  "discount"                      ✅ allow    0.3s
   │  └─ 🔧 Edit  src/pricing/rules.py            ✅ allow    0.2s
   ├─ 👤 Turn 2
   │  ├─ 🧠 chat claude-sonnet   in=24,890 out=890    $0.098
   │  └─ 🔧 Edit  .github/workflows/ci.yml        ❌ DENY  path_denied
   ├─ 🗜️ context compaction                                 2.1s
   ├─ 👤 Turn 3
   │  └─ 🧠 chat claude-sonnet   in=31,002 out=1,540  $0.131
   └─ 📊 evidence
      ├─ changed_files=[src/pricing/rules.py, tests/test_rules.py]
      ├─ path_violation=none
      ├─ test: pytest tests/test_rules.py  exit=0     42.3s
      └─ commit 3e8a91c ✅
```

### 4.2 具体能回答的问题

| 问题 | 从 trace 读到 | 现状 |
|---|---|---|
| 这个需求花了多少钱 | 规划 ¥0.0089（3 次 DeepSeek）+ 执行 $0.313（3 次 Sonnet） | 两个数字都拿不到 |
| 23 分钟花在哪 | 规划 15.8s，派单+校验 3.9s，**Worker 执行 19m47s（85%）**，test 42s | 无 |
| 为什么失败 | Turn 2 `Edit .github/workflows/ci.yml` span 上 `permission.decision=deny`、`reason=path_denied`；同 span 挂 grant 的 `expires_at`，可区分越权与 grant 过期 | 只能翻日志 |
| compaction 掉了什么 | `PreCompact` hook 出 span；Turn 1 输入 18k → Turn 3 31k，中间压缩过一次 | 完全黑盒 |
| T2 为什么没开始 | T1 `runner.completed` 到 T2 `task.assign` 的时间空档 = 调度延迟 | 无 |
| 哪个仓库最容易 BLOCKED | 按 `repomesh.repository_id` 聚合 `states.block()` span，区分「Team runtime is not ready」与「context_grant_binding_mismatch」 | 无 |
| confirmation 判得准不准 | 每次 `chat` 的 `gen_ai.input.messages` / `output.messages` 都在 span 上，可直接回放 | 需复现 |

### 4.3 观测不到的（边界）

- **Manager / Leader agent 自己的推理**：跑在 AgentTeams 的 OpenClaw/QwenPaw 容器，不经过
  `repomesh-runner`。要覆盖需给那些镜像也装 LoongSuite（Pilot 支持 OpenClaw 和 Qwen Code CLI，
  但那是另一批镜像）
- **Matrix 消息正文**：不在 trace 里，属于 L2
- **审计留存**：Studio 不为此设计，最终落 `observability` 模块

---

## 5. 与队友观测方案（observability-plan.html）的差异

| 方案原文 | 实际情况 |
|---|---|
| L3「在项目入口加 `agentscope.init(studio_url=...)`，和狼人杀项目一样」 | **跑不通**。本项目无 AgentScope Agent，改为 LoongSuite JS / Pilot |
| L3「LoongSuite 自动捕获 LLM 调用」 | Python 版捕获不到（`DeepSeekClient` 是裸 httpx，CLI 侧是 Node 进程）；需 JS 版 + 手写规划侧 span |
| iframe `localhost:3000/session/{agent_name}` | **已实测确认不存在**：SPA catch-all 静默重定向到 `/overview`（HTTP 仍 200，curl 看不出错）。且 **Studio 没有单 trace 深链**——trace 详情是纯 React state，地址栏不变，iframe 只能嵌 `/tracing` 列表页；要做「点 run 看这条 trace」必须自建前端走 tRPC `getTrace`。详见 `studio-verification-20260807.md` 第 4/5 节 |
| L1「SQLite + FastAPI」 | 代码里是 **Postgres**（`PostgresRunnerGatewayStore`；`runtime-planes.md` 明写 PostgreSQL is the fact source） |
| 阶段三「让 Team 更新状态」 | 流向反了。状态由 Runner 发事件 → `RunnerControlGateway._write_back()` → Postgres，Worker 全程不碰业务库 |

---

## 6. 落地顺序

1. **P1 的 ~20 行**（`DeepSeekClient.chat` 加 span，顺手收 `usage`）——半天，立刻有规划阶段成本数据
2. **P3 装 LoongSuite**（镜像 + `SpawnSpec.environment` 注入）——一天，拿到执行阶段全部 token 与工具调用
3. **P2 的 traceparent**——把 1 和 2 串起来，半天，此时第 4 节那棵树才成立
4. **`build_default_executor` 补 observer**——补 runner 侧治理决策 span
   （permission deny、path violation、test 结果），这些 LoongSuite 看不到，是本项目独有

前三步做完即有完整的单条 trace。

**唯一不可逆的决策是 resource attributes 的命名**
（`repomesh.changeset_id / task_id / run_id / repository_id / attempt / adapter`）。
后端可以随时换，埋点字段换不了。

---

## 7. 接入前的风险确认

- LoongSuite 安装脚本从 `arms-apm-cn-hangzhou-pre.oss-cn-hangzhou.aliyuncs.com` 和
  `loongcollector-community-edition.oss-cn-shanghai.aliyuncs.com` 拉取（前者域名带 `-pre`，
  疑似预发环境）。`curl | bash` 进 worker 镜像前先下载审阅
- `intercept.js` 记录**完整输入输出消息**。Pilot 有 per-agent 内容采集策略与密钥脱敏开关，
  接生产前必须确认配置
- 凭据不得进入 trace：`runtime-planes.md` 已规定凭据不得序列化进 Runtime v1 消息、
  context 文件、Matrix 消息与日志，span 属性同此约束
- **Studio 监听 `0.0.0.0`**（实测），而 trace 里含完整 prompt 与模型输出——
  开发机需防火墙拦外部入站，不得在共享网络裸跑

---

## 参考

- AgentScope Studio：https://github.com/agentscope-ai/agentscope-studio
- Studio tracing 文档：`docs/tutorial/en/develop/tracing.md`（同仓库）
- AgentScope v2 源码：`src/agentscope/middleware/_tracing/{_trace,_attributes}.py`、`tests/tracing_test.py`
- LoongSuite JS：https://github.com/alibaba/loongsuite-js
- LoongSuite Pilot：https://github.com/alibaba/loongsuite-pilot
- OpenTelemetry GenAI 语义约定：https://opentelemetry.io/docs/specs/semconv/gen-ai/
