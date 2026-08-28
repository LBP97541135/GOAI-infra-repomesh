# 合并 main 改动记录 — 2026-08-28

## 1. 概述

| 项目 | 内容 |
|---|---|
| 合并方向 | `origin/main` → `feat/repo-scan-chain-review` |
| 合并提交 | `a237a2f` Merge origin/main into feat/repo-scan-chain-review |
| 合并前状态 | 本地分支 `95fdc40`（315 ahead / 8 behind origin/main） |
| main 分支 HEAD | `f3d343b` fix(dev-orchestration): wire worker task dispatch end-to-end |
| 合并结果 | 317 ahead / 0 behind origin/main，工作区干净 |
| 承载分支 | `feat/repo-scan-chain-merge-main`（基于合并提交 `a237a2f`） |

本次合并将 main 分支（含身份访问网关、worker 任务派发端到端打通等近期演进）合入本地
`feat/repo-scan-chain-review` 分支，并将本地分支的决策链历史、世界图缓存 M1 等演进完整保留。

## 2. 冲突概览

合并共产生 **68 个冲突**。按目录将冲突文件分为五组处理：

| 组 | 文件数 | 处理方式 |
|---|---|---|
| root（根目录配置） | 4 | 逐文件决策 |
| docs（文档） | 6 | 逐文件决策，module-map 手动合并 |
| frontend（前端） | 6 | 逐文件决策 |
| src（后端业务） | 7 | 全部取 ours，随后修复合并引入的回归 |
| tests（测试） | 7 | 全部取 ours |

另有 10 个核心文件冲突块采用人工逐块裁决（双参 API 演进、乐观锁、迁移链等敏感区域），
以及 33 个文件自动判定无冲突标记后直接 `git add`。

## 3. 决策原则

- **ours 优先保留**：本地分支独有的能力演进，包括：
  - 世界层依赖图缓存 M1（`container.py` 的 `repository_profiles()` / `world_graph()` /
    `invalidate_world_graph()`）
  - 图预补充（supplements / conflicts / observations，替代 theirs 的 `supplemented_repos`
    字符串数组）
  - `plan_snapshot_store.set_discovery(expected_version=)` 乐观锁
  - 平台白名单语义演进（已知平台免配置扫描 + `REPOMESH_REPOSITORY_PLATFORMS` 显式声明 +
    `REPOMESH_REPOSITORY_SCAN_ALLOWED_HOSTS` 兜底）
- **theirs 采纳**：main 已发布的既有行为，包括：
  - `identity_access` 模块（PolicyAuthorizationGateway 登录门禁）
  - 文档侧对登录门禁、README 产品描述的更新
  - 前端 vite 配置默认 `:8000` 全执行面 + `REPOMESH_API_TARGET` 覆盖
- **手动合并**：同时涉及两边演进、不能整体取一方的文件（如 `module-map.md`）。

## 4. 分组决策明细

### 4.1 root 组

| 文件 | 决策 | 理由 |
|---|---|---|
| `.env.example` | ours | 保留平台白名单与仓库扫描相关的新增环境变量 |
| `README.md` | theirs | main 对产品描述与登录门禁的更新成立 |
| `compose.yaml` | theirs | 与 main 的运行编排保持一致 |
| `dev-up.sh` | theirs | 与 main 的开发启动脚本保持一致 |

### 4.2 frontend 组

| 文件 | 决策 | 理由 |
|---|---|---|
| API 契约（contract） | ours | 图预补充新字段（supplements/conflicts/observations） |
| `DiscoveryApproval.tsx` | ours | 使用图预补充新字段 |
| `DiscoveryPanel.tsx` | theirs | 与公共代码 `hasView` ref 配套 |
| discovery API 层 | ours | 契约演进配套 |
| `issues.ts` | ours | `open_count: 4` 与 6 条数据中 4 条 `state: "open"` 自洽 |
| `vite.config.ts` | theirs | 默认 `:8000` 全执行面 + `REPOMESH_API_TARGET` 覆盖 |

### 4.3 src 组

后端业务文件全部取 ours，保留本地分支的决策链历史、世界图缓存 M1、图预补充与乐观锁演进。
随后修复了合并引入的两处回归（见第 6 节）。

### 4.4 tests 组

测试文件全部取 ours，与 ours 的 src 契约（双参 `require_single_repo_url(url, fetcher)`、
settings 平台白名单字段等）保持一致。

### 4.5 docs 组

| 文件 | 决策 | 理由 |
|---|---|---|
| `docs/architecture/module-map.md` | 手动合并 | 两边各保留一行（Capability Management + Runtime） |
| `delivery-read-model` 文档 | ours | 契约演进配套 |
| handoff / merge-analysis / 推理轨迹 | theirs | main 侧记录更完整 |
| `gateway-fix-resolution-20260814.md` | theirs | ours 版本泄露真实 API Key，必须打码 |

## 5. 迁移链处理

两边迁移链完全分叉，采用 **main 链为基线** 的方案：

- 恢复 theirs 原版迁移：`20260812_0019` / `20260812_0020` 原版 + observability `0031`–`0034`
- 删除 ours 重铸的 `0028` / `0029` 六个迁移文件
- 重挂 ours 独有迁移（保持版本号递增、不冲突）：
  - `plan_snapshot_discovery_version` → `20260828_0032`
  - `decision_chain_nodes` → `20260828_0033`
- `validate_migrations.py` 验证单链通过

## 6. 合并引入的回归与修复

全量测试首轮出现 **15 个失败**，定位到两处根因并修复：

1. **`settings.py` 丢失字段（10 个失败）**
   `Settings' object has no attribute 'repository_scan_platforms'`
   合并时 settings 被 theirs 版本覆盖，丢失了 ours 的两个字段，已补回：
   ```python
   repository_scan_platforms: str = ""          # host=platform 映射
   repository_scan_include_forks: bool = False
   ```
   其余 ours 字段（`repository_scan_allowed_hosts` 等）保留。

2. **残留单参调用（5 个失败）**
   `_stub_require_single_repo_url() missing 1 required positional argument: 'fetcher'`
   `console.py` 与 `router.py` 中残留 theirs 的单参调用
   `require_single_repo_url(url)`，已删除，保留 ours 双参版本
   `await require_single_repo_url(url, fetcher)`。

## 7. 验证结果

| 检查项 | 结果 |
|---|---|
| `ruff check .` | 通过（全绿） |
| 全量 `pytest` | **1653 passed, 16 skipped** |
| 修复回归的两个测试文件 | 43 passed |

## 8. 遗留处理：GOAI 初赛 PPT 文件

合并后工作区出现 **20 个 D 状态**的 GOAI 初赛 PPT 文件
（`GOAI初赛PPT-*.md` / `*.pptx`）。验证结论：

- 这些文件在本地提交 `95fdc40` 中首次被引入并追踪（父提交 `ccf1cd3` 中为 0，`95fdc40`
  中为 20），属于本地分支的合法内容
- 两个合并父提交中 main 侧无这些文件，删除它们并非 main 引入的变更
- 属于合并冲突处理过程中的工作区副作用，已通过 `git checkout -- 'GOAI初赛PPT-*'`
  从索引恢复，合并结果完整保留这批文件

## 9. 提交与新分支

- 合并提交：`a237a2f` Merge origin/main into feat/repo-scan-chain-review
  - parent1：`95fdc40`（本地分支）
  - parent2：`f3d343b`（origin/main）
- 新分支：`feat/repo-scan-chain-merge-main`（基于 `a237a2f`，用于承载并推送本次合并结果）
- 合并相对 ours 分支：99 个文件变更（+19821 / -16731）
- 本地分支领先 `origin/feat/repo-scan-chain-review` 10 个提交（含本次合并）
