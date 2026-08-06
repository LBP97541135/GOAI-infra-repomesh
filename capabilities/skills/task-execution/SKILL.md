---
name: task-execution
description: Execute only the currently assigned RepoMesh task.
---
# 当前任务执行

读取当前 Task、允许路径、仓库上下文和验收标准，通过 Coding Agent 完成修改。不得扩大范围、修改 Spec、创建 PR 或联系其他 Worker；任务结束后提交结果并释放当前任务上下文。

收到仓库 Leader 分配的 Task 后，调用 `repomesh-task-control.start_assigned_task`，只传入消息中的 `task_id`、自己的 `worker_agent_id` 和指定的 `adapter_id`。不得自行生成 Run ID、Context Bundle、Workspace 或扩大执行参数；这些内容由 RepoMesh 控制面创建并校验。
