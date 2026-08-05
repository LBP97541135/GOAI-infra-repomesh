---
name: worker-dispatch
description: Assign ready repository tasks to eligible workers under policy constraints.
---
# Worker 调度

只派发依赖已满足的 Task，并绑定 Worker、仓库、允许路径、Workspace 和上下文对象。并行任务必须无写入冲突；Worker 之间不得私下协调。

