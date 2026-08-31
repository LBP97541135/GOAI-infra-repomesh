# AgentLoop 接入方案（可观测接续）

> 目标：把 RepoMesh 现有 OTel 锚点接续到阿里云 AgentLoop，零代码改动。
> 日期：2026-08-31 ｜ 状态：**通道 A 已验证上云 ✅ / 通道 B 已配置待重启生效**

## 0. 接入状态

| 通道 | 状态 |
|---|---|
| A：RepoMesh API/runner → AgentLoop | ✅ 已配置 + 代码已支持 headers + 真实上报验证通过（控制台可见 `repomesh-connectivity-check`） |
| B：AgentTeams Manager/Workers → AgentLoop | ✅ 已配置（`agentteams-manager.env`）⏳ 待重启 manager + 重建 worker 生效 |

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
```

> ⚠️ 重要：`REPOMESH_OTLP_ENDPOINT` 填 AgentLoop 控制台给的 **base 接入点**（`.../apm/trace/opentelemetry`），SDK 会追加 `/v1/traces`。实测直接 POST 到 base 路径返回 404，追加 `/v1/traces` 后才可达——不要自行改成带 `/v1/traces` 的完整地址，也不要截掉路径。

> 注：runner 进程（如独立启动的 `repomesh_runner`）读取同名 `REPOMESH_OTLP_ENDPOINT`，默认 service_name 为 `repomesh-runner`；若要区分可加 `REPOMESH_OTLP_SERVICE_NAME=repomesh-runner`。

### 生效机制（2026-08-31 已扩展代码支持 AgentLoop 鉴权）

- `src/repomesh/bootstrap/app.py:818` → `setup_tracing(settings.otlp_endpoint, service_name=settings.otlp_service_name, headers=settings.otlp_headers)`
- `src/repomesh/settings.py:93-96` → `otlp_endpoint` / `otlp_service_name` / `otlp_headers`（`REPOMESH_OTLP_HEADERS`，格式 `k=v,k2=v2`）
- `src/repomesh_runner/main.py:150` → 读 `REPOMESH_OTLP_ENDPOINT` + `REPOMESH_OTLP_HEADERS`，无值则 no-op
- `src/repomesh_runner/telemetry.py` → `setup_tracing(endpoint, service_name, headers)` 将 headers 透传给 OTLPExporter；`_traces_url` 保持"base + /v1/traces"拼接
- 测试：`tests/test_telemetry.py` 新增 AgentLoop endpoint 拼接与 headers 解析用例

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

## 6. 通道 B：AgentTeams Manager / Workers

### 配置

在 `~/agentteams-manager.env`（Manager 容器环境变量文件）追加：

```bash
# ---- AgentLoop / CMS 2.0 上报（通道 B：Agent 容器侧）----
AGENTTEAMS_CMS_TRACES_ENABLED=true
AGENTTEAMS_CMS_ENDPOINT=https://<project>.cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry
AGENTTEAMS_CMS_LICENSE_KEY=<LicenseKey>
AGENTTEAMS_CMS_PROJECT=<SLS project>
AGENTTEAMS_CMS_WORKSPACE=<CMS workspace id>
AGENTTEAMS_CMS_SERVICE_NAME=agentteams-manager
```

### 生效机制（AgentTeams v1.0.9+ 已内置，无需改代码）

- `hermes/scripts/hermes-worker-entrypoint.sh`（137–139 行）、`qwenpaw/scripts/qwenpaw-worker-entrypoint.sh`（73–75 行）、`manager/scripts/init/start-copaw-manager.sh`（182–187 行）自动将 `AGENTTEAMS_CMS_*` 注入为：
  ```bash
  export OTEL_EXPORTER_OTLP_ENDPOINT="${AGENTTEAMS_CMS_ENDPOINT}"
  export OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"
  export OTEL_EXPORTER_OTLP_HEADERS="x-arms-license-key=${AGENTTEAMS_CMS_LICENSE_KEY},x-arms-project=${AGENTTEAMS_CMS_PROJECT},x-cms-workspace=${AGENTTEAMS_CMS_WORKSPACE}"
  ```

### 重启

```bash
# 1) 重启 Manager（读取新环境变量）
docker restart agentteams-manager
# 2) 删除重建 Worker 容器，使其从 Manager 继承 OTLP 配置
#    （Worker 由 Manager 拉起，重启后自动带 OTEL_EXPORTER_*）
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
```

> 数据为批量上报（BatchSpanProcessor），控制台有 1–2 分钟延迟，属正常现象。

## 8. 排错 FAQ

| 现象 | 排查顺序 |
|---|---|
| 控制台无数据 | ① Endpoint 可达？`curl -I` ② LicenseKey 无空格/换行 ③ 容器确实重启 ④ `docker logs` 看报错 ⑤ 等 1–2 分钟刷新 |
| 通道 A 无数据 | `.env` 是否被 compose 读取（`docker compose config \| grep OTLP`）；api 是否 `--force-recreate` 重启 |
| 通道 B 无数据 | Manager env 文件路径是否正确；Worker 是否重建（旧容器不继承）；`env \| grep OTEL` 是否在容器内可见 |
| 401 / 鉴权失败 | LicenseKey 与 workspace 是否属于同一 AgentSpace；region 是否一致 |

## 9. 后续可选增强（按性价比排序）

1. **Higress 网关 OTel 插件**：Agent 所有 LLM 调用都过网关，埋 1 点覆盖全部流量（token/TTFT 计量最完整）——此前规划文档评估的最高性价比动作。
2. 补 `retrieval.*` span：扫描链路已有 dep_evidence / identities / exposed_apis 数据，缺 span 埋点。
3. 结构化日志带 `trace_id`：实现"日志 ⇄ Trace"联动定位。
4. OTLP Metrics：将 alerting 现有 estimated_cost / latency_p95 / success_rate 指标同步上报，与自建告警双轨。

## 10. 关联文档

- AgentTeams 侧官方参数文档：`components/agentteams/docs/cms-integration.md`
- 可观测现状盘点：`docs/可观测性-赛题对照与实现方案-2026-08-14.md`
- 评委答疑 Q3-1（AgentLoop 接入方式）：`docs/chenwenhui/GOAI复赛-评委答疑会-准备问题清单-2026-08-27.md`
- 官方 quickstart：https://www.alibabacloud.com/help/en/agentloop/latest/getting-started
