# AI 网关修复进展记录（2026-08-13）

> 本文档记录 agentteams AI 网关（Higress）链路排查与修复的当前状态，供下次继续时参考。

---

## 1. 背景与目标

- AgentTeams 的 AI 网关基于 Higress（运行在 `agentteams-controller` 容器内），负责把 worker / manager 的 LLM 请求转发到上游模型服务。
- 现象：worker 执行任务时调用 LLM 报 404 `No available model found for model_code: qwen3.6-plus`，说明模型名与上游不匹配。
- 目标：修复网关链路，让 worker 能正常调用模型。

## 2. 关键决策（务必理解）

**我们没有使用 qwen 模型，也没有使用 Higress 内置的 qwen provider。**

实际配置是：

- **LLM 服务来源 = 麦芽（maiya）网关**，地址 `192.168.77.248:10006/maiya/v1`
- **协议** = openai 兼容（`openai/v1`）
- **模型** = `DeepSeek-V4-Flash`（用户唯一可用的模型）

也就是说：在初始化时选择了 `openai-compat` provider + 自定义 base URL + 自定义模型名，而不是默认的 qwen 全家桶。

由此引发的问题链：

1. 网关 / 上游的模型列表里**没有 qwen3.6-plus**（只有 `DeepSeek-V4-Flash` 等）。
2. worker 的配置里之前填的是 `qwen3.6-plus` → 上游 404。
3. 修复方式 = 把**所有** worker 配置里的模型名统一改成 `DeepSeek-V4-Flash`，同时补齐网关缺失的 consumer 凭证。

## 3. 当前架构与链路

```
worker 容器 (openai SDK / httpx)
   │  HICLAW_AI_GATEWAY_URL = http://aigw-local.hiclaw.io:8080
   ▼
Higress 网关 (agentteams-controller 容器内, 监听 8080)
   │  ① key-auth wasm 认证（7 个 consumer，Bearer key）
   │  ② ai-route "deepseek-route"（domain * / aigw-local.hiclaw.io, path /）
   │  ③ ai-proxy wasm（openai-compat provider, agentteamsMode=true）
   ▼
上游：192.168.77.248:10006/maiya/v1（麦芽网关，openai 兼容）
   ▼
LLM 模型：DeepSeek-V4-Flash
```

### 关键配置快照

| 配置项 | 位置 | 内容 |
|---|---|---|
| ai-route | `/data/configmaps/ai-route-deepseek-route.yaml` | `deepseek-route`，domains `["*","aigw-local.hiclaw.io"]`，path `/`，upstreams `[{provider: openai-compat, weight: 100, modelMapping: {}}]`，allowedConsumers 7 个 |
| ai-proxy wasm | `/data/wasmplugins/ai-proxy.internal.yaml` | openai-compat：`agentteamsMode: true`，`openaiCustomUrl: http://192.168.77.248:10006/maiya/v1`，`openaiCustomServiceName: openai-compat.dns.static`，port 10006，protocol openai |
| key-auth wasm | `/data/wasmplugins/key-auth.internal.yaml` | 7 个 consumer：manager / worker-alice / worker-bob / worker-rm-leader-a-client / worker-rm-worker-a-client / worker-rm-leader-a-api / worker-rm-worker-a-api |
| McpBridge | `/data/mcpbridges/default.yaml` | `openai-compat.dns` → 192.168.77.248:10006 (static)，另有 higress-console、tuwunel、llm-openai-compat.internal |
| worker 模型 | MinIO `/data/minio/hiclaw-storage/agents/*/openclaw.json` + `providers.json` | `agents.defaults.model.primary` = `hiclaw-gateway/DeepSeek-V4-Flash`，`active_llm.model` = `DeepSeek-V4-Flash` |

### 已确认的 key（consumer 凭证）

- `worker-rm-worker-a-api` = `5a47904192a4af16a5a165c3f70ff5c7fa0fc4303e2c56586875ee175d2aa939`
- `manager` = `230ff414dcf947b6eefdc27f8083f48808218ba0bc6b260e277b65bc041ff7a8`
- `worker-rm-leader-a-api` = `e4782acf352139dc8d8bd833a324d110430d3df69f7907806119c90d28961248`

## 4. 已完成的工作

### 4.1 模型名统一改为 DeepSeek-V4-Flash ✅

- 排查并修改了全部 4 个 RepoMesh worker 容器 + alice/bob 的本地配置（`/root/hiclaw-fs/agents/*/openclaw.json`、`providers.json`）
- 同步修改 MinIO 源配置（`/data/minio/hiclaw-storage/agents/*/openclaw.json` + `providers.json`），保证 HICLAW_FS_SYNC 重新同步后不会被覆盖回旧模型名
- 修改内容：
  - `agents.defaults.model.primary` → `hiclaw-gateway/DeepSeek-V4-Flash`
  - `agents.defaults.models` 补充 `DeepSeek-V4-Flash` 条目
  - `models.providers.hiclaw-gateway.models` 补充 `DeepSeek-V4-Flash` 条目
  - `providers.json` 的 `active_llm.model` → `DeepSeek-V4-Flash`

### 4.2 补齐 Higress consumer 凭证 ✅

- 原 `key-auth.internal` 缺失 `worker-rm-worker-a-api` 等 consumer，导致 401。
- 用正确 payload 格式创建（`credentials: [{"type":"key-auth","source":"BEARER","key":null,"values":[key]}]`，**不能**用 `source/name` 简写格式，会返回空 201 但不生效）。
- 现 7 个 consumer 全部在列，`/v1/models` 认证通过。

### 4.3 验证结论（当前状态）

| 测试 | 结果 |
|---|---|
| 直接 POST 麦芽上游 `192.168.77.248:10006/maiya/v1/chat/completions`（带 model） | ✅ 200，正常 |
| 通过网关 `GET /v1/models`（worker key） | ✅ 200，模型列表含 `DeepSeek-V4-Flash`（共 114 个模型） |
| 通过网关 `POST /v1/chat/completions`（**不带** model，如 `{}`） | ✅ 正常转发到上游（上游回 404 model 为空） |
| 通过网关 `POST /v1/chat/completions`（**带** model，如 `{"model":"DeepSeek-V4-Flash",...}`） | ❌ **400 `{"detail":"API error: bad request, http smuggling"}`** |
| 通过网关 `POST` 带 model `qwen3.6-plus` | 404（预期，上游无此模型） |

## 5. 当前阻塞问题：400 http smuggling

### 5.1 现象

- **带 `model` 字段的 POST 请求** 通过网关时 100% 返回 400 `{"detail":"API error: bad request, http smuggling"}`（响应头 `server: istio-envoy`）。
- 请求根本没到达上游（上游 echo/maiya 无记录）。
- **不带 `model` 字段** 的请求正常转发 → 说明 key-auth、ai-route 匹配、上游转发本身都正常。

### 5.2 已排除的原因

- ❌ 客户端问题：curl / python http.client / 手工 raw socket 全部触发，且 `Content-Length` 与 body 完全一致。
- ❌ 认证问题：`/v1/models` 同 key 同路径 200。
- ❌ 上游问题：直连麦芽 200。
- ❌ TE/CL 并存：envoy 原生返回 `Bad Request`（不同格式），不是这个错误。

### 5.3 当前判断（最可能的根因）

- 错误字符串 `API error: bad request, http smuggling` 不在任何 wasm 插件二进制中（strings 全扫过），判定来自 **higress-core / model-router 内置模块**（随 envoy 镜像内置）。
- 机制推测：Higress 的 ai-route 在请求**带 model 字段**时会触发"模型路由"逻辑 —— 内置模块读取 body 提取 model 后，重放 body 时 `Content-Length` 与实际 body 长度不一致 → 触发 http smuggling 防护。
- 佐证：之前成功转发到 10006 echo server 的一条记录显示，转发请求 `transfer-encoding: chunked` 且 **body 为空** —— body 在被读取后丢失。
- 也就是说：**模型名本身没问题了，卡在网关对"带 model 的请求体"的处理上。**

### 5.4 待尝试的方向（未执行）

1. 确认当前 Higress 版本 / higress-core 版本，查是否有已知 bug 或修复版本。
2. 对照官方 ai-route 完整配置（veast 文章示例包含 `version`、`headerPredicates`、`urlParamPredicates`、`modelPredicates`、`fallbackConfig` 字段），**我们的 ai-route 缺少这些字段**，补齐 `modelPredicates`/`fallbackConfig` 或调整 `modelMapping` 可能改变 http-source 读 body 的行为。
3. 检查 `openaiCustomServiceName: openai-compat.dns.static`（带 `.static`）与 McpBridge 里 `openai-compat.dns` 的命名是否应保持一致（initializer.go 代码生成的是 `openai-compat.dns`，实际配置被改成了 `.dns.static`）。
4. 参考 Higress 官方 issue（如 #4174 static provider 报错）与文档确认 openai-compat + 自定义 URL 的正确配置姿势。

## 6. 遗留环境信息（勿忘）

- 容器内 `python3 /tmp/echo_server2.py 10006` 是**调试遗留进程**，占用 10006 端口（会记录收到的请求到 `/tmp/echo_recv.log`）。排查完应清理，避免干扰真实麦芽服务。
- 网关日志：`/var/log/hiclaw/higress-gateway.log`（只记了 matrix 请求，AI 请求未落盘；envoy stdout/stderr 均指向 /dev/null，wasm 日志不可见）。
- 调试脚本均在容器 `/tmp/`：`verify_gateway.sh`（三种测试一键跑）、`smuggle_test.py`（body 变体测试）、`dump_ai_cm.py`（dump configmaps）、`scan_wasm_smuggle.sh`（扫描 wasm 字符串）。
- 宿主机路径 `d:\Workspace\Agentteams\GOAI-infra-repomesh`，agentteams 源码在 `components/agentteams`，网关相关代码：`agentteams-controller/internal/gateway/higress.go`、`internal/initializer/initializer.go`（provider/route 初始化逻辑）。

## 7. 待办

- [ ] 修复网关对"带 model 的 POST 请求"的 400 http smuggling（见 5.4）
- [ ] 修复后验证：通过网关用 worker key 调用 `DeepSeek-V4-Flash` 成功
- [ ] 清理调试遗留：echo server（10006）、临时脚本
- [ ] 触发/观察 worker 任务，确认通知摘要 issue 能跑通
