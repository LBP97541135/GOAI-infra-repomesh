# AI 网关修复总结：麦芽的问题 + 新配置（2026-08-14）

> 交接文档。只记录两件事：**① 发现的原上游（麦芽网关）的问题；② 本次要改的新配置（gemini-3-flash，经 vibeapi.cn）。**
> 一切以 Console API 实际下发值为准，不要看 configmap 快照。

---

## 一、发现麦芽的问题

### 1.1 现象

通过网关 `POST /v1/chat/completions`（带 model）返回 **400 http smuggling**，worker 无法调用 LLM。

### 1.2 诊断过程（tcpdump 抓包实证）

原上游为麦芽网关：`192.168.77.248:10006`（uvicorn/FastAPI，OpenAI 兼容）。

| 请求方式 | 结果 |
|---|---|
| Higress 转发（带 model） | **400 http smuggling** |
| Higress 转发（无 model） | 404（body 被改写后上游不认识 model） |
| 直连麦芽（Content-Length） | 200 正常 |

### 1.3 根因

1. **Higress 模型路由转发请求必带 `Transfer-Encoding: chunked` 头**，且不带 `Content-Length`（tcpdump + pcap 解析实证；官方 GitHub Issue **#2490** 确认此为内置行为，**无配置开关**，只能改代码或换上游）；
2. **麦芽（uvicorn 安全检测）拒绝"带 model 的 chunked 请求"**，返回 400 http smuggling；无 model 的 chunked 则被接受。

### 1.4 结论

麦芽上游与 Higress 的 chunked 转发不兼容，**放弃麦芽，切换为兼容性好的 OpenAI 兼容代理（vibeapi.cn）**。

---

## 二、要改的新配置（gemini-3-flash via vibeapi.cn）

### 2.1 新上游

| 项 | 值 |
|---|---|
| Base URL | `https://www.vibeapi.cn/v1` |
| API Key | `sk-BodQQPYCDtj2zHRJnQt6PLwqlxoXPwk0AqEIiwWPkwDZBcq4` |
| 模型 | `gemini-3-flash` |
| 说明 | new-api 网关（v1.7.38），模型列表含 gemini-3-flash；直连 200 验证通过 |

### 2.2 网关 provider 最终配置（Console API，provider 名仍为 `openai-compat`）

```jsonc
{
  "tokens": ["sk-BodQQPYCDtj2zHRJnQt6PLwqlxoXPwk0AqEIiwWPkwDZBcq4"],   // 顶层 tokens 也必须更新！
  "rawConfigs": {
    "openaiCustomUrl": "https://www.vibeapi.cn/v1",
    "openaiCustomServiceName": "vibeapi.dns",        // 指向服务源（不能直接填裸域名！）
    "openaiCustomServicePort": 443,
    "apiTokens": ["sk-BodQQPYCDtj2zHRJnQt6PLwqlxoXPwk0AqEIiwWPkwDZBcq4"],
    "agentteamsMode": true,
    "type": "openai",
    "protocol": "openai/v1"
  }
}
```

> ⚠️ 改配置踩坑（已解决）：
> 1. **裸域名不会生成 cluster**——`openaiCustomServiceName` 直接填 `www.vibeapi.cn` 会 503（envoy 无该 cluster），必须通过服务源（McpBridge）引用；
> 2. **static 类型服务源不接受域名**（只接受 IP），报 400；**dns 类型接受域名**，故建 dns 服务源；
> 3. **PUT provider 时顶层 `tokens` 和 `rawConfigs.apiTokens` 必须一起改**，否则顶层旧值覆盖新值。

### 2.3 新增服务源（Console API）

```jsonc
{ "type": "dns", "name": "vibeapi", "domain": "www.vibeapi.cn", "port": 443, "protocol": "https", "properties": {} }
```

（dns 类型 → cluster 名 = `vibeapi.dns`，与 `openaiCustomServiceName` 自洽。）

### 2.4 模型名统一改为 `gemini-3-flash`

- **容器侧**：4 个 RepoMesh worker（`rm-worker-a-api` / `rm-leader-a-api` / `rm-worker-a-client` / `rm-leader-a-client`）中所有 json 已替换：
  - `openclaw.json`、`.copaw/providers.json`、`.copaw.secret/providers.json`、`.copaw.secret/providers/active_model.json`、`.copaw.secret/providers/custom/hiclaw-gateway.json`；
- **MinIO 权威源**：`hiclaw/hiclaw-storage/agents/<agent>/...` 共 **7 个对象**已通过 `mc cp` 下载→替换→上传更新；
- ⚠️ worker 进程运行中可能把旧配置写回，改完后需再 grep 确认无旧模型名残留（已确认 0 残留）。

### 2.5 修复 worker 访问网关的 DNS 别名

worker 经 `http://aigw-local.hiclaw.io:8080` 访问网关，但网关容器原先在 `hiclaw-net` 上没有该网络别名，worker 内解析到 127.0.0.1 → Connection refused。修复：

```bash
docker network disconnect hiclaw-net agentteams-controller
docker network connect --alias aigw-local.hiclaw.io hiclaw-net agentteams-controller
```

修复后 worker 内 `getent hosts aigw-local.hiclaw.io` → `172.20.0.3`（网关容器 IP）。

### 2.6 验证结果（全部 200，真实回复）

在 **4 个 worker 容器内部**（非宿主机）执行：

```
GET  http://aigw-local.hiclaw.io:8080/v1/models            → HTTP 200
POST http://aigw-local.hiclaw.io:8080/v1/chat/completions
     {"model":"gemini-3-flash","messages":[{"role":"user","content":"ping"}]}
                                                          → HTTP 200，返回 "Pong!" 等真实内容
```

- worker key（Bearer）：`5a47904192a4af16a5a165c3f70ff5c7fa0fc4303e2c56586875ee175d2aa939`
- 网关地址：`http://aigw-local.hiclaw.io:8080`（= agentteams-controller 容器 8080）

---

## 三、给接手 agent 的关键操作备忘

- **查真实配置**（以 Console 为准）：容器 `agentteams-controller` 内 `python3 /tmp/diag_step2.py`（已就绪，登录 kissie/1159633cwhabc）。
- **网关 provider 编辑套路**：GET → 改 `tokens` + `rawConfigs` → PUT（保 version）。
- **改模型名**：`replace_model_all.py`（容器内 /tmp，遍历所有 json）+ `fix_minio.py`（mc 更新 MinIO）。
- **验证链路**：worker 容器内跑 `/tmp/gw_conn_verify.sh`。
- 遗留（与本次无关）：`hiclaw-manager` 等容器 crash-loop（Matrix 凭据错误 `M_FORBIDDEN`）；麦芽上游若日后恢复使用，需先解决 Higress chunked 兼容（Issue #2490，无开关）。
