# Runner 接入 RepoMesh 修改清单

## 目标

Runner 只负责可靠执行 Coding Agent：

```text
RunnerTask -> CLI Protocol Driver -> Coding Agent -> RunnerExecutionResult
```

RepoMesh 负责 Agent 身份、Task 分配、Spec、权限、Worktree、跨仓库编排、PR 和 Merge。

## 合并前阻断项

### 1. 修复测试收集失败

当前 `stream_json.py` 的 `CliProfile` 仅在 `TYPE_CHECKING` 中导入，但运行时注解会引用它，导致 4 个 Pytest 收集错误：

```text
NameError: name 'CliProfile' is not defined
```

可增加：

```python
from __future__ import annotations
```

合并门槛：

```powershell
ruff check .
pytest -q
```

两项必须完整通过。

### 2. 接受外部准备好的 Workspace

RepoMesh 会创建隔离 Worktree。Runner 不应只创建空的 `{workspace_root}/{run_id}`，也不负责 Clone 或选择仓库。

Runner 需要：

- 接收 `workspace_id`、绝对路径、`repository_id` 和 `base_sha`。
- 验证 Workspace 位于配置的根目录内，禁止路径逃逸。
- 在指定 Workspace 启动 Coding Agent。
- 不允许 Coding Agent 访问其他仓库工作区。

### 3. 保持 RepoMesh 拒绝规则为硬边界

`BYPASS_PERMISSIONS` 不能突破 RepoMesh 下发的显式拒绝规则。

权限优先级：

```text
denied_paths
> disallowed_tools
> allowed_paths
> allowed_tools
> provider permission mode
```

要求：

- `disallowed_tools` 在所有模式下始终拒绝。
- 增加 `allowed_paths` 和 `denied_paths`。
- Provider 的 bypass 只能跳过 Provider 自身确认，不能扩大 RepoMesh Grant。
- 在文件系统和网络硬隔离完成前，Worker 禁止使用无条件 bypass。

### 4. 验证 Context 引用

`RunnerTask.context_bundle` 当前只传递但未消费。

Runner 至少需要：

- 校验 `bundle_id`、版本和 `content_hash`。
- 接收 RepoMesh 已裁剪的 Coding Agent Package，不读取完整 Spec 数据库。
- 不自行决定 Agent 可以看到哪些 Spec。

RepoMesh 暂时不会要求 Runner 把 Spec 写入 Worktree；具体注入方式将在适配器契约确认后决定。

### 5. 返回结构化结果

`RunnerExecutionResult` 建议至少包含：

```json
{
  "status": "succeeded",
  "summary": "完成任务",
  "changed_files": ["src/pricing/api.py"],
  "test_results": [
    {"command": "pytest tests/pricing", "exit_code": 0}
  ],
  "artifacts": [
    {"kind": "git-diff", "uri": "...", "content_hash": "sha256:..."}
  ],
  "native_session_id": "..."
}
```

失败结果不得将不完整输出包装成成功结果。

### 6. 完成真实 Session Resume

当前 Claude、Codex、Kimi Profile 均为 `resumable=False`。

至少需要对首批支持的 CLI 验证：

- 首轮执行返回真实 Session ID。
- 进程失败后使用该 ID 恢复。
- 恢复后继续原任务，而不是创建新会话。
- 无效 Session ID 明确失败，不静默新建会话。

## 建议 RunnerTask 契约

```json
{
  "organization_id": "...",
  "project_id": "...",
  "repository_id": "...",
  "task_id": "...",
  "run_id": "...",
  "worker_agent_id": "...",
  "adapter_id": "claude-code",
  "workspace": {
    "workspace_id": "...",
    "path": "...",
    "base_sha": "..."
  },
  "context": {
    "bundle_id": "...",
    "content_hash": "sha256:...",
    "coding_package_hash": "sha256:..."
  },
  "permissions": {
    "allowed_tools": ["read", "edit", "test"],
    "disallowed_tools": ["git-push"],
    "allowed_paths": ["src/pricing/**", "tests/pricing/**"],
    "denied_paths": [".github/**"],
    "network_targets": []
  },
  "instruction": "完成当前 Task Spec",
  "test_commands": ["pytest tests/pricing"],
  "resume_session_id": null
}
```

字段名称可以协商，但绑定关系和权限语义不能省略。

## RepoMesh 侧负责

以下内容不需要 Runner 实现：

- Worker、Repository Leader、Organization Leader 身份管理。
- 验证 Worker 是否为 Task Assignee。
- 生成最小 `CodingAgentPackage`。
- 生成 Context Grant 和路径权限。
- 创建仓库 Worktree 并确定 Base SHA。
- 选择 Coding Agent，决定重试、恢复或切换 Agent。
- Review Patch 和测试结果。
- Push、PR、Merge、回滚和跨仓库执行顺序。

## 首批适配范围

当前 Runner 已覆盖 Claude Code、Codex、Kimi。RepoMesh 第一版还需要确认 Trae 和 Cursor 是否有稳定的 Headless 协议。

每个 CLI 必须分别声明并实测：

```text
launchable / observable / resumable
```

未真实验证的能力不能标记为支持。

## 验收标准

- Ruff 和完整 Pytest 通过。
- Claude、Codex 至少完成一次真实 CLI 冒烟测试。
- 显式拒绝规则在所有 Provider 模式下都无法绕过。
- Runner 只在指定 Workspace 内执行。
- Context/Package Hash 不一致时拒绝执行。
- 成功结果包含改动文件和测试证据。
- 至少一个 CLI 完成真实失败后的 Session Resume。
