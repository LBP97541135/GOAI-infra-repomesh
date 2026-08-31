# Q3a — mock coding agent 工厂零触达静态审计

> 日期:2026-08-28
> 工单:W-C3b(轨 Q / Q3a)
> 依据:[最终验收执行计划 §4 轨 Q](room-native-bridge-final-acceptance-execution-plan-20260828.md)、
> [最终验收标准 §8 FAIL](../room-native-bridge-final-acceptance-standard-20260827.md)
> 性质:**纯静态审计**,本次未改动任何产品代码
> 审计基线:`e695eeb0`

验收标准 §8 的第一条 FAIL 是「任一 Codex Leader/Worker 实际由成员容器或 fake adapter 代替」。
本文回答其中一半:组合根注入的 `mock_coding_agent_factory` 在六成员验收路径上是否可能被触达。

---

## 1. 结论

**零触达成立。** `mock_coding_agent_factory` 只有一个运行时消费者
——HTTP 端点 `POST /api/v1/coding-runs/mock`——六成员路径上没有任何一段代码、
配置或前端会调用它;external Bridge 自带 consumer 组合 `repomesh_runner` 的
`DriverExecutor`,与 `ApplicationContainer` 的这个字段之间不存在调用边。

需要同时记住的两点(§4 展开):

- 仓库里还有**另一个**、与本字段无关的 mock:runner 镜像内的
  `repomesh-mock-agent` 可执行文件与 `mock` runner profile。它同样到不了六成员路径,
  但理由不同(Bridge 枚举排除 + 驱动族未注册),取证方式也不同。
- `POST /api/v1/coding-runs/mock` 在生产构建里同样存在,且**无鉴权**(§6 发现 F-1)。
  它到不了验收路径靠的是「没有任何调用方」,不是靠开关或凭据。所以 §5 的取证条目
  用访问日志正面证明它在验收窗口内零调用,而不是断言它不可调用。

---

## 2. 静态调用图:`mock_coding_agent_factory` 的全部消费者

声明与注入:

| # | 位置 | 角色 |
|---|---|---|
| 1 | `src/repomesh/bootstrap/container.py:365` | `ApplicationContainer.mock_coding_agent_factory: Callable[[str], CodingAgent]` 字段声明 |
| 2 | `src/repomesh/bootstrap/app.py:32` | `from repomesh.integrations.coding_agents.mock import MockCodingAgent, MockScenario` |
| 3 | `src/repomesh/bootstrap/app.py:426` | 生产组合根注入 `lambda scenario: MockCodingAgent(MockScenario(scenario))` |
| 4 | `scripts/run_local_dev.py:51` | 本地开发脚本的组合根,注入同一 lambda |
| 5 | `tests/conftest.py:38` | 测试容器夹具,注入同一 lambda |

读取(消费)该字段的位置,全仓库仅一处:

| # | 位置 | 触发方式 |
|---|---|---|
| 6 | `src/repomesh/modules/agent_runtime/api/router.py:512` | `agent = container.mock_coding_agent_factory(body.scenario)`,在 `run_mock_agent` 处理函数内 |

从 6 向上一层,到达网络面:

| # | 位置 | 事实 |
|---|---|---|
| 7 | `src/repomesh/modules/agent_runtime/api/router.py:509` | `@router.post("/coding-runs/mock", response_model=CodingRunView, status_code=202)` |
| 8 | `src/repomesh/api/router.py:42` | `api_router.include_router(agent_runtime_router, prefix="/api/v1")` → 完整路径 `POST /api/v1/coding-runs/mock` |

调用链到此闭合。链路末端的实现是 `src/repomesh/integrations/coding_agents/mock/adapter.py:21`
的 `MockCodingAgent`(`name = "mock"`),它执行的是 `ExecuteCodingRun`,一个与 Worker
执行面(`StartAssignedWorkerTask` / `DispatchWorkerTask` / runner gateway)完全并列、
互不调用的旧执行入口。

**该路径的全部入口只有一个 HTTP 请求。** 复核过的三处否定事实:

- 前端零引用:`frontend/` 与 `web/` 中没有 `coding-runs` 字符串;全仓库只有
  `src/repomesh/modules/agent_runtime/api/router.py` 与一份 2026-08-07 的历史记录文档提到它。
- 适配器注册表零收录:`build_default_registry()`(`registry.py:38`)=
  `AdapterRegistry(create_adapters())`,而 `create_adapters()`(`catalog.py:302`)
  只从 `SPECS` 构造 `CliAgentAdapter`。`MockCodingAgent` 不在 `SPECS` 里,
  因此 `adapter_id` 无论取什么值都解析不到它。
- 容器托管 Worker 执行面零引用:`container.worker_execution_service()`
  (`container.py:1945`)→ `StartAssignedWorkerTask` → `worker_task_dispatcher()`
  (`container.py:1930`)→ `runner_gateway()`。整条链不读 `mock_coding_agent_factory`;
  `grep -rn "mock" src/repomesh/modules/agent_runtime/` 全部命中都在上表第 7/6 那一个
  处理函数里(`router.py:509` 装饰器、`:510` 函数名、`:512` 工厂调用),别无他处。

---

## 3. 论证:六成员路径为何结构上到不了该工厂

六成员验收路径的执行形态是 external Bridge:每个 Codex 实例由
`repomesh-agent-bridge` 进程自带 consumer,把 `repomesh_runner` 组合起来去驱动本机
codex CLI。它与 RepoMesh 后端之间只有 HTTP,且只走三条路(租约 / 事件 / start action)。

### 3.1 Bridge 不经过 `ApplicationContainer`

`ApplicationContainer` 是 RepoMesh 后端进程内的组合根。Bridge 是**另一个进程、
另一个组合根**:`src/repomesh_agent_bridge/cli.py` 自行装配
`GovernedRuntime` / `GovernedRunConsumer`(`runner_consumer.py:1090-1119`),
其依赖是 `HttpLongPollTaskSource`、`HttpEventSink`、`AppServerDriver`、`DriverExecutor`。
`grep -rn "MockCodingAgent|mock_coding_agent_factory|CodingAgent"
src/repomesh_agent_bridge/ src/repomesh_runner/` **零命中**——两个包都不认识这个类型,
也无从注入它。跨进程唯一的通道是 HTTP,而 §2 已确认 Bridge 不请求
`/api/v1/coding-runs/mock`。

### 3.2 Bridge 的 profile 在启动期就被钉死为 codex

- `src/repomesh_agent_bridge/contracts.py:123`:
  `CODING_PROFILES = ("codex", "claude-code", "kimi")`,enrollment schema 的
  `codingProfile` 枚举。注释写明这是 `repomesh_runner.profiles.PROFILES` 的**真子集**,
  「Runner 可以带 Bridge 不许驱动的 profile(验证用的 `mock` 正是这种)」。
  `tests/agent_bridge/test_wire_contracts.py:120` 已用 `assert "mock" not in CODING_PROFILES` 钉死。
- `src/repomesh_agent_bridge/application.py:90`:enrollment 校验拒绝枚举外的 `codingProfile`。
- `src/repomesh_agent_bridge/cli.py:450`:装配会话时,`coding_profile != CODEX_PROFILE_ID`
  直接 `BridgeStartupError` 起不来。
- `src/repomesh_agent_bridge/cli.py:365`:装配受治理执行时,同样的拒绝。
- `src/repomesh_agent_bridge/cli.py:461` 与
  `src/repomesh_agent_bridge/adapters/coding_session.py:218`:实际使用的 profile 是
  常量 `get_profile(CODEX_PROFILE_ID)`,不来自输入。

也就是说:一个 profile 不是 codex 的 Bridge **不会启动**,而不是降级运行。

### 3.3 即便任务侧被塞入 `mock`,Bridge 的驱动表里也没有它

Bridge 上报给 RepoMesh 的 `adapter_id` 就是自己的 `coding_profile`
(`cli.py:388`,`RepoMeshGovernedTaskAdapter(adapter_id=enrollment.coding_profile)`),
因此租回来的任务只可能是 `codex`。退一步做最坏假设——若任务里的 `adapterId` 是 `mock`:

`DriverExecutor.execute`(`src/repomesh_runner/executor.py:201-206`)按
`task.adapter_id` 解析 profile,再按 `profile.family` 找驱动。而 Bridge 只注册了一个族
(`runner_consumer.py:1107-1109`,`drivers={DriverFamily.APP_SERVER: driver}`),
`mock` profile 的族是 `DriverFamily.STREAM_JSON`(`profiles.py:158-181`),
于是抛 `DriverError("mock: no driver registered for stream_json")`——**响亮失败,
不是静默替身**。这条也不通往 `mock_coding_agent_factory`:它属于另一个 mock(§4.2)。

### 3.4 与该工厂的距离,一句话

`mock_coding_agent_factory` 位于 RepoMesh 后端进程、只被一个无人调用的 HTTP 处理函数读取;
六成员路径的执行发生在六个 Bridge 进程里,它们既不共享该进程的容器对象,也不请求那个端点。
两者之间没有调用边,不是「被开关关掉了」,而是**结构上不相连**。

---

## 4. 诚实清单:哪些配置/路径**会**触达 mock

零触达的结论只对六成员验收路径成立。以下路径会真的跑到 mock,验收期间必须确认它们未被使用。

### 4.1 `MockCodingAgent`(即本字段)

| 路径 | 触达方式 | 验收期是否可能出现 |
|---|---|---|
| `POST /api/v1/coding-runs/mock` | 任何人手工发一个 HTTP 请求(**无鉴权**,见 F-1) | 不应出现;§5 用访问日志正面取证 |
| `tests/conftest.py:38` 夹具 | pytest 内的容器,进程内 | 不涉及活体验收 |
| `scripts/run_local_dev.py:51` | 本地开发脚本的组合根 | 若验收用它拉后端,端点同样存在;取证方式与生产一致 |
| `tests/contracts/test_mock_coding_agent.py`、`tests/test_domain.py:115`、`tests/agent_runtime/test_authorized_execution.py:73` | 单测直接构造 | 不涉及活体验收 |

### 4.2 runner 镜像内的 `repomesh-mock-agent`(**另一个 mock**)

与 `mock_coding_agent_factory` 无关,但同属 §8 FAIL 的「fake adapter」范畴:

| 路径 | 触达方式 | 验收期是否可能出现 |
|---|---|---|
| `mock` runner profile(`src/repomesh_runner/profiles.py:158`) | 任务的 `adapter_id == "mock"` 时被 `DriverExecutor` 解析 | Bridge 不会发出这个 adapter_id(§3.3);**容器托管 Worker 若被这样派活则会触达** |
| runner 镜像内的可执行文件(`components/repomesh-runner/Dockerfile:64-65`) | 镜像里始终装着 `repomesh-mock-agent` 启动器 | 镜像存在 ≠ 被使用;取证靠容器清单为空 |
| `tests/runner/test_mock_agent_executable.py` | 单测真起子进程驱动它 | 不涉及活体验收 |

`mock` profile 的 docstring 已自陈:「Validation-only profile: NOT a vendor CLI…
Never use it to serve real work: it does not read or write the workspace and never calls a model.」

### 4.3 Bridge 的 `--inert` 生产替身(**第三种 fake**)

`src/repomesh_agent_bridge/cli.py:94` 的 `--inert` 开关会把会话换成
`InertCodingSession`(`adapters/memory.py:491`)。它不是测试替身,是生产里的诚实兜底:
被 @ 时永远只回同一句 `INERT_SESSION_NOTE`(`adapters/memory.py:55`)。
`cli.py:276` 使 `--inert` 与 `--workspace-root` 互斥,所以一个 inert Bridge 不可能执行受治理任务
——但它**可以在房间里说话**,因此六实例中若混入一个 inert,AC-05/AC-07 的房间证据看起来仍是「有回应」。
这条必须单独取证(§5 条目 E-5)。

---

## 5. V2 取证清单条目(只读命令)

终局验收当场执行,全部只读。`<...>` 为验收现场填入的值;不要把任何 token 写进报告。

**E-1 · 后端从未处理过 mock 端点**
在验收窗口的后端日志里搜端点路径,期望 0 行:

```bash
docker compose logs --since "<验收开始时间>" <api 服务名> | grep -c "coding-runs/mock"   # 期望 0
```

后端若不是容器起的,对进程日志文件跑同一 grep。**注意反向证据的方向**:E-1 证明的是
「零调用」,不是「不可调用」——该端点确实无鉴权(F-1)。

**E-2 · 六个 Bridge 的 enrollment profile 都是 codex**
`repomesh-agent-bridge check` 是只读子命令(`cli.py:140-143`),自陈
「matrix sync: not started (check joins nothing) / coding session: not spawned
(check spawns nothing)」。对六份 enrollment 各跑一次,读 `_report` 的输出:

```bash
repomesh-agent-bridge check --enrollment <每份 enrollment 文件> | grep -E "^(profile|containerManaged|role):"
# 期望每份都是 profile: codex 与 containerManaged: false
```

这一条同时兑现验收标准 §9 证据清单第 4 条(六份 `containerManaged: false` 只读证据)。

**E-3 · 没有 Bridge 进程带 `--inert`**
Bridge 在 Windows 主机上运行,取进程命令行:

```powershell
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -like '*repomesh-agent-bridge*' } |
  Select-Object ProcessId, CommandLine     # 期望六条,且没有一条含 --inert
```

**E-4 · 没有 RepoMesh runner Worker 容器在验收窗口内跑过**
容器托管路径是 §4.2 唯一能触达 `mock` profile 的路径,验收要求它压根没被用:

```bash
docker ps -a --filter "ancestor=repomesh-runner" --format "{{.Names}}\t{{.Status}}\t{{.CreatedAt}}"
# 期望:空,或全部 CreatedAt 早于验收窗口
```

**E-5 · 房间里没有出现 inert 兜底话术**
对三组房间的消息导出(或 RepoMesh Room 页导出的同一批消息)搜:

```bash
grep -c "this build cannot run a coding session yet" <房间消息导出>   # 期望 0
```

**E-6 · 租出去的任务 adapterId 全是 codex**
任务载荷带 `adapterId`(`src/repomesh_runner/contracts.py:169`),由 RepoMesh 的任务源
返回给 Bridge 的长轮询。取任一侧即可,两侧都拿到最好:

```bash
grep -ho '"adapterId": *"[^"]*"' <Bridge 日志 或 后端任务源日志> | sort | uniq -c
# 期望:只有 "adapterId": "codex" 一种
```

若日志级别没有记录载荷,退化为 E-4 + E-2 的联合证明:容器托管路径未启用(E-4),
且六个 Bridge 上报的 adapter_id 就是各自 enrollment 的 `coding_profile`
(`cli.py:388`),而那六个值已由 E-2 核过。

**E-7 · 三个 Draft PR 的正文不含任何 mock 痕迹,且追溯八字段齐全**
Q1 落地后,主路径 PR 正文带 issue / change_set / plan / repository / task / run /
worker_agent / branch / commit。逐个 PR 目视核对,并确认 `- worker_agent:` 指向的是
六个 external AgentPrincipal 之一,而不是任何容器托管身份。

---

## 6. 审计发现

**F-1(未修,报告项):`POST /api/v1/coding-runs/mock` 无鉴权。**
`run_mock_agent`(`router.py:509-512`)不调用同文件里的 `_authorize_agent_action` /
`_authorize_runner`,`agent_runtime_router` 挂载时(`api/router.py:42`)也没有
router 级 `dependencies`。它执行的是进程内的 `MockCodingAgent`,不写工作区、不接触
仓库凭据,所以影响面是「任何能访问 API 的人都能在服务端伪造一条 coding run 记录」,
而不是代码执行。**这不构成 Q3a 的阻断**:零触达结论建立在「没有任何调用方」之上,
E-1 用日志正面证明,不依赖鉴权。是否收口留给主脑裁决。

**未发现 V2 配置下 mock 可被触达的路径。** 若终局验收时 §5 任一条目结果与期望不符,
按工单纪律停工上报,不得就地修改产品代码。
