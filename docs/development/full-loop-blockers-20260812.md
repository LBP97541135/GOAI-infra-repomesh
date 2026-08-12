# 全流程终态验收 · 停工问题记录（2026-08-12，用户停工令快照）

- 记录人：主脑（编排会话）
- 分支：`feat/console-v2`，头 = `137dbed`（未推送；origin 停在 `96afeb0`）
- 配套文档：验收报告 `full-loop-acceptance-report-20260812.md`（验收检测2 维护，
  A-1 关闭、A-2/A-3/A-4/C 类入册，§7 结论「不可推送，待修复复走终判」）；
  施工收官记录见 `full-loop-plan-20260812.md` 文首。

## 1. 一句话现状

全部批次（0/S/A/B/C/D/E）施工收官且逐批验收合并；终态实机验收把「从 GUI 亲手
物化的轮次」这条主路径走通到物化为止，在执行面连续剥出六层问题——四层已修
（含两层当场活体复验），**两层修复被停工令中断**，执行面（真建团/派工/交付）
尚未打通，B4/B5-saga 复走未做。

## 2. 已修复并合并的（本轮验收期间）

| 问题 | 修复 | 状态 |
| --- | --- | --- |
| A-1 跨组织物化 500（应 409） | `ba7e827` 异常上提 contracts + 端点译 409 | **关闭**（验收 GUI 409 原文截图；e6b251db 三次历史 500 同族归因） |
| 环境：delivery_auto 开启但缺 REQUIRED_CHECKS → 请求期 500 | env 补 `REPOMESH_DELIVERY_REQUIRED_CHECKS=["tests"]`（夹具仓真实 check 名） | 关闭（curl 复验）；**衍生 backlog：组合根配置错误应启动期校验** |
| A-4 刚物化轮次白屏（`dayLabel` null `.match`、无错误边界整树卸载） | `b08d6da`：契约按实测标可空 + 兜底「—」+ 逐字段说缺失 + 详情页八区块 ErrorBoundary | **关闭**（夹具黑屏反证 + 5c1b3567 活体页面恢复 4106 字符 0 降级块）。文案裁决：读模型无「刚物化 vs 中断」区分字段，单句「本轮尚无快照」+双来路 tooltip，不编造区分 |
| A-3 半执行不可收敛 + 房间未就绪 500 | `137dbed`（c666ab1 译 503 + 7659c89 materialize 可重入重放：同/新键都能补完半执行；§8.3 收据只护收据不护轮次行的缺口由重放语义补） | 代码已合并；**活体收敛复验未做**（停工） |

## 3. 未修复的问题（按阻断程度排序）

### P0 · 阻断终态验收

1. **第六层：GUI 路径从不向 AgentTeams 投影运行时**（本轮最深发现，诊断代理证据级）。
   `RegisterNativeAgent` + `ReconcileProjectAgentTopology`（唯一注册 agent 进控制器、
   唯一写 `room_id/leader_room_id/runtime_status` 的代码）只接在
   `scripts/run_pipeline.py`，`src/` 零生产调用点（`bootstrap/container.py:175
   native_agent_registration()` 已定义无人调用）。控制器实测无 rm-team-b/c 系
   4 agent 2 team；`room_id` 恒 NULL → 派工 `_route` 必失败。
   **主脑已裁决**：materialize 在 start_plan 之前同步做运行时投影（注册+reconcile，
   幂等，失败→503 不半执行）。**修复代理（feat/runtime-provision）刚启动即被
   停工令终止，零产出。**
2. **A-5：`link_execution_plan` 活体 Postgres 路径不持久化**。成功物化的
   35e66beb 快照 `execution_plan_id` 仍 NULL（SQL 实证）；测试全绿=测试 store 与
   Postgres 实现分叉（方法论五形态第五条活体案例）。后果：rounds[] 投影
   plan_version/时间戳 null（A-4 的数据源头）+ **防二次物化 409 失守**。
   **修复代理（feat/link-persist-fix）停在写测试中途，worktree
   `agent-a07a819495c10a396` 留有半成品，未提交完整。**
3. **宿主 8100 → AgentTeams 控制面连通性**：controller 容器 8090/6167 未发布宿主
   端口 + 系统 HTTP_PROXY 劫持容器 DNS 名请求。sidecar
   `repomesh-agentteams-forwarder`（socat，127.0.0.1:8090/6167）**已建好在跑、
   实测 200**；**8100 的 env 增量未应用**（需
   `REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://127.0.0.1:8090`、
   `REPOMESH_AGENTTEAMS_MATRIX_URL=http://127.0.0.1:6167` 后重启）。
4. **worker 派工文件通道在 Windows 宿主必踩**：`REPOMESH_AGENTTEAMS_STORAGE_ROOT`
   指向宿主不可见的 docker 卷；应切 `AgentTeamsObjectTaskPublisher`（MinIO 在
   控制器 9000，sidecar 需加转发 + 对象存储 env）。**未做**（原并入 ⑱，已停）。

### P1 · 不阻断但已定性

5. supplemented_repos 双串（LLM 括号注记漏进仓名 + 服务端未归一去重，C 类，
   服务端数据实证）。
6. 控制器经 docker.sock 轮流重启全部历史 worker 容器；08-07 批容器 baked 的
   AGENTTEAMS_AUTH_TOKEN 在 08-08 token 轮换后失效、起来即 401 自杀——持续
   污染 docker ps 的噪声（清理需重建容器，未动）。
7. 「重试物化」GUI 入口不存在，且前置依赖「本轮卡住了」的判定字段（读模型
   现无此字段，A-4 修复的 tooltip 同源缺口）。
8. `DeliveryPlanView.plan_version` 契约 `number` 实测 null（暂无展示消费方）。
9. dev-up 收养库守卫洞：8100 停跑 + 既有 compose postgres 在跑时，脚本会把
   先于它存在的库误判为自己的并迁移（本机=活体 5432，危险分支；验收已入册）。
10. 隐式组织 UX（「全部工作区」下建 issue 落入非预期组织，C 类）。

### P2 · 既有 backlog（本轮之前即存在）

recovery-plans 裸端点无守卫；url-type GitLab 嵌套 group 判单仓；契约 tasks[]
转写核对；「回滚即 ChangeSet」设计轮；`graph_edges` 恒空（v0.2 §5.5 老问题）；
缺失依赖是否阻塞审批的产品语义；catalog 与远端仓存在性对账（api/client 远端
不存在）；nginx console-web 单独 restart 脆弱性；返工候选唯一性（用户独立
会话在修，task_9aab9a1f，状态未知）。

## 4. 环境快照（停工时点）

- 8100：主脑后台进程 `bxxrvlcrf`，跑 **d41d35d 代码**（e5b77de…137dbed 中
  b08d6da/137dbed 未装载），env=5533 DSN + action token + gh 钥匙串 token
  （静态 SCM 适配器）+ delivery_auto + REQUIRED_CHECKS=["tests"]；**AgentTeams
  URL 仍是打不通的容器 DNS 名**（Matrix sync 噪声持续）。
- 5533：迁移头 0023；**外科改动**：checkout/billing 两行 catalog URL 已指真仓
  （api/client 仍 github.example 占位）。
- sidecar `repomesh-agentteams-forwarder` 在跑（本轮新建，可 `docker rm -f`）。
- 5280 vite 正常（A-4 修复已 HMR 生效）；5432/8000 活体未动。
- GitHub 侧：真实夹具仓 = checkout / billing / pricing-core；凭据 = gh 钥匙串
  catbobyman token（repo scope，经 env 注入 8100，未落盘）。
- 验收标本（保全勿清理）：5c1b3567（半执行）、35e66beb（成功物化未投影运行时）、
  de2973ab（clarify 强行继续）、a2c0c2f9（跨组织 409 样本）、e6b251db（历史
  空文本）、种子 B 轮的回滚决策+恢复计划。种子复位清单见编排记忆。

## 5. 恢复剧本（用户放行后）

1. 重启/续派两路被停代理：⑰ A-5（worktree 半成品可续或重派）→ ⑱ 运行时投影
   （从零，裁决与 brief 已在编排记忆）；
2. 逐路验收合并 → 8100 换 env（AgentTeams 两行 + 对象存储若 ⑱ 需要）重启；
3. 发验收「B4 观测开始」：35e66beb **重放补完**（不会自收敛——其团队规格从未
   到达控制器）→ B4 全链（建团/双房间/派工/runner/真仓候选分支/PR/CI `tests`/
   merge gate）→ 真 change set 的 GUI 回滚 saga 活体证据 → 5c1b3567 重放 →
   报告终判；
4. 终判通过后：全量回归（须用户点头）→ 推送 → 种子复位（等用户令）。
