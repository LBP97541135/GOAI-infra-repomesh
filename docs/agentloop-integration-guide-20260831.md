# AgentLoop 接入方案（可观测接续）

> 目标：把 RepoMesh 现有 OTel 锚点接续到阿里云 AgentLoop，零代码改动。
> 日期：2026-08-31 ｜ 状态：**通道 A 三信号（Trace/Metrics/Logs）代码+网关验证通过 ✅ / 通道 B 已配置待重启生效**

## 0. 接入状态

| 通道 | 状态 |
|---|---|
| A：RepoMesh API/runner → AgentLoop（Trace） | ✅ 已配置 + headers 透传 + 真实上报验证通过（控制台可见 `repomesh-connectivity-check`） |
| A：RepoMesh API/runner → AgentLoop（Metrics） | ✅ 已接入：`setup_metrics` + `_metrics_url` 拼接 + LLM/工具调用计数器埋点；网关 `/v1/metrics` 实测 200 |
| A：RepoMesh API/runner → AgentLoop（Logs） | ✅ 已接入：`setup_logs` + `_logs_url` 拼接 + `LoggingHandler` 自动附加 trace_id；网关 `/v1/logs` 实测 200 |
| B：AgentTeams Manager/Workers → AgentLoop | ✅ 端到端打通（2026-08-31 实测）：controller/manager 重建注入 CMS 7 变量 → worker openclaw.json 注入 `openclaw-cms-plugin`（trace）+ `diagnostics-otel`（metrics）→ `[ArmsTrace] Exporter initialized (service=agentteams-worker-e2e)` → 容器内 OTLP traces/metrics/logs 三端点实测 200 |

## 1. 核心结论

- **AgentLoop = 标准 OpenTelemetry 平台**（阿里云 CMS 2.0 下的 Agent 全栈可观测），按 OTLP/HTTP 协议直报，支持 Trace / Token / Tool 调用 / Agent 轨迹。
- **接续 = 配环境变量 + 重启**，现有埋点锚点（约 70% 已写好、0% 在导出）全部直接复用，不改一行业务代码。
- AgentTeams 运行时走 **CMS 2.0 默认集成**，官方明确：不需要装 ARMS 探针、不需要在集成中心选采集方式——创建 AgentSpace 时绑定的 CMS 2.0 workspace 即完成对接。

## 2. 两条上报通道

```
┌────────────────────────────────────────────────────────────┐
│  AgentLoop（AgentSpace → CMS 2.0 workspace）                │
│  endpoint: https://<project>.cn-hangzhou.log.aliyuncs.com/  │
│           apm/trace/opentelemetry                           │
│  headers : x-arms-license-key / x-arms-project /            │
│            x-cms-workspace                                  │
└───────────────▲───────────────────────────▲────────────────┘
                │ OTLP/HTTP                   │ OTLP/HTTP（AGENTTEAMS_CMS_*
                │ REPOMESH_OTLP_*              │ 自动注入 OTEL_EXPORTER_*）
        ┌───────┴───────┐             ┌────────┴────────┐
        │ 通道A：RepoMesh│             │ 通道B：AgentTeams│
        │ api + runner  │             │ manager+workers │
        │ planning.*    │             │ 思考/LLM/工具调用 │
        │ gen_ai.* mcp.*│             │ token 计量/轨迹  │
        └───────────────┘             └─────────────────┘
```

| 通道 | 覆盖范围 | 配置位置 | 生效方式 |
|---|---|---|---|
| A：RepoMesh | 计划编排 planning.*、LLM 调用 gen_ai.*、MCP 工具 mcp.*、observer root/tool_use span | 仓库根 `.env` | 重启 api / runner |
| B：AgentTeams | Agent 容器侧黑盒：思考轨迹、LLM 调用、工具调用、token 计量 | `~/agentteams-manager.env` | 重启 manager、重建 worker |

## 3. 前置准备：开通 AgentLoop（一次性，约 5 分钟）

1. 阿里云账号完成**实名认证**。
2. 开通四个服务（按控制台指引开通即可）：
   - 云监控 CloudMonitor（CMS 2.0）
   - 日志服务 SLS（Simple Log Service）
   - MSE AI Governance Center
   - AgentLoop
3. RAM 用户授权系统策略 `AliyunAgentLoopFullAccess`。
4. 登录 **AgentLoop 控制台**：`https://agentloop.console.alibabacloud.com/`
5. **Agent Spaces 列表 → 右上角 Create Space**，填写：
   - Region：`cn-hangzhou` / `cn-shanghai` / `cn-hongkong`（**创建后不可改**）
   - Agent Space 名称：3–63 字符，小写字母/数字/连字符/下划线，**全局唯一、创建后不可改**
   - CMS 2.0 Workspace：选 **Auto-create**（系统自动创建 `agentloop-<32位码>`）或复用已有 workspace
   - 系统自动绑定：MSE namespace + SLS project（均为 `agentloop-<32位码>`）
6. 创建后到 **System Management > Space Management** 确认资源绑定齐全。

## 4. 需要收集的参数（4 个）

创建好 AgentSpace 后，从控制台收集以下值（后续配置全靠它们）：

| 参数 | 控制台位置 | 映射到环境变量 |
|---|---|---|
| Endpoint（OTLP 上报地址） | 集成中心 / CMS 2.0 接入文档（OTel 接入段） | `AGENTTEAMS_CMS_ENDPOINT`、`REPOMESH_OTLP_ENDPOINT` |
| LicenseKey（访问凭证） | 集成中心 | `AGENTTEAMS_CMS_LICENSE_KEY`（header `x-arms-license-key`） |
| Project（SLS 项目名） | 空间管理 > 资源绑定（SLS project，形如 `agentloop-<32位码>` 或 `proj-xtrace-xxx-cn-hangzhou`） | `AGENTTEAMS_CMS_PROJECT`（header `x-arms-project`） |
| Workspace（CMS 2.0 空间 ID） | 空间管理 > 资源绑定（形如 `default-cms-xxx-cn-hangzhou`） | `AGENTTEAMS_CMS_WORKSPACE`（header `x-cms-workspace`） |

Endpoint 格式参考：
```
https://<project>.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry
```

## 5. 通道 A：RepoMesh API / runner

### 配置（已落地 2026-08-31）

仓库根目录 `.env` 已填入真实参数：

```ini
# ---- AgentLoop / OTLP 上报（通道 A：RepoMesh 控制面）----
REPOMESH_OTLP_ENDPOINT=https://proj-xtrace-2eb66b38475880aa4d34533055742318-cn-hangzhou.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry
REPOMESH_OTLP_HEADERS=x-arms-license-key=j2mvbfwr8q@92c1c70037310fa,x-arms-project=proj-xtrace-2eb66b38475880aa4d34533055742318-cn-hangzhou,x-cms-workspace=agentloop-f6b04100cd32575d72ff2676eabf7af7
REPOMESH_OTLP_SERVICE_NAME=repomesh-api
# ---- Metrics / Logs 开关（2026-08-31 新增，默认 false）----
REPOMESH_OTLP_METRICS_ENABLED=true
REPOMESH_OTLP_LOGS_ENABLED=true
REPOMESH_OTLP_LOG_LEVEL=INFO   # 经 LoggingHandler 上云的最低日志级别
```

> ⚠️ 重要：`REPOMESH_OTLP_ENDPOINT` 填 AgentLoop 控制台给的 **base 接入点**（`.../apm/trace/opentelemetry`），SDK 会追加信号路径。实测直接 POST 到 base 路径返回 404；追加 `/v1/traces`、`/v1/metrics`、`/v1/logs` 后均返回 200（2026-08-31 实测）——不要自行改成带 `/v1/...` 的完整地址，也不要截掉路径。

> 注：runner 进程（如独立启动的 `repomesh_runner`）读取同名 `REPOMESH_OTLP_ENDPOINT`，默认 service_name 为 `repomesh-runner`；若要区分可加 `REPOMESH_OTLP_SERVICE_NAME=repomesh-runner`。runner 侧开关为 `REPOMESH_OTLP_METRICS_ENABLED` / `REPOMESH_OTLP_LOGS_ENABLED` / `REPOMESH_OTLP_LOG_LEVEL`（`"1"/"true"/"yes"` 判定）。

### 生效机制（2026-08-31 已扩展代码支持 AgentLoop 鉴权 + 三信号）

- `src/repomesh/bootstrap/app.py:818` → `setup_tracing(settings.otlp_endpoint, service_name=settings.otlp_service_name, headers=settings.otlp_headers)`，随后按开关调用 `setup_metrics(...)` / `setup_logs(...)`
- `src/repomesh/settings.py:93-96` → `otlp_endpoint` / `otlp_service_name` / `otlp_headers`（`REPOMESH_OTLP_HEADERS`，格式 `k=v,k2=v2`）；新增 `otlp_metrics_enabled` / `otlp_logs_enabled` / `otlp_log_level`
- `src/repomesh_runner/main.py:150` → 读 `REPOMESH_OTLP_ENDPOINT` + `REPOMESH_OTLP_HEADERS` + 三个开关，无值则 no-op
- `src/repomesh_runner/telemetry.py` → `setup_tracing` / `setup_metrics` / `setup_logs` 均透传 headers；`_traces_url` / `_metrics_url` / `_logs_url` 保持"base + /v1/信号路径"拼接（幂等）
- Metrics 打点：`src/repomesh/integrations/llm/deepseek.py`（LLM 调用次数、输入/输出 token，`repomesh.llm.*`）+ `src/repomesh_runner/observer.py`（工具调用次数，`repomesh.tool.calls`）——proxy-meter 机制：未启用时 no-op，启用后自动解析到真实 MeterProvider
- Logs：`LoggingHandler` 挂到 root logger，自动给每条日志附加当前 `trace_id`/`span_id`（日志 ⇄ Trace 联动）；SDK 已对该类发 DeprecationWarning，后续可迁移 `opentelemetry-instrumentation-logging`
- 测试：`tests/test_telemetry.py` 新增 AgentLoop endpoint 拼接、headers 解析、`setup_metrics`/`setup_logs` 安装与 no-op 用例（共 11 个全部通过）

### 重启

```powershell
docker compose up -d --force-recreate api
# 若 runner 独立进程：重启 runner 使其读到新环境变量
```

### 验证记录（2026-08-31 ✅ 已通过）

```bash
.venv\Scripts\python.exe <check_agentloop.py>  # 真实 endpoint + headers 上报测试 span
# 输出：tracing enabled: True / force_flush returned: True（无 404/鉴权错误）
```

控制台已可见服务 `repomesh-connectivity-check` 的测试 trace。

**三信号网关可达性（2026-08-31 10:29 实测，空 POST 探测）：**

| 路径 | HTTP 状态 | 结论 |
|---|---|---|
| base（无信号路径） | 404 | 必须追加信号路径 |
| `/v1/traces` | 200 | Trace 通道通 |
| `/v1/metrics` | 200 | Metrics 通道通 |
| `/v1/logs` | 200 | Logs 通道通 |

代码侧：`ruff check .` 全绿 + `pytest tests/test_telemetry.py` 11 个用例全部通过。

## 6. 通道 B：AgentTeams Manager / Workers

### 配置

CMS 配置挂在 **controller** 上（embedded 镜像内嵌 MinIO/Higress），由 controller 下发 manager，manager 在拉起 worker 时传播。三处容器都需要以下 7 个变量：

```bash
AGENTTEAMS_CMS_TRACES_ENABLED=true
AGENTTEAMS_CMS_METRICS_ENABLED=true
AGENTTEAMS_CMS_SERVICE_NAME=agentteams-manager
AGENTTEAMS_CMS_ENDPOINT=https://<project>.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry
AGENTTEAMS_CMS_LICENSE_KEY=<LicenseKey>
AGENTTEAMS_CMS_PROJECT=<SLS project>
AGENTTEAMS_CMS_WORKSPACE=<CMS workspace id>
```

（实测值见 `~/agentteams-manager.env` / controller 启动参数；worker 的 `SERVICE_NAME` 由 manager 自动设为 `agentteams-worker-<WORKER_NAME>`。）

### 生效机制（2026-08-31 实测修正：不是环境变量自动生效）

> 文档原稿（v1.0.9 注入 `OTEL_EXPORTER_OTLP_*` 环境变量）**与 worker 实际行为不符**。
> 实测：openclaw 主体**不消费** `OPENCLAW_CMS_PLUGIN_DIR` 环境变量，CMS 上报必须经
> **openclaw.json 的 `plugins` 段显式启用插件**，配置存放在 **MinIO**（`agentteams-storage/agents/<WORKER_NAME>/openclaw.json`），worker 启动时拉取并 merge 到本地。

真实链路：

```
controller（embedded，7 CMS 变量）
  └─ 拉起 manager（copaw，继承 7 变量，SERVICE_NAME=agentteams-manager）
       └─ 拉起 worker（openclaw，继承 7 变量）
            └─ openclaw.json（存于 MinIO）plugins 段：
                 openclaw-cms-plugin（trace）:
                   endpoint = AGENTTEAMS_CMS_ENDPOINT
                   headers  = {x-arms-license-key, x-arms-project, x-cms-workspace}
                   serviceName = agentteams-worker-<WORKER_NAME>
                 diagnostics-otel（metrics，可选）:
                   diagnostics.otel.{enabled,endpoint,headers,serviceName,metrics:true}
```

- 官方注入逻辑在 `components/agentteams/manager/agent/skills/worker-management/scripts/generate-worker-config.sh`（177–235 行），条件：`AGENTTEAMS_CMS_TRACES_ENABLED=true` + endpoint/license/workspace 非空 + 插件 manifest 存在。
- worker 侧 merge 规则（`merge-openclaw-config.sh`）：MinIO 版提供 `plugins.entries` base / `plugins.load.paths` 并集 / models / gateway / channels，本地自定义保留。
- diagnostics-otel 需要 npm 生产依赖：镜像内 package.json 的 `devDependencies` 含 `workspace:*`（monorepo 协议），容器内 `npm install --omit=dev` 会报 `EUNSUPPORTEDPROTOCOL`。落地时已 `jq 'del(.devDependencies)'` 后重装成功（102 包）。

### 落地记录（2026-08-31 ✅）

1. **重建 controller + manager**：旧三容器为 3 小时前创建，CMS 7 变量从未注入（install 脚本只在进程 env 有值时 `-e` 传入）。`rebuild-controller.ps1` / `rebuild-manager.ps1` 提取原 env 后注入 CMS 7 变量重建。
2. **manager 自动恢复 worker**：manager 重建后自动拉起 `agentteams-worker-e2e` 且 7 变量齐全（传播机制验证通过）。
3. **手动注入 worker openclaw.json**（manager 未自动重新生成）：在 worker 容器内用官方 jq 逻辑注入 `openclaw-cms-plugin` + `diagnostics-otel` 到 MinIO 版配置（`mc cat` → jq → `mc cp`），重启 worker。
4. **验证通过**：日志出现 `[plugins] [ArmsTrace] Plugin activated (endpoint: …, service: agentteams-worker-e2e)`、`[gateway] ready (4 plugins: diagnostics-otel, matrix, memory-core, openclaw-cms-plugin)`、`[ArmsTrace] Exporter initialized`；容器内 OTLP 三端点实测 200。

### 重启

```bash
# 1) 重启 Manager（读取新环境变量）
docker restart agentteams-manager
# 2) worker 由 manager 自动恢复；若需要强制刷新 openclaw.json：
#    改 MinIO 中的 agentteams-storage/agents/<WORKER_NAME>/openclaw.json 后
docker restart agentteams-worker-<WORKER_NAME>
```

## 7. 验证

### 云端验证（控制台）

1. 登录 CMS 2.0 控制台（`https://cmsnext.console.aliyun.com/`），选择目标 workspace。
2. **AI Agent 可观测 / LLM Observability > Model Applications**：确认应用出现。
3. **Trace Analysis**：执行一次完整任务（如创建计划 → worker 执行 → 完成），看：
   - Trace ID、总 token 消耗、Agent 数、LLM 调用数、Tool 调用数
   - 调用树含 AGENT / LLM / TOOL / MCP 等 span 类型

### 本地验证

```bash
# Endpoint 可达性（应返回 2xx/3xx 或 TLS 握手成功）
curl -I https://<project>.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry

# 容器内 OTLP 环境变量已注入（worker 容器内执行）
env | grep OTEL_EXPORTER

# API 日志应出现 tracing 初始化日志（INFO 级别）
docker logs <api-container> 2>&1 | grep -i tracing

# 通道 B：worker 插件激活 + exporter 初始化（worker 容器日志）
docker logs agentteams-worker-e2e 2>&1 | grep -E "ArmsTrace|ready \(4 plugins"
# 期望：Plugin activated … service: agentteams-worker-e2e / Exporter initialized

# 通道 B：worker 内 OTLP 三端点冒烟（应各返回 200）
docker exec agentteams-worker-e2e sh -c 'for s in traces metrics logs; do curl -s -o /dev/null -w "$s %{http_code}\n" -X POST "https://<project>.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry/v1/$s" -H "x-arms-license-key: <LicenseKey>" -H "x-arms-project: <SLS project>" -H "x-cms-workspace: <workspace>" -H "Content-Type: application/x-protobuf" --data-binary ""; done'

# 通道 B：确认 openclaw.json 已含 CMS 插件（worker 容器内）
jq '.plugins.entries | keys' /root/agentteams-fs/agents/e2e/openclaw.json
# 期望：["diagnostics-otel","matrix","memory-core","openclaw-cms-plugin"]
```

> 数据为批量上报（BatchSpanProcessor），控制台有 1–2 分钟延迟，属正常现象。

## 8. 排错 FAQ

| 现象 | 排查顺序 |
|---|---|
| 控制台无数据 | ① Endpoint 可达？`curl -I` ② LicenseKey 无空格/换行 ③ 容器确实重启 ④ `docker logs` 看报错 ⑤ 等 1–2 分钟刷新 |
| 通道 A 无数据 | `.env` 是否被 compose 读取（`docker compose config \| grep OTLP`）；api 是否 `--force-recreate` 重启 |
| 通道 B 无数据（worker） | ① openclaw.json 是否含 cms 插件：`jq '.plugins.entries' ~/agentteams-fs/agents/e2e/openclaw.json` ② 无 → 按 §6 落地记录手动注入 MinIO 版并重启 worker ③ `docker logs worker \| grep ArmsTrace` 应有 `Exporter initialized` |
| worker 日志报 `EUNSUPPORTEDPROTOCOL` | diagnostics-otel 的 `workspace:*` devDependency 导致 npm install 失败 → 容器内 `jq 'del(.devDependencies)' /opt/openclaw/extensions/diagnostics-otel/package.json` 后重装（§6） |
| API 容器 `Restarting (255)`（迁移失败） | ① 镜像是否包含最新 merge 迁移（`alembic upgrade head` 报 Multiple head → `docker compose build api`）② `alembic_version` 是否滞后于实际 schema（手工对齐后 `upgrade head`）③ 迁移是否缺 `CREATE SCHEMA`（本次 decision_chain 属 0001 已建，非 0033 缺陷） |
| 通道 B 无数据 | Manager env 文件路径是否正确；worker 是否重建（旧容器不继承 CMS 变量）；openclaw.json 是否含 cms 插件条目（见上行） |
| 401 / 鉴权失败 | LicenseKey 与 workspace 是否属于同一 AgentSpace；region 是否一致 |

## 9. 后续可选增强（按性价比排序）

1. **Higress 网关 OTel 插件**：Agent 所有 LLM 调用都过网关，埋 1 点覆盖全部流量（token/TTFT 计量最完整）——需要新部署网关（架构变更），本轮未做，仍是最高性价比动作。
2. 补 `retrieval.*` span：扫描链路已有 dep_evidence / identities / exposed_apis 数据，缺 span 埋点。
3. ~~结构化日志带 `trace_id`~~ → **已落地（2026-08-31）**：`setup_logs` + `LoggingHandler`，日志自动附加 trace_id/span_id。
4. ~~OTLP Metrics 上报~~ → **已落地（2026-08-31）**：`setup_metrics` + `repomesh.llm.calls` / `repomesh.llm.tokens.*` / `repomesh.tool.calls` 计数器；后续可把 alerting 的 estimated_cost / latency_p95 / success_rate 指标继续同步上报，与自建告警双轨。

## 10. 关联文档

- AgentTeams 侧官方参数文档：`components/agentteams/docs/cms-integration.md`
- 可观测现状盘点：`docs/可观测性-赛题对照与实现方案-2026-08-14.md`
- 评委答疑 Q3-1（AgentLoop 接入方式）：`docs/chenwenhui/GOAI复赛-评委答疑会-准备问题清单-2026-08-27.md`
- 官方 quickstart：https://www.alibabacloud.com/help/en/agentloop/latest/getting-started
