# AgentScope Studio 本地验证记录（2026-08-07）

- 作者：catmem
- 关联：`docs/development/observability-instrumentation-plan-20260807.md`（第 1.1 节、第 5 节）
- 目的：把观测方案「线 C（展示端）」里的空洞用实测数据填上——真实端口、真实路由、
  GenAI 属性渲染、`repomesh.*` 自定义属性渲染、iframe 集成该用什么 URL
- 环境：Windows 11 Home China 10.0.26200，Node v22.22.1，npm 11.13.0

---

## 0. 结论速览

| 问题 | 实测结论 |
|---|---|
| 装得上吗 | 装得上。`npm i -g @agentscope/studio`，v1.0.9，bin 名 `as_studio` |
| Web UI 端口 | 默认 3000，**本机被 Docker 容器 `multica-frontend-1` 占了，Studio 自动退到 3001** |
| OTLP/HTTP | 与 Web UI **同端口**，路径 `/v1/traces`。本机实测 `http://localhost:3001/v1/traces` |
| OTLP/gRPC | **默认就开，不需要设 `OTEL_GRPC_PORT`**。本机 4317 正常监听 |
| `/session/{agent_name}` 路由 | **不存在**。SPA catch-all 会重定向到 `/overview` |
| 单条 trace 有 URL 吗 | **没有**。trace 详情是 `/tracing` 页面上的一个抽屉，点开后 URL 一个字都不变 |
| iframe 该用什么 URL | `http://<host>:<port>/tracing`（只能定位到列表页）。要按 trace 深链**必须自建前端**，走 tRPC `getTrace` |
| GenAI 属性 | 认。`gen_ai.usage.*` 汇总成列表页 Tokens 列，`gen_ai.input/output.messages` 渲染成 Input/Output 面板 |
| `repomesh.*` 自定义属性 | 认，在「All Attributes」里按点号还原成嵌套 JSON 展示 |
| Resource 属性 | **存了但 UI 不显示**。想在 Studio 里看见 `run_id`，必须写成 **span 属性**，不能只放 Resource |

---

## 1. 安装

```bash
node --version   # v22.22.1  （要求 >= 20，package.json engines: node>=20, npm>=10）
npm  --version   # 11.13.0

npm install -g @agentscope/studio
```

- 版本：**1.0.9**
- 全局包路径：`C:\Users\18092\AppData\Roaming\npm\node_modules\@agentscope\studio`
- `package.json` 声明 `"bin": { "as_studio": "./bin/cli.js" }`

### 1.1 坑：安装慢，且第一次没有任何输出

本机走的是 `registry.npmmirror.com`。整包依赖树很大（`typeorm` + `better-sqlite3` +
`@grpc/grpc-js` + `mongodb` / `mssql` / `ioredis` 等 typeorm 的一堆 optional driver），
**实测耗时 > 10 分钟**。`better-sqlite3` 是原生模块，走 prebuild 下载。

第一次用 `npm install -g ... 2>&1 | tail -30` 跑，管道会把输出全缓冲住，
10 分钟内看不到一个字节，很容易误判成卡死。**排查时请直接重定向到文件**，
配 `--loglevel=info`：

```bash
npm install -g @agentscope/studio --loglevel=info > install.log 2>&1
```

### 1.2 Windows 上的启动命令

npm 生成了三个 shim，都在 `C:\Users\18092\AppData\Roaming\npm\`：

| 文件 | 用途 |
|---|---|
| `as_studio` | Git Bash / MSYS shell |
| `as_studio.cmd` | cmd.exe（**从 Git Bash 后台起进程时用这个最稳**） |
| `as_studio.ps1` | PowerShell |

三种写法都可以：

```bash
as_studio                                              # PATH 里有 %APPDATA%\npm 时
"C:\Users\18092\AppData\Roaming\npm\as_studio.cmd"     # 显式路径，本次验证用的
npx @agentscope/studio                                 # 不装全局也行，但每次都要解依赖
```

`bin/cli.js` 只干两件事：没设 `NODE_ENV` 就设成 `production`，然后
`require('../dist/server/src/index.js')`。生产模式下会**自动 `opener` 打开浏览器**
（打的是 `http://localhost:<port>/home`，随后被前端 catch-all 重定向到 `/overview`）。

---

## 2. 端口实测

### 2.1 默认值与覆盖方式

源码 `dist/shared/src/config/server.js`：

```js
exports.ServerConfig = {
    port: parseInt(process.env.PORT || DEFAULT_CONFIG.server.port.toString()),          // 3000
    otelGrpcPort: parseInt(process.env.OTEL_GRPC_PORT || DEFAULT_CONFIG.server.otelGrpcPort.toString()), // 4317
    database: { type: 'better-sqlite3', database: <appdata>/database.sqlite },
};
```

即：**`PORT` 控 Web UI + OTLP/HTTP，`OTEL_GRPC_PORT` 控 OTLP/gRPC**。已实测：

```bash
PORT=3400 OTEL_GRPC_PORT=4417 as_studio
#     Studio UI:      http://localhost:3400
#       - HTTP:       http://localhost:3400/v1/traces
#       - gRPC:       http://localhost:4417
```

### 2.2 gRPC 4317 是默认开的

方案文档原文写「OTLP/gRPC 在 4317（`OTEL_GRPC_PORT`）」容易被读成「要设这个变量才开」。
**实测不是**：`OtelGrpcServer` 在 `initializeServer()` 里无条件 `start(actualGrpcPort)`，
`OTEL_GRPC_PORT` 只是**改端口号**用的，不是开关。

裸启动（不设任何环境变量）后：

```
TCP    0.0.0.0:3001    LISTENING    40524
TCP    0.0.0.0:4317    LISTENING    40524     ← 同一个 PID
```

TCP 连 `127.0.0.1:4317` 成功。**注意**：本次未做 gRPC 端到端发包验证，
因为仓库 `.venv` 里只有 `opentelemetry-exporter-otlp-proto-http`，
没装 `opentelemetry-exporter-otlp-proto-grpc`。要验证需先补这个依赖。
如果启动时 gRPC 起不来，代码是 `console.warn` 后继续跑，不会退出——
所以**看到 UI 起来了不等于 gRPC 起来了**，得看 banner 或 netstat。

### 2.3 坑一：3000 端口冲突（本机已发生）

```
HTTP port 3000 is already in use.
Automatically using available HTTP port 3001 (non-interactive mode)
```

占用者是 Docker 容器 `multica-frontend-1`（`127.0.0.1:3000->3000/tcp`，
宿主进程 `com.docker.backend.exe`）。方案文档第 5 节担心的「3000 与 Grafana 撞」，
本机是更早地和 Docker 项目撞了。

行为分两种（`dist/server/src/index.js`）：

- **交互式终端**（stdin/stdout 都是 TTY）：弹 `Would you like to start on port N instead? (y/n)`，
  答 n 就 `process.exit(1)`
- **非交互式**（后台/管道/CI）：自动往上找空闲端口（`portfinder`，搜索窗口 base+2000），静默换端口

### 2.4 坑二：换掉的端口**不落盘**

`setPort()` 只改内存里的 config，**从头到尾没有调 `saveConfig()`**。
实测 `%APPDATA%\AgentScope-Studio\` 下只有 `database.sqlite`，没有 `config.json`。

结论：**自动退让选出来的端口是不确定的，重启后可能变**。
既然要给前端 iframe 用、要给线 A/B 的 exporter 配 endpoint，
**必须显式 `PORT=xxxx` 固定**，别依赖默认值和自动退让。

建议给本项目定一个不跟 Grafana(3000)/Docker 抢的端口，例如：

```bash
PORT=3900 OTEL_GRPC_PORT=4319 as_studio
```

### 2.5 坑三：监听在 0.0.0.0，不是 127.0.0.1

`httpServer.listen(port)` 没指定 host，实测 `netstat` 显示 `0.0.0.0:3001` 和 `0.0.0.0:4317`。
**局域网内任何人都能打开你的 trace 面板**，而 trace 里带完整 prompt / 输出消息。
方案文档第 7 节「凭据不得进入 trace」的约束在这里同样成立，
另外本地跑也建议加防火墙规则或反代限制，别裸奔。

### 2.6 本次验证的实际落点

| 项 | 值 |
|---|---|
| Studio UI | `http://localhost:3001` |
| OTLP/HTTP traces | `http://localhost:3001/v1/traces` |
| OTLP/gRPC | `localhost:4317` |
| 进程 PID | 40524（`node .../@agentscope/studio/bin/cli.js`） |
| SQLite | `C:\Users\18092\AppData\Roaming\AgentScope-Studio\database.sqlite` |
| 配置目录 | `%APPDATA%\AgentScope-Studio\`（Windows）；macOS `~/Library/Application Support/AgentScope-Studio` |

---

## 3. 发测试 span

临时脚本放在会话 scratchpad（**未进仓库**）：
`%TEMP%\claude\...\scratchpad\send_test_spans.py`。

用仓库 `.venv`（`opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`，SDK 1.44.0）跑：

```bash
cd D:\Project4work\GOAI-infra-repomesh\.claude\worktrees\obs-line-a
OTLP_ENDPOINT=http://localhost:3001/v1/traces uv run python <scratchpad>/send_test_spans.py
```

发了一棵三层树，全部用 `SimpleSpanProcessor`（逐条导出，子 span 先于父 span 到达）：

| span | `gen_ai.operation.name` | 关键属性 |
|---|---|---|
| `invoke_agent repomesh-planner`（根） | `invoke_agent` | `gen_ai.agent.{name,id,description}`、`gen_ai.input/output.messages`、`repomesh.{run_id,task_id,changeset_id,repository_id,attempt,adapter}`、一个 span event `governance.check` |
| `chat deepseek-chat`（子） | `chat` | `gen_ai.request.{model,temperature,max_tokens}`、`gen_ai.response.{model,id,finish_reasons}`、`gen_ai.usage.input_tokens=123` / `output_tokens=456` |
| `execute_tool Read`（子） | `execute_tool` | `gen_ai.tool.name` / `.call.id` / `.call.arguments` / `.call.result`、`repomesh.run_id` / `task_id` / `permission.decision` |

三者 `gen_ai.conversation.id` 都设成 `repomesh-verify-conv-001`。
Resource 上另放了 `service.name=repomesh-verify` 和 `repomesh.project_id/repository_id/attempt/adapter`。

结果：

```
trace_id      = 53e2a455cb32808b1cee34ecec1ac89d
```

Studio 侧日志三条全收：

```
[OTEL] Received OpenTelemetry traces request: POST /traces
[OTEL] Content-Type: application/x-protobuf
```

**没有跨 span 组装问题**：三条独立 POST（子先父后）进来后，UI 里正确还原成
父子树。Studio 是按 `traceId` + `parentSpanId` 在读取时组装的，不依赖到达顺序。

`/v1/traces` 接受 `application/x-protobuf`、`application/vnd.google.protobuf`、
`application/protobuf`、`application/octet-stream`、`application/json`，body 上限 **10mb**。
空 body POST 返回 `422`。

---

## 4. URL 路由表（实测）

路由枚举来自前端 bundle 里的 `RouterPath`，逐条在浏览器地址栏验证过：

| 页面 | URL 模板 | 说明 |
|---|---|---|
| 根 | `/` | 前端 catch-all 重定向 → `/overview` |
| 总览 | `/overview` | 侧栏 Develop > Overview。Projects/Runs/Tokens/Invocations 四张卡 + 月度柱状图 |
| 项目列表 | `/projects` | 侧栏 Develop > Projects |
| 项目详情 | `/projects/{projectName}` | 嵌套路由 `/:projectName/*` |
| Run 列表 | `/projects/{projectName}/runs` | |
| Run 详情 | `/projects/{projectName}/runs/{runId}` | |
| **Trace 列表** | **`/tracing`** | 侧栏 Develop > Traces。**这是我们唯一用得上的页面** |
| Friday（内置 copilot） | `/friday` | 侧栏 Agent > Friday |
| Friday 对话 | `/friday/chat` | |
| Friday 设置 | `/friday/setting` | |
| 评测总览 | `/eval` | **有路由但侧栏里没有入口** |
| 评测详情 | `/eval/{evalId}` | |
| 评测任务详情 | `/eval/{evalId}/task/{taskId}` | |
| 评测对比 | `/eval/{evalId}/compare/` | |
| 任意其它路径 | `*` | `<Navigate to="/overview" replace />` |

侧栏实际分组（实测文本）：
`Develop`（Overview / Projects / Traces）、`Agent`（Friday）、
`Document`（Tutorial / API，外链 doc.agentscope.io）、`Contact`（GitHub / DingTalk / Discord）、底部 `Settings`（弹窗，不是路由）。

### 4.1 `/session/{agent_name}` 路由：**不存在**

方案文档第 5 节的疑点确认成立。实测：

```
浏览器访问 http://localhost:3001/session/repomesh-planner
→ 地址栏立刻变成 http://localhost:3001/overview
```

服务端 `curl` 侧看不出来，因为 Express 对所有非 `/trpc` 路径都回 `index.html`：

```
GET /session/repomesh-planner  ->  200      ← 是 SPA 壳，不是真页面
GET /overview                  ->  200
```

所以队友方案里 `iframe src="localhost:3000/session/{agent_name}"` 的写法，
**接进去会得到一个 Overview 首页**，不会报错，但也永远不是想要的东西——
这种「静默错」比 404 更难排查，务必别照抄。

Studio 的信息架构里**根本没有「agent / session」这个维度**：
它只认 Project → Run（AgentScope SDK 上报的）和 Trace（OTLP 上报的）。

---

## 5. Trace 详情页：**没有 URL**（最关键的一条）

这是本次验证最重要的发现，直接决定前端接法。

### 5.1 从首页点到单条 trace 的完整路径

```
/overview
  → 侧栏 Develop > Traces
    → /tracing                                （地址栏：http://localhost:3001/tracing）
      → 表格里点任意一行
        → 右侧滑出「Node Details」抽屉         （地址栏：http://localhost:3001/tracing —— 一个字都没变）
          → 抽屉左栏是 span 树，点某个 span
            → 右栏换成该 span 的详情           （地址栏：仍然是 http://localhost:3001/tracing）
```

前端源码印证：`/tracing` 下面**没有任何子路由**——

```js
TracePage = () => <TraceContextProvider pollingInterval={5000} pollingEnabled>
                      <div className="h-full flex flex-1"><TraceListPage /></div>
                  </TraceContextProvider>
```

`TraceListPage` 里选中的 trace / span 全是 React 组件 state，
既不写 path 也不写 query string（全 bundle 里 `useSearchParams` 只被 react-router 内部用了一次，
业务代码没用）。

**结论：`http://host:port/tracing?traceId=xxx`、`/tracing/{traceId}` 这类 URL 都不存在，
拼了也只会落到空列表页。刷新页面 = 抽屉关闭 = 回到列表。**

### 5.2 但是有 tRPC 接口可以直接取数据

```bash
curl -G "http://localhost:3001/trpc/getTrace" \
     --data-urlencode 'input={"traceId":"53e2a455cb32808b1cee34ecec1ac89d"}'
```

返回：

```json
{"result":{"data":{
  "traceId":"...","spans":[...],
  "startTime":...,"endTime":...,"duration":...,"status":...,"totalTokens":579
}}}
```

每个 span 带 `traceId / spanId / parentSpanId / name / kind /
startTimeUnixNano / endTimeUnixNano / attributes / events / links / status /
resource / scope / conversationId / latencyNs`。

可用的 tRPC procedure（`dist/server/src/trpc/router.js`）：

| procedure | 入参 | 用途 |
|---|---|---|
| `getTrace` | `{ traceId: string }` | 单条 trace 全量 span |
| `getTraces` | `{ pagination, ... }`（`TableRequestParamsSchema`，必填 `pagination`） | trace 列表，支持分页/排序/过滤 |
| `getTraceStatistic` | `{ startTime?, endTime? }` | 顶部统计卡 |
| `getProjects` / `getDataInfo` / `getCurrentVersion` | | 其它 |

`attributes` 已经是**点号还原后的嵌套 JSON**（不是扁平 kv），`resource.attributes` 同理。

### 5.3 iframe 集成建议（明确结论）

| 想要的效果 | 可行性 | 做法 |
|---|---|---|
| 面板里嵌一个「全部 trace」视图 | ✅ 可行 | `<iframe src="http://<host>:<PORT>/tracing">`，**这是唯一能用的模板** |
| 嵌某个 run / task 的 trace | ❌ 不可行 | 无深链，用户进去后还得自己在搜索框里贴 trace_id |
| 嵌某个 agent 的 session | ❌ 不可行 | `/session/*` 路由不存在（见 4.1） |
| 按 trace_id 深链 | ❌ 不可行 | 见 5.1 |

**给前端的明确建议：**

1. **短期**：iframe 只用 `http://<host>:<PORT>/tracing`，
   并在旁边把 `trace_id` 明文展示 + 一个「复制」按钮，让用户自己粘到 Studio 的
   `Search traces in the table` 里。别拼任何带 id 的 URL——**拼了不报错，只是静默错**。
2. **中期**（真要「点 run → 看这条 trace」）：**不要用 iframe**。
   RepoMesh 前端自己调 `GET /trpc/getTrace?input={"traceId":"..."}` 拿 span 数组，
   自己画瀑布图。数据结构就是标准 OTLP span，画起来不复杂，
   而且能顺手把 `repomesh.*` 属性做成一等公民（Studio 只会把它塞进 All Attributes 里）。
3. `PORT` 必须显式固定（见 2.4），否则 iframe 的 src 端口会漂。
4. Studio 监听 0.0.0.0（见 2.5），iframe 跨机访问时注意暴露面。

---

## 6. GenAI 属性渲染情况

抽屉右栏（span 详情）固定四块：**标题（span name）→ 三格摘要（Start Time / Duration / Tokens）
→ Metadata 折叠面板（Input / Output）→ All Attributes 折叠面板**。

### 6.1 `chat` span（点 `chat deepseek-chat`）

| 位置 | 显示内容 | 数据来源 |
|---|---|---|
| 标题 | `chat deepseek-chat` | span name |
| Duration | `300.83ms` | span 起止时间 |
| **Tokens** | **`579`** | `gen_ai.usage.input_tokens`(123) + `output_tokens`(456) 自动求和 |
| Metadata > Input | 渲染成格式化 JSON 的 messages 数组 | `gen_ai.input.messages` |
| Metadata > Output | 同上 | `gen_ai.output.messages` |
| All Attributes | 全量嵌套 JSON | 所有 span 属性 |

**模型名在哪**：只在 span name（`chat deepseek-chat`）和 All Attributes 的
`gen_ai.request.model` / `gen_ai.response.model` 里。**没有独立的「Model」字段**——
所以 span 名一定要按约定写成 `chat {model}`，否则 UI 上看不到模型。

**token 数在哪**：span 详情的 Tokens 格 + trace 列表的 Tokens 列。
列表列是**整条 trace 的汇总**（本例 579，来自唯一那个 chat span）。
`gen_ai.request.temperature` / `max_tokens` / `finish_reasons` 都**只在 All Attributes 里**，没单独渲染。

### 6.2 `invoke_agent` span（根）

- Tokens 显示 `-`（该 span 自己没有 `gen_ai.usage.*`）——
  **详情面板不做子树 roll-up，只有 trace 列表那一层汇总**
- Metadata Input/Output 正常渲染（因为我挂了 `gen_ai.input/output.messages`）
- `gen_ai.agent.name / id / description` 只在 All Attributes 里

### 6.3 `execute_tool` span

工具名同样只体现在 span name（`execute_tool Read`）。
`gen_ai.tool.call.arguments` / `.result` 在 All Attributes 里正常展示。

**一个渲染 bug 级的行为**：这个 span 没有 `gen_ai.input.messages`，
于是 Metadata > **Input 面板直接把整个 attributes JSON 倒了出来**（含 `repomesh` 那一坨），
Output 面板显示 `null`。也就是说 Metadata 面板是「有 messages 就渲染 messages，
没有就 fallback 成全量属性」。不影响可用性，但看起来会以为是重复内容。

### 6.4 span 树 / 瀑布图

抽屉左栏是可折叠的 span 树，默认**只显示根节点，需要点前面的三角展开**：

```
invoke_agent repomesh-planner   675.26ms
├─ chat deepseek-chat           300.83ms
└─ execute_tool Read            100.57ms
```

父子关系、耗时都对。列表页的 `Span Count` 列显示 3，也对。

### 6.5 trace 列表页字段

`Trace Name`（= 根 span name）、`Trace ID`、`Start Time`、`Duration`、`Tokens`、
`Span Count`、`Status`。顶部有 `Last 7 days / Last 30 days / All` 时间筛选、
`Search traces in the table` 搜索框、统计卡（Times / Tokens / Average Latency）。

`Status` 显示 `UNSET`——因为脚本没调 `span.set_status()`。
**线 A/B 埋点时记得显式设 `Status.ERROR`**，否则失败的 span 在列表上和成功的长得一样。

---

## 7. `repomesh.*` 自定义属性渲染情况

### 7.1 span 属性上的 `repomesh.*`：**能看见**

在 `invoke_agent` span 的 **All Attributes** 面板里，点号被还原成嵌套对象：

```json
{
  "gen_ai": { ... },
  "repomesh": {
    "run_id": "9f2c1a4e-0000-4000-8000-000000000001",
    "task_id": "T1-pricing-discount-field",
    "changeset_id": "CS-001",
    "repository_id": "pricing",
    "attempt": 1,
    "adapter": "claude-code"
  }
}
```

`execute_tool` span 上的 `repomesh.permission.decision` 也一样还原成
`{"repomesh":{"permission":{"decision":"allow"}}}`。

**限制**：只在 All Attributes 这一个折叠面板里，
**没有列、没有过滤器、没有按 `repomesh.run_id` 聚合的能力**。
方案文档 4.2 节想要的「按 `repomesh.repository_id` 聚合 BLOCKED」，
Studio 做不到，得走自建后端或 tRPC 拉数据自己算。

### 7.2 Resource 属性上的 `repomesh.*`：**存了但 UI 不显示**

脚本里 `Resource.create({...})` 放的 `service.name` 和
`repomesh.project_id / repository_id / attempt / adapter`，
在 `getTrace` 的 JSON 里确实**每个 span 都带了完整 `resource.attributes`**：

```json
"resource": {"attributes": {
  "telemetry": {"sdk": {"language":"python","name":"opentelemetry","version":"1.44.0"}},
  "service": {"name":"repomesh-verify","instance":{"id":"..."}},
  "repomesh": {"project_id":"proj-verify","repository_id":"pricing","attempt":1,"adapter":"claude-code"}
}}
```

但 UI 的 All Attributes 面板**只渲染 span attributes，不渲染 resource**，
`service.name` 在界面上任何地方都找不到。

> **给线 A/B 的硬性建议**：方案文档 3.3 节打算用
> `OTEL_RESOURCE_ATTRIBUTES=repomesh.run_id=...` 把 run_id 塞进 Resource。
> 数据不会丢（tRPC 能读到），但**在 Studio 界面上看不见**。
> 如果指望人肉在 Studio 里排查，就得**同时**把 `repomesh.run_id` / `task_id`
> 写成 span 属性。CLI 侧（LoongSuite 注入）只能走 Resource，这条得靠自建前端补。

### 7.3 span events：存了，UI 未见渲染

脚本挂的 `governance.check` event 在 `getTrace` JSON 的 `span.events` 里完整存在，
但抽屉里没找到对应的展示区。方案文档 3.2 节「每个校验点记 span event」
——数据留得住，**但要在 Studio 里看得见得改成 span 属性**。

---

## 8. 只有 Traces 页有数据，Projects/Overview 是空的

发完 span 后：

- `/tracing` → 有 1 条 trace ✅
- `/projects` → `No data available`
- `/overview` → Projects 0 / Runs 0 / Tokens 0 / Invocations 0

原因：Project/Run 那条链路是 **AgentScope Python SDK 通过 tRPC `registerRun` +
socket.io 主动注册**的，跟 OTLP 摄取完全是两套。我们只发 OTLP，
所以 Projects/Runs/Overview 永远是空的。

**结论：对 RepoMesh 来说，Studio 只有 `/tracing` 一个页面有意义。**
（这也进一步说明第 5.3 节「中期自建前端」的判断——为了一个页面挂个 Node 服务不划算。）

---

## 9. 坑清单汇总

| # | 坑 | 影响 | 规避 |
|---|---|---|---|
| 1 | npm 安装 >10 分钟且管道会吞输出 | 误判卡死 | `--loglevel=info` 重定向到文件 |
| 2 | 3000 被 Docker 容器 `multica-frontend-1` 占 | Studio 静默换到 3001 | 显式 `PORT=3900` |
| 3 | 自动换的端口**不落盘**（`setPort` 没调 `saveConfig`） | 重启后端口可能变，iframe src 失效 | 同上，永远显式设 `PORT` |
| 4 | 非交互式静默换端口，交互式会阻塞在 y/n 提示 | 脚本/CI 里起可能挂住 | 后台起用 `.cmd` + 重定向（非 TTY） |
| 5 | 监听 `0.0.0.0` 而非 `127.0.0.1` | 局域网可读全部 prompt/输出 | 防火墙或反代 |
| 6 | gRPC 起失败只 `console.warn` 不退出 | UI 正常但 gRPC 静默不可用 | 看 banner / `netstat` 确认 4317 |
| 7 | `/session/{agent}` 不报 404，静默重定向到 `/overview` | 队友方案照抄会得到错页面且难排查 | 见 4.1，别用 |
| 8 | 单条 trace 无 URL | iframe 无法深链 | 见 5.3 |
| 9 | Resource 属性 UI 不显示 | `run_id` 在界面上看不见 | 关键 id 同时写 span 属性 |
| 10 | span events UI 不显示 | 治理校验点看不见 | 关键信息用 span 属性 |
| 11 | 无 `chat {model}` 命名就看不到模型名 | | span name 严格按 GenAI 约定 |
| 12 | 不 `set_status` 则一律 `UNSET` | 失败 span 看不出来 | 显式设 `Status.ERROR` |
| 13 | Windows/Git Bash 下 `tasklist /FI` 被 MSYS 转成 `D:/Git/FI` | 查进程报错 | 用 `powershell -NoProfile -Command "Get-Process -Id N"` |
| 14 | 本次无法截图 | | 运行环境的 Browser pane 不合成帧，`computer{action:"screenshot"}` 5s 超时。所有页面内容以 `document.body.innerText` 文本形式取证，已逐段抄录在第 4/5/6/7 节 |

---

## 10. 进程管理

### 当前状态（**保持运行，供线 A/B 发 span**）

| 项 | 值 |
|---|---|
| PID | **40524** |
| 命令行 | `node C:\Users\18092\AppData\Roaming\npm\node_modules\@agentscope\studio\bin\cli.js` |
| UI | http://localhost:3001 |
| OTLP/HTTP | http://localhost:3001/v1/traces |
| OTLP/gRPC | localhost:4317 |
| 启动日志 | `%TEMP%\claude\D--Project4work-GOAI-infra-repomesh\<session>\scratchpad\studio.log` |

线 A/B 的 exporter 请配：

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3001
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:3001/v1/traces
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

（注意是 **3001 不是 3000**；3000 是 Docker 的 `multica-frontend-1`，往那儿发会 404 或打到别人身上。）

### 怎么停

前台运行时直接 `Ctrl+C`（代码里注册了 `cleanup`：关 socket.io → 停 gRPC → 关 HTTP）。

后台运行时（本次情况）：

```bash
# 1. 找 PID
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { \$_.CommandLine -like '*agentscope*' } | Select-Object ProcessId,CommandLine | Format-List"
# 或
netstat -ano | findstr ":3001" | findstr LISTENING

# 2. 杀（注意 Git Bash 下 taskkill 的斜杠参数会被 MSYS 转换，用 powershell 最稳）
powershell -NoProfile -Command "Stop-Process -Id 40524 -Force"
```

停掉之后数据不丢，全在 `%APPDATA%\AgentScope-Studio\database.sqlite`，
重启 Studio 还能看到。要清空就删这个文件（或用 UI 右下角 Settings 里的存储路径入口）。

---

## 11. 需要回写主方案文档的点

`docs/development/observability-instrumentation-plan-20260807.md` 第 1.1 / 5 节
建议按本文更新：

1. 「OTLP/HTTP 在 3000（与 Web UI 同端口），OTLP/gRPC 在 4317（`OTEL_GRPC_PORT`）」
   → 补充：**3000/4317 只是默认值，冲突时会静默漂移且不落盘；`OTEL_GRPC_PORT` 是改端口不是开关**
2. 第 5 节 `iframe localhost:3000/session/{agent_name}` 那一行的结论从「未见此路由」
   升级为 **「已实测确认不存在，且会静默重定向到 /overview」**
3. 新增一条：**Studio 没有单 trace 深链**，iframe 只能到 `/tracing` 列表页；
   要做「点 run 看 trace」必须自建前端 + tRPC `getTrace`
4. 3.3 节 `OTEL_RESOURCE_ATTRIBUTES` 那段补一句：
   **Resource 属性在 Studio UI 上不可见**，关键 id 需同时写成 span 属性
5. 第 7 节风险清单补一条：**Studio 监听 0.0.0.0**，trace 里含完整 prompt/输出

---

## 附：本次验证用到的原始证据

- 启动 banner（`studio.log`）：`Studio UI: http://localhost:3001` /
  `HTTP: http://localhost:3001/v1/traces` / `gRPC: http://localhost:4317`
- `netstat -ano`：`0.0.0.0:3001` 与 `0.0.0.0:4317` 同属 PID 40524
- trace id：`53e2a455cb32808b1cee34ecec1ac89d`，3 spans，totalTokens 579
- 路由枚举（前端 bundle）：
  `RouterPath = { OVERVIEW:"/overview", PROJECTS:"/projects", TRACING:"/tracing",
  FRIDAY:"/friday", EVAL:"/eval", FRIDAY_CHAT:"/chat", FRIDAY_SETTING:"/setting",
  EVAL_EVALUATION:":evalId", EVAL_TASK:":evalId/task/:taskId" }`
- 端口环境变量（`dist/shared/src/config/server.js`）：`PORT` / `OTEL_GRPC_PORT`
