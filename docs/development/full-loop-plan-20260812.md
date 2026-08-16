# 全流程闭环立项清单（prompt → merge → 可回滚）

**日期**：2026-08-12｜**基线**：`feat/console-v2` 头 = `96afeb0`（已推 GitHub）
**背景**：控制台 v2 施工收官后，多会话团队开发模式已终止。本清单是下一阶段的
总计划——把「发 prompt → 仓库发现 → 计划 DAG/spec → agent 执行 → CI/联调门禁 →
merge → 可回滚」整条产线从命令行搬进图形界面，并补齐 merge 后回滚。

**终态定义**：外部用户从 GUI 走完全链，任何一步可通过 GUI 回滚；不能通过 GUI
操作的行为闭环视为缺陷（用户既定验收标准）。

---

## ✅ 施工收官记录（2026-08-12，新主脑编排的多代理并行施工）

**全部批次已完成并逐批验收合并**，头 = `9290061`（未推送）。终态实机验收由
「验收检测2」会话执行中（报告将落
`docs/development/full-loop-acceptance-report-20260812.md`）。

| 批次 | 合并节点 | 内容摘要 |
| --- | --- | --- |
| 0-1 M-12 | `d24fa14` | 降级计数汇总合入 |
| 0-2 M-7 | `e5b77de` | TaskOrigin 枚举 + TaskEvidenceView（迁移 0022） |
| S 一键启动 | `91c0cbe` | dev-up/down 双语 + compose console profile + README 置顶 |
| A 仓库池 | `d72f15e`+`172064d` | scan-repo/url-type/console 白名单/202 轮询 + 添加仓库卡片 |
| B 发现链 | `0ae4506`+`59fe63a` | 服务端发现四步（契约 v0.4，迁移 0023）+ 发现面板/分档审批 |
| C 物化+DAG | `08aeba3`+`df56a05`+`ac8686b` | materialize 薄端点（ensure topology）+ 物化弹窗 + DAG 泳道渲染/着色 |
| D 回滚实装 | `bcf5667`+`45177ec` | GitHub revert 网关 + saga 接线 + RecoveryWaiting/CI 门禁 + 返工接线/plan 重开 |
| E GUI 回滚 | `9290061` | 原子 rollback 端点 + 范围表对话框（契约 v0.1 §4.6） |

顺带修复的既有根因级缺陷：materialize 从未持久化快照（`dict(item)` 对 frozen
dataclass 恒 TypeError 被吞）；`CreateRepositoryAgentTeam` 零生产调用方（控制台
issue 第一轮必撞 topology not found）；发现审批送错指纹字段（必 409）。
遗留待办见编排记录；「回滚即 ChangeSet 递归」另立设计轮。

**以下为立项时的盘点与批次定义，作历史计划保留**（其中 §0「没通的」已全部解决
或勘正——D-4 两条缺口经核实不存在，见批次 D 勘正注记）。

---

## 0. 当前基线盘点（立项时事实，2026-08-12 已全部过时——见上方收官记录）

**已通的**（真 GitHub 三仓活体验证过）：
- 管线五步 API 全在且有鉴权：`/requirement-analysis` → `/discovery` →
  `/confirmation` → `/integration` → `/bridge/materialize`（驱动者=
  `scripts/run_pipeline.py`，GUI 触发不了——这就是要搬的东西）；
- 仓库入库两档入口：单仓 URL 注册 + `POST /repositories/scan-org` 组织批扫
  （远程拉文件树/依赖/提交，建 AutoCard 入目录；SSRF 白名单、502 不回显已加固）；
- 执行面：plan loop 批次推进、runner 领任务、CLI runtime 驱动编码 agent
  （`integrations/coding_agents` 23 适配器）、AgentTeams 团队+双房间；
- 交付面：候选分支真实推 origin、每仓 CI 门禁、返工闭环、跨仓联调门禁、
  **merge 执行器已实装**（`integrations/scm/delivery.py` merge_when_allowed/
  merge_ready_repositories/reconcile_and_merge + `github.py:230` 真 merge）、
  merge 前回滚（withhold/MirrorGitReverter）；
- 控制台读模型全套：issue 列表/详情、轮次、决策夹审批（真批亲证 merge gate
  false→true）、房间流、事件时间线、仓库/团队/花名册；quickstart 已入库。

**没通的**：
- GUI 无仓库添加/扫描入口；GUI 建 issue 只落草稿快照，管线五步一步触发不了；
- **merge 后回滚是纯骨架**：`integrations/scm/recovery.py` 有协议与 saga
  （关 PR→开 revert PR→merge revert→复验，冲突自动开处理任务），但
  **无 GitHub 侧实现、组合根零接线**；
- 交付尾巴三缺口（追踪报告 §4）：返工换 task_id 致分支/PR 分叉（应按 plan_id
  命名候选分支）、FAILED plan 无法重开、joint dispatch 不校验候选已发布；
- M-7 契约两缺口未裁未做（提案已实施并入契约，原提案文件已删——语义见
  `task_orchestration/contracts.py` 的 TaskOrigin/TaskEvidenceView docstring）；
- 遗留一笔未合并：M-12 降级计数器（分支 `fix/runtime-degradation-visibility`
  的 `029e3e1`；test_grid.py 尾部 add/add 冲突，两段测试互不相干按序拼接即可）。

---

## 1. 批次清单（按执行顺序）

### 批次 0：收尾（半天）
| 项 | 内容 | 验收判据 |
| --- | --- | --- |
| 0-1 | 合并 M-12 `029e3e1`（解尾部拼接冲突） | tests/api 83 passed；8100 重启后降级日志行可见 |
| 0-2 | 裁决并实施 M-7 两缺口（提案已留档，建议顺序：缺口 2 `origin` 枚举 → 缺口 1 `evidence` 视图；接受「投影期解析零迁移」折中） | 删除读模型 `json.loads` 与 `REWORK_TASK_TITLE`；反证修复前必红且红因正确 |

### 批次 S：开箱即用启动器（小，可与任何批次并行）

目标：**一条命令配好环境并打开前后端界面**。以 quickstart（9b123d3，命令全部实核过）
四步为蓝本做成自动化，两档交付：

| 项 | 内容 |
| --- | --- |
| S-1 | **一键开发脚本** `scripts/dev-up.ps1` + `dev-up.sh`：起 compose postgres → 等库就绪 → `uv sync` + `alembic upgrade head` → 起 uvicorn 8100（带 token env）→ `npm install` + 起 vite 5280 → 自动开浏览器到登录页。幂等可重入：已在跑的组件跳过不重启；`dev-down` 对称收摊 |
| S-2 | **全栈 compose**（真开箱）：`docker compose --profile console up` 一条命令拉起 postgres + 8100 后端（容器内自动迁移）+ 前端（构建产物由后端/nginx 同源托管，免代理与 CORS）。宿主机只需 Docker |
| S-3 | 可选演示数据开关：`--seed` 参数调 seed 脚本灌 Saleor 演示种子，让首屏不是空态 |
| S-4 | 就绪自检与失败可读：每步失败给出人类可读指引（端口被占/Docker 未启动/迁移失败各自说人话）；结尾打印「登录页 URL + 首次 bootstrap 提示」。**注意 /health/ready 最小配置返回 503（已知语义问题），就绪判据用 8100 根路由或 bootstrap 页可达，不用 readiness** |
| S-5 | README/quickstart 顶部加「一键启动」一节，四步手工路径降级为「脚本背后做了什么」的解释文档 |

**验收**：全新 clone 的机器（仅有 Docker+uv+node / 或仅 Docker 走 S-2），一条命令后
浏览器自动打开、bootstrap 建号、进入控制台，全程零手工配置。

### 批次 A：仓库池 GUI 入口（小，1-2 天）

**入口形态=贴 URL（已核代码）**：组织 URL 批扫端点现成（`POST /repositories/scan-org`）；
**单仓 URL** 的扫描能力在应用层已有（`scan_remote.py:143` 能从单仓 URL 推断 entry repo，
`scan_remote()` 支持单仓），但 **router 没暴露单仓 URL 端点**——现有 `POST /repositories`
是手工填全字段的注册，不是 URL 扫描。A-0 补一层薄封装即可。

| 项 | 内容 |
| --- | --- |
| A-0 | 后端薄封装：单仓 URL 扫描端点（复用 scan_remote 的单仓路径 + 同白名单），与 scan-org 并列；**顺带出 URL 类型识别端点**（组织/单仓判定的单一事实源，前端徽标防抖调用） |
| A-1 | console 命名空间包会话鉴权的转发端点（同 `POST /issues` 模式）：`POST /console/repositories/scan-org` 与单仓扫描同理——这是「双鉴权统一」的最小分期 |
| A-2 | 长任务模型：202 + 任务状态轮询端点（SSE 留 backlog） |
| A-3 | 仓库页「扫描组织 / 添加仓库」按钮 + 弹窗（URL 输入；**token 不进 GUI**，只支持服务端 env 配置——已定） |
| A-4 | 结果展示：注册 N / 跳过 N / 失败 N，扫描中的进行态 |

**验收**：GUI 填一个组织 URL → 仓库页长出新仓库卡，全程无 curl。

### 批次 B：issue → 发现/确认（中，带人工审批点）
| 项 | 内容 |
| --- | --- |
| B-1 | 建 issue 后详情页出「发现」面板：触发 Step 0 需求分析（sufficient/追问问题=clarify 回路回归）→ Step 1 候选评分列表（score+rationale）→ Step 2 三档分类 |
| B-2 | 分档结果进决策夹审批：organization leader 调整 required/maybe/excluded 后放行（复用现有审批交互与幂等键模式） |
| B-3 | 契约面：以上各步的读投影 + 写触发进 delivery-read-model 契约新章节（零新实体原则延续：发现结果挂 issue 的快照版本） |

**开放决策（开工前拍板）**：审批是必经点还是可配置跳过（全自动模式）。
建议：v1 必经，全自动进 backlog。

### 批次 C：集成、物化与执行触发 + 图形化 DAG（大，原 #22 并入）
| 项 | 内容 |
| --- | --- |
| C-1 | 审批放行 → Step 3 集成：生成 spec + contracts + task_dag + execution_batches，落 issue 新快照版本 |
| C-2 | **图形化 DAG 渲染**（原台账 #22）：节点连线图渲染 task_dag+batches。前置：先读 `ba38004` 校准边语义（M-9 同名解析 None/M-10 节点边同口径——前端顾问已标记的过期前提） |
| C-3 | 「物化并开工」按钮 → `bridge/materialize`（拓扑保障沿用 CONS-33 双房间模式）→ plan loop 自动转起 |
| C-4 | 执行进度展示 = 现有读模型（轮次/房间/事件/决策），仅补 DAG 上的实时状态着色 |

**验收**：GUI 从 prompt 到 plan COMPLETED 全程可走可看。

### 批次 D：merge 后回滚实装（中大，设计已拍板勿重开）
| 项 | 内容 |
| --- | --- |
| D-1 | `RevertDeliveryGateway` 的 GitHub 实现：close PR / create revert PR / merge revert PR / revalidate（真仓活体验证） |
| D-2 | `RecoverySagaExecutor` + `GovernedRecoveryActionHandler` 接进组合根；冲突→自动开冲突处理任务 |
| D-3 | 三阶段语义照 v2 设计稿：merge 前 withhold（已有）→ 部分 merge 逆序 revert → 交付后「回滚即 ChangeSet」（rollback_of 递归走产线重过门禁） |
| D-4 | 交付尾巴三缺口一并修：候选分支按 plan_id 命名、FAILED plan 可重开、joint dispatch 校验候选已发布 |

**D-4 勘正（2026-08-12 施工核实）**：三缺口中两条**不存在**——候选分支自 `2b66ad6`
起即按 plan 键命名（`repomesh/{plan.id[:8]}/{repo[:8]}`，task_id 从不参与）；
`joint dispatch` 在 src/ 全仓零命中（只存在于文档散文）。仅「FAILED plan 重开」为真
缺口，已修（13b4d0e）。**新发现的真尾巴**：返工成功后同 leader 下两个 SUCCEEDED
worker task 使 `_candidates_for_batch`「唯一成功候选」抛 ValueError——返工修复闭环
因此落不了地；修法=TaskView 增 `created_at` 排序事实 + 取最新 SUCCEEDED（已排队）。

### 批次 E：GUI 回滚入口（小）
| 项 | 内容 |
| --- | --- |
| E-1 | issue/交付视图加带审批的「回滚」操作（复用决策夹 + 幂等键模式），按三阶段展示可回滚范围与后果 |

**终态验收**：构造一次故意的坏交付，从 GUI 发起回滚，真仓 revert PR 合入、
产线重过门禁，全程无命令行。

---

## 2. 顺序与依赖

```
批次0（收尾） → A（仓库池） → B（发现/确认） → C（集成/物化/DAG）
                                              ↘
                                       D（回滚实装）→ E（GUI 回滚）
S（开箱即用启动器）：无依赖，可随时并行；建议早做——之后每个批次的验收都受益
```
- 0-2（M-7）必须在 C/D 前：进度与回滚展示消费 evidence/origin 字段，先把地基打诚实；
- D 可与 B/C 并行（不同代码面）；E 依赖 D；
- 量级参考：A+B+C ≈ 控制台 v2 修复批次总量；D+E ≈ 其三分之一到一半。

## 3. 已定不重开的决策
- 三阶段回滚设计、per-task push=候选发布、merge 才是不可逆边界、ChangeSet
  ready 自动建 PR（v2 设计稿四决策，2026-08-08 拍板）；
- token 不进 GUI；发现审批 v1 必经；零新实体原则延续；
- M-13 auto_card 渲染=全量五字段投影（不做子集）。
- **GUI 设计四裁决（2026-08-12 用户拍板）**：①扫描失败=整体重扫（幂等跳过，
  无单仓重试）；②clarify 可强行继续但决策记录留痕；③DAG v1 只读（编辑进
  backlog，调整走返回分档重新生成）；④回滚只做整 change set（无单仓回滚）。
  **设计定稿**：`full-loop-gui-design-20260812.md`；**原型**：
  `frontend-prototype/full-loop-surfaces.html`（自包含，双击打开）。
  IA 裁决：不加顶层导航，②③④全长在 issue 详情页、①在仓库页。

## 4. Backlog（本计划不含，留档）
双鉴权完整统一（主体化凭据）、SSE 推送、review_validation（⑧⑨）实装、
Leader→Admin 汇报链、M-14/15（awake/uptime 待真实 controller 验证）、
E-1 module-map 承认读模型层、E-8 架构测试、工作区名大小写归一化、
readiness 语义（最小配置 503）、种子复位（清理 6 项验证数据，注意 leader id
将从随机 v4 变为稳定 58fdff94…）、issue 级关闭、服务端筛选、团队拆除。

## 5. 方法论存档（施工时执行）
- 测试可信度五形态：绿≠正确、绿≠覆盖、绿≠无盲区、红≠红对了、测到的≠想测的
  对象；处置=反证必须红在要测的原因上，反证不可用给正对照；
- 排查回归先问「被怀疑的代码在不在这条路径上」（路径排除优先于耗时对照）；
- 类型字段增删以全仓构造点普查为准；改派生语义先读断言在断言什么；
- 前端验证=浏览器实走 + `tsc -b`（--noEmit 是空转桩）+ oxlint 看输出；
- 管道后 `$?` 是 tail 的；定向全过≠CI 全绿，合并前跑全量。
