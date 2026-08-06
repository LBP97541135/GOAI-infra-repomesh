# RunnerTask 与结果回传参考(runtime.v1 中文版)

> Bohan 工作文件夹的中文版(2026-08-05)。正式契约(英文)在
> `contracts/runtime/v1/task-and-result-reference.md`,机器校验的权威是同目录的
> JSON schema。**三者不一致时,以 schema 为准,其次英文正本,最后本文。**
>
> 本文所有 JSON 示例都是由实现代码(`RunnerTask.to_wire()` /
> `RunnerEvent.to_wire()`)真实产出的,不是手写稿。

跨越 RepoMesh ⇄ Runner 边界的只有两个形状:下发的 **RunnerTask**,回传的
**RunnerEvent**(结构化结果装在终态事件里)。

命名约定:wire 上是 camelCase,Python 数据类内部是 snake_case。可选键**缺失
和显式 null 等价**——解析器两种都接受(v1 加法兼容规则)。

---

## 一、RunnerTask(RepoMesh → Runner)

全字段填满的样子:

```json
{
  "schemaVersion": "runtime.v1",

  "organizationId": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "projectId":      "3f1a8b22-91d4-4c3e-8a01-5b7e2d9c4f10",
  "taskId":         "a1b2c3d4-5e6f-4a8b-9c0d-1e2f3a4b5c6d",
  "runId":          "9f8e7d6c-5b4a-4392-8180-7f6e5d4c3b2a",
  "correlationId":  "0e1d2c3b-4a59-4687-9584-3a2b1c0d9e8f",
  "attempt": 1,

  "adapterId": "claude-code",
  "instruction": "完成当前 Task Spec：修正 pricing 的四舍五入规则并补测试",

  "repository": {
    "repositoryId": "5d4c3b2a-1908-4e7f-a6d5-c4b3a2918070",
    "url": "https://github.com/acme/pricing-service.git",
    "baseRevision": "main"
  },

  "workspace": {
    "workspaceId": "ws-91f3a7c2",
    "path": "/srv/repomesh/worktrees/pricing-service/9f8e7d6c",
    "baseSha": "9f2c1ab7e3d4568a0b1c2d3e4f5a6b7c8d9e0f1a"
  },

  "contextBundle": {
    "bundleId": "2b3c4d5e-6f70-4182-9394-a5b6c7d8e9f0",
    "version": 3,
    "manifestUri": "s3://repomesh-bundles/2b3c4d5e/v3/manifest.json",
    "contentHash": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "codingPackageHash": "sha256:2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"
  },

  "permissions": {
    "mode": "accept_edits",
    "allowedTools": ["read", "edit", "test"],
    "disallowedTools": ["git-push"],
    "allowedPaths": ["src/pricing/**", "tests/pricing/**"],
    "deniedPaths": [".github/**", "infra/**"],
    "networkTargets": []
  },

  "testCommands": ["pytest tests/pricing", "ruff check src/pricing"],
  "resumeSessionId": "d76b674d-9bfa-4405-8b01-54d0c5fb8f8d",
  "credentialRefs": ["github-app-token", "anthropic-api-key"],
  "workerAgentId": "6a7b8c9d-0e1f-4203-8415-263748596a7b",

  "idempotencyKey": "a1b2c3d4:run:9f8e7d6c:attempt:1",
  "issuedAt": "2026-08-05T12:30:45+00:00"
}
```

### 字段约束(违反即在解析时拒收)

| 字段 | 约束 |
| --- | --- |
| `schemaVersion` | 必须是 `runtime.v1`,否则整条拒绝 |
| `attempt` | ≥ 1 |
| `adapterId` | 已注册的 profile id:`claude-code` / `codex` / `kimi`(`mock` 仅验证用) |
| `instruction`、`idempotencyKey` | 非空 |
| `issuedAt` | **必须带时区** |
| 各 `*Hash` | `sha256:` + 64 位小写十六进制 |
| 各字符串数组 | 元素非空且不得重复 |
| `permissions.mode` | `default` / `accept_edits` / `auto` / `bypass_permissions` |
| `workspace.path` | 绝对路径,**必须已由平台建好**——Runner 只校验,从不创建 |
| `credentialRefs` | 只放引用**名**;凭证值永不进入契约 |

### 三条语义(都对应已裁决的决策)

- **workspace 给了就是唯一权威**(决策 D1)。Runner 会 resolve 路径并要求它严格
  位于自己配置的根目录之内(`..`/symlink 逃逸被拒),且**从不 clone**——此时
  `repository.url` / `baseRevision` 降级为参考信息。不给 workspace 则退回过渡
  行为:自建 `{workspace_root}/{runId}`,没有隔离保证。
- **`permissions.mode` 只决定"要不要问",永远不决定"允许多少"**(决策 D2/D3)。
  优先级:`deniedPaths > disallowedTools > allowedPaths > allowedTools > mode`。
  拒绝规则在**所有模式**下生效,包括 `bypass_permissions`——bypass 只免掉交互
  确认。没有任何 profile 会映射 CLI 自己的 bypass flag,因为那类 flag 会让 CLI
  停发权限回调,而拒绝规则正是靠回调执行的。
- **`idempotencyKey` 是"至多一次执行"的钥匙**。Runner 把它记进本地账本;同一个
  键重复投递是 no-op(不重跑、不重发事件)。同一次尝试必须复用同一个键,换键
  就等于新任务。
- **`resumeSessionId`** 装的是上一轮事件里回传的 `nativeSessionId`。executor
  只对 `resumable` 能力**真机实测过**的 profile 转发它(三家 CLI 均于
  2026-08-05 实测通过,含杀进程后恢复)。无效 id 显式失败,没有 CLI 会静默
  新建会话。

---

## 二、结果回传(Runner → RepoMesh)

**没有 HTTP 响应体这回事。** Runner 通过**有序的 RunnerEvent** POST 到事件
端点来汇报,每条带 HTTP 头 `Idempotency-Key: {idempotencyKey}:event:{sequence}`。
`eventId` 是从这个键派生的 UUIDv5——同一事件重发时 id 不变,接收端拿哪个去重
都行。

### 信封 vs 信纸(envelope vs payload)

打比方:每个事件是一封信。

**信封(顶层字段)= 不拆信就要用的信息**:寄给谁(`taskId`/`runId`/
`correlationId`)、第几封(`sequence`)、什么时候寄(`occurredAt`)、什么类型
(`eventType`)、当前会话号(`nativeSessionId`)。每封信的信封格式完全一样。

**信纸(`payload`)= 随信类型变的内容**:接单信一句话,完工信厚厚一沓,报错
信写的是错误详情。

`nativeSessionId` 写在**信封**上而不是信纸里,因为它对每种信都有意义:driver
在 CLI 刚报出会话号的那一刻就通过中间事件把它播出去(事件时刻,不等 turn
结束)——进程中途崩掉时,号已经在平台手里,而那正是最需要 resume 的时刻。

### 示例一:接单事件(每次执行的第一封信)

```json
{
  "schemaVersion": "runtime.v1",
  "eventId": "87e43613-07cb-5a0a-b59d-7bf54550bf8f",
  "eventType": "runner.accepted",
  "organizationId": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "projectId": "3f1a8b22-91d4-4c3e-8a01-5b7e2d9c4f10",
  "taskId": "a1b2c3d4-5e6f-4a8b-9c0d-1e2f3a4b5c6d",
  "runId": "9f8e7d6c-5b4a-4392-8180-7f6e5d4c3b2a",
  "correlationId": "0e1d2c3b-4a59-4687-9584-3a2b1c0d9e8f",
  "attempt": 1,
  "sequence": 1,
  "occurredAt": "2026-08-05T12:30:46.101000+00:00",
  "nativeSessionId": null,
  "payload": { "adapterId": "claude-code" }
}
```

### 示例二:终态事件,结构化结果的真身(这是"测试失败"的真实输出)

```json
{
  "schemaVersion": "runtime.v1",
  "eventId": "35edf779-4a3e-5d4c-b982-00a018f82975",
  "eventType": "runner.failed",
  "taskId": "a1b2c3d4-5e6f-4a8b-9c0d-1e2f3a4b5c6d",
  "runId": "9f8e7d6c-5b4a-4392-8180-7f6e5d4c3b2a",
  "attempt": 2,
  "sequence": 2,
  "occurredAt": "2026-08-05T12:34:59.622000+00:00",
  "nativeSessionId": "019fd1bb-ff7e-7750-b6a7-8eaa46cb755b",
  "payload": {
    "status": "failed",
    "summary": "test_command_failed: pytest tests/pricing (exit code 1)",
    "changedFiles": ["src/pricing/api.py"],
    "testResults": [
      { "command": "pytest tests/pricing",   "exitCode": 1 },
      { "command": "ruff check src/pricing", "exitCode": 0 }
    ],
    "artifacts": [],
    "testCommand": null
  }
}
```

终态事件类型与 `payload.status` 一一对应:
`runner.completed`→`succeeded`、`runner.failed`→`failed`、
`runner.interrupted`→`interrupted`、`runner.input_required`→`input_required`。
其余已定义的类型(`runner.session_started`、`runner.progress`、
`runner.test_completed`)是给未来进度流预留的;当前 engine 只发接单 + 一条终态。

### 信纸背后的进程内形状:RunnerExecutionResult

executor 返回它(Python,`src/repomesh_runner/contracts.py`),engine 把它
翻译进终态 payload:

| 字段 | payload 键 | 说明 |
| --- | --- | --- |
| `status` | `status` | `succeeded` / `failed` / `interrupted` / `input_required` |
| `summary` | `summary` | 验证命令失败时在此点名 |
| `native_session_id` | 信封 `nativeSessionId` | **唯一被提到信封上的字段** |
| `changed_files` | `changedFiles` | workspace 内 `git status` 采集;带 toplevel 防护,父仓库的脏文件永不冒充本次证据 |
| `test_results` | `testResults` | 逐条 `testCommands` 的 `{command, exitCode}` |
| `artifacts` | `artifacts` | 契约预留位(`{kind, uri, contentHash}`),当前无人填充 |
| `test_command` | `testCommand` | 旧字段,已被 `testResults` 取代,恒为 null |

### 结果语义(每条都有实测依据)

- **验证失败 = 整体失败**。agent 干完了、哪怕 `ruff` 也过了,只要任何一条
  `testCommands` 退出码非零,整体 status 就是 `failed`,summary 点名第一条
  失败的命令。但**证据一条不删**:所有命令照跑、全部退出码和改动文件照报。
  不完整的产出永远不会被包装成成功。
- **`interrupted` 也带 `changedFiles`**——被打断的 agent 可能已经改了一半
  文件,不报告就等于把 workspace 变成黑箱。
- **失败时 `nativeSessionId: null` 是信息,不是缺失**。它明确表示"会话从未
  打开,拿这个号去 resume 必然再失败"。反之,失败但**带号** = 会话还活着,
  可以续。这条规则背后是实测踩到的坑:某家 CLI 在 resume 一个不存在的 id 时
  会把该 id 在错误帧里**回显**——所以只有 init 帧确认过的会话号才被采信,
  只被回显、从未打开的号直接丢弃。
- 会话号是厂商不透明字符串(三家格式互不相同),原样保管、原样递回,但复用前
  过一遍消毒:超长、以 `-` 开头(会被当成命令行 flag)、含控制字符的一律丢弃
  ——因为它之后要被拼进 resume 命令行。
