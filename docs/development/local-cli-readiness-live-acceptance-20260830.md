# 本地 CLI 一键拉起与开工门禁 —— R6 活体验收判定（2026-08-30）

> 被验对象：`codex/local-cli-readiness` 分支（7 提交，头 `2be613a0`，基 `db55bf29`）。
> 需求：`local-cli-launch-readiness-requirement-20260829.md`；施工：同日 construction spec v2。
> 证据目录：gitignored `output/local-cli-r6-evidence/`（curl 回执、readiness 快照、日志摘录）。
> 结论先行：**八项 AC 全部通过；两项挂起裁决（I-1/I-2）均已取得活体裁决材料；另录得 2 项新发现与 2 项观察项，均不阻断合并。**

## 1. 环境与方法

- 平台栈复用既有 AgentTeams controller（含 conduit/Matrix 18080、controller 转发 18090）；
  E1 六成员的 controller 侧 Team/资源与 enrollment 均复用波次 3 之后留存的开通产物，
  重取 controller token，实测既有 Matrix token 全部有效。
- RepoMesh 侧全新：一次性 postgres（15549，`--rm`）、alembic 迁移至链尾、
  seed 管理员 + 三个夹具仓（repomesh-e2e-pricing-core / billing / checkout）+
  组织 leader + 六个 external principal。R8 只读对账 **84 过 0 挂**
  （六成员 `containerManaged:false`、team 铸名、matrix id、skills 全符）。
- 后端（8077）、前端（5281）、Local Launcher（8121）全部跑本分支代码。
- 六个 Bridge 全程由 Launcher/页面拉起，**未手动起过任何 Bridge**。
- 发现链（issue → 分析 → 候选 → 分类 → 审批 → 计划 → 物化）全程走控制台 UI；
  curl 仅用于验证被拒路径与读端取证，未替代任何业务步骤。

## 2. AC 对照

| AC | 判定 | 关键证据 |
|---|---|---|
| AC-01 一键启动/幂等 | **PASS** | modal「启动并重新检查」一键拉起六桥：~10s 4/6、~40s 6/6 全绿；二次点击（页面主按钮）后六 PID 完全不变（`ac01-pids-*.txt`），进程数不变 |
| AC-02 全停 409 | **PASS** | 六桥全停时物化 modal 预检点名六人且无提交入口；服务端 `POST .../discovery/materialize` 硬 409 `external_members_not_ready`，message="6 local CLI members are not ready"，members 六人（3 leader + 3 worker，`offline / no readiness report`）；任务 0、消息 0、拓扑 3 团队已建且 `decomposition_mode=leader`（`ac02-materialize-409.json`） |
| AC-03 5/6 拒 + 免刷新重试 | **PASS** | 杀 gamma-worker 后同幂等键重试 → 409 只点名 c2（reason 变为 "the member stopped reporting readiness"）；页面行内「重启」单拉该成员（其余五 PID 不动，`-Only` 缝实证）；重开 modal「仅重新检查」即全绿，未重跑任何发现步骤，随后物化成功（`ac03-materialize-5of6-409.json`） |
| AC-04 角色能力 | **PASS**（单测为主 + 活体旁证） | readiness 快照：3 leader 仅 `leaderLane:true`，3 worker 仅 `governedLane:true`，全 ready（`ac04-readiness-lanes-full.json`）；六人过门即含 workspaceRoot 等值校验 |
| AC-05 租约失效/围栏 | **PASS** | 硬杀（无 goodbye）后：launcher 立即报未运行；租约到期读端推导 `stale`→`offline`，页面「未运行 + 租约过期」；伪 instanceId 晚到 renew → 409 `stale_instance` 且真租约不受扰（`ac05-*.json/.txt`）；非 UUID instanceId 被 schema 422 更早拦下 |
| AC-06 失败诚实展示 | **PASS（按 I-1 裁决口径，见 §3.1）** | 藏 enrollment 后重启失败：页面显示「未运行 · 无 PID 文件 + 租约过期」，不谎报就绪，无 stderr/凭据泄漏；失败"阶段"不在页面（见 I-1 与 F-2） |
| AC-07 降级可恢复 | **PASS** | launcher 未起时页面给出双假设说明（没在跑 / Origin 不在白名单）+ 三条可复制命令 + 启动前置清单；起 launcher 后自动恢复；写路由缺自定义头 403、坏 Origin 403、状态响应无 ACAO 无秘密字样 |
| AC-08 端到端顺序 | **PASS** | 六桥先 ready 才物化成功；三批依赖序自动滚动：leader 收通知 → codex 出计划 → worker 子任务自动接单执行（gamma 批含一次 worker 失败 → leader 返工重派 → 最终成功）；三个 leader 任务全 succeeded，任务终态 8 succeeded + 1 failed（返工留痕）；全程无人工输入 Task UUID |

Bridge 时序（Task 3 断言）另有直接日志证据：重启段日志顺序为
`startup readiness POST → 200` → `bridge ready: ...` → 15s 周期续租心跳。

后端重启自愈：杀 uvicorn 父子对并重启后，T+6s 租约表为空（此刻门 fail-closed），
T+18s 六人全部 ready —— 15s 续租周期的 renew-on-missing 插回如设计兑现。

## 3. 两项挂起裁决的活体材料

### 3.1 I-1：AC-06 的"失败阶段"展示口径

活体证实：launcher 以分离进程拉起成员，启动失败（enrollment 缺失）时页面能给出的
最诚实答案就是「未运行 + 无 PID 文件 + 租约过期 + 日志路径」——阶段信息只在成员日志里。
**建议按施工判断落锤：AC-06 措辞软化为"页面呈现未就绪与日志位置，失败阶段以成员日志为准"；
如需页面级阶段，后补日志尾巴/退出码面（独立小 PR）。**

### 3.2 I-2：门对缺失 `containerManaged` 失开

活体证伪了风险：vendored controller **确实供出** `containerManaged` flag
（六成员 binding 均为 `false`，R8 对账通过），AC-02 的 409 点名了全部六人
（含 3 个 repository leader），组织 leader（Manager 资源）被正确豁免且未漏进名单。
**失开语义在本环境无实害；作为记录在案的设计取舍保留。**

## 4. 新发现（不阻断，建议后续处理）

- **F-1（Minor）**：launcher 写路由同步 shell 到 PowerShell 期间事件循环被阻塞，
  页面 5s 状态轮询失败，顶部出现「连不上本机启动器：Failed to fetch」红条；
  操作结束后轮询恢复，但红条不自清，与"启动器拒绝"标签并列造成误导。
  修法方向：写路由丢线程池 / 前端在下一次 status 成功后清除瞬时失败横幅。
- **F-2（Important，关联 I-1）**：launcher 对 ps1 启动失败（如 enrollment 缺失）
  返回裸 `500 Internal Server Error`（无结构化 body）。未泄露 stderr/凭据（好），
  但页面无法区分"启动器内部错"与"成员起不来"。建议与 I-1 的日志面一并处理：
  把 ps1 非零退出翻译成结构化 4xx/5xx。
- **O-1（观察）**：三批全部完成后 issue 页头仍显示「第 1/3 批执行中」——
  plan-execution 读模型的批次指示滞后，属既有执行面读模型，不在本轮 AC 范围。
- **O-2（观察，供 D-M7-1 关联裁决）**：leader→Manager 汇总（task_report）以服务端
  `@admin` 身份、mention envelope 形式发进该仓 leader DM 房间；`@manager` 并不是
  该房间成员（Manager 经 controller 通道读取）。投递本身 200 + `delivered` + Matrix
  event 可取回；"落进 manager 的房间"的物理语义与 M7 预期的差异记录在此。

## 5. D-M7-1 修复（`cac5d1e2`）活体取证

三个批次各产生一条 leader→Manager `task_report`，全部 `delivered`
（14:53 / 14:58 / 15:12 UTC），带 Matrix event_id 且事件可从 Matrix 取回——
修复前该路径必 500。取证文件：`dm71-*.txt`。

## 6. 终态账面

- 任务：9 条（3 leader + 6 worker 子任务），8 succeeded + 1 failed（返工留痕）；
- 消息：task_assignment 9、decision 8、task_report 3，全部 delivered；
- 房间 timeline 投影：6 个拓扑房间共 50 条；
- 后端无 500；六桥全程续租未断（含后端重启窗口的自动恢复）。

## 7. 环境现状与拆除

验收结束时环境保持活体（六桥、后端 8077、前端 5281、launcher 8121、一次性库 15549）。
拆除顺序（照波次 3 交接 §7.6 步骤 8）：页面「停止全部」或 stop_members.ps1 →
杀 uvicorn 父子对 → `docker rm -f repomesh-r6-pg`。controller 栈与他线容器勿动。
