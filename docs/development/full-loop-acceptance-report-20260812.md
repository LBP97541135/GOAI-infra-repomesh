# 全流程闭环终态实机验收报告（2026-08-12）

- 文档状态：**主体定稿 · B4/B5-saga 复走待环境修复**——本报告除 §2-B4/B5s 与 §7 终判外
  均为终稿；复走完成后追加修订
- 验收人：验收检测2（独立验收会话，只验收不修改；本文件是该会话唯一写入物）
- 验收对象：`feat/console-v2`。**开走基线 = 9290061**；验收期间三次在途合并：
  `00d33a2`（用户裁决取消登录门）、`ba7e827`（A-1 修复，测试注释点名本验收）、
  `d41d35d`（静态 delivery token 组合根分支）。验收与修复在同一环境交替推进，
  各证据均标注其时点的实例形态
- 验收判据（用户定，两条）：①纯 GUI 跑通全流程闭环——凡不能通过 GUI 完成的动作与
  行为闭环一律视为缺陷；②一键打开功能可用
- 方法论：亲自实证不采信转述；写动作前后固定 pre/post curl 对照（只读留档，非操作
  路径）；只报告不修改；未实走路径如实列举；缺陷标本保全不清理（供修复验证）

## 1. 环境与形态

| 项 | 值 | 验证方式 |
| --- | --- | --- |
| 前端 dev | http://127.0.0.1:5280（vite，默认数据源 live） | 浏览器实走 + 页脚自述 |
| 后端 API | http://127.0.0.1:8100/api/v1（uvicorn，单 worker；验收期间随修复重启数次） | curl 实调 |
| 数据库 | cons-live-pg@5533，迁移头 0023 | 经读模型间接验证 |
| 读模型鉴权 | `Authorization: Bearer console-dev-token` | curl 200 |
| 身份 | **无登录门（00d33a2 起）**，默认管理员 · 管理员 | 侧栏亲证；验收人未输入任何凭据 |
| LLM | DeepSeek（shell env，8100 继承）——发现链四步走真模型 | 分析/评分/分类结果实证 |
| SCM 凭据 | 中段起配齐：`REPOMESH_DELIVERY_GITHUB_TOKEN`（静态 token 分支 d41d35d）+ `REPOMESH_DELIVERY_AUTO_ENABLED=true` + `REPOMESH_DELIVERY_REQUIRED_CHECKS=["tests"]`（后者一度缺失，酿成 A-3 首个触发，见 §3） | 主脑通报 + 行为反证 |
| AgentTeams 控制面 | **宿主 8100 始终不可达**（容器 DNS 名 + 8090/6167 未发布宿主端口 + 系统代理劫持三重墙；`/health/ready` → `not_ready:agentteams`；花名册 9/9「探测不可达」为同一事实旧证）——sidecar 转发修复在途 | curl + 主脑 traceback |
| 夹具仓远端现实 | 真实存在：checkout / billing / pricing-core；**api 与 client 远端不存在**；种子 catalog URL 原为占位符，验收中段主脑将 checkout/billing 两行外科更新为真仓地址 | 主脑实测通报 |
| 活体边界 | 5432/8000/agentteams 存量容器全程未操作（新增 sidecar 为主脑修复代理所为） | — |
| 截图链路 | 前段=应用内 Browser 面板，后段=chrome-devtools（用户指示切换） | 图像存于验收会话记录 |

## 2. 验收清单

### A. 一键打开（判据②）——**全过**

| # | 项 | 结果 | 证据 |
| --- | --- | --- | --- |
| A1 | `dev-up.sh --no-browser` 重入路径 | √ | 四阶段逐项「已在提供服务，跳过」，直达 URL 摘要；零副作用（容器 uptime 未断） |
| A2 | compose console 冷路径（**首次实跑**） | √ | `REPOMESH_CONSOLE_PORT=8180 … --profile console up -d --build` 一次通过：三容器 healthy、console-api 自迁移成功、8180→200。界面（登录门拆除后重建）：直入控制台、默认管理员、空态诚实（Open 0/Closed 0/「没有进行中的 issue」/live 页脚——私库无种子空是对的） |
| A2 收摊 | `down -v` 作用域 | √ | 先 `--dry-run` 证只删 console 专属卷；实际执行平台栈无损、共享网络因在用被正确保留 |
| A1 冷路径 | dev 档从零起 | 未实走 | 本机不可安全实走（缺陷 A-2）；冷路径合法实证即 A2 |

### B. 全流程 GUI 闭环（判据①）

三条验收 issue（全部 GUI 创建）：`a2c0c2f9`（优惠码，误落 e2e-fix-verify 工作区→跨组织
负样本）、`5c1b3567`（货到付款，console-demo，A-3 半执行标本）、`35e66beb`（订单备注，
console-demo，物化成功标本）。

| # | 项 | 结果 | 证据（实读值） |
| --- | --- | --- | --- |
| B1 | 新建 issue 真创建 | √ | 三条 issue 弹窗提交全部真创建；处理者随工作区派生（全部工作区→rm-org-leader-e2e-fix-verify；console-demo→console-demo-org-leader）；上一轮 B-1 缺陷修复成立 |
| B1+ | 工作区切换 | √ | 切换器真列表（console-demo 9 agent / e2e-fix-verify / sec-verify-ws / ＋创建）；**上一轮 B-2 缺陷修复成立**（顺带实证） |
| B2 | 发现四步（真 LLM）×3 次实走 | √ | ①充分判定（0.90/0.85/0.90）+关键词；②4 候选评分、rationale 英文原文原样；③三档生成+行内改档+批准：改档留痕（谁改的、原判、时间戳）、审批意见原文入档、`approval.evidence_version` 绑定界面指纹（e8578f9b/7842adbd 亲证两枚）、`effective_tiers` 记 `adjusted:true/original_tier`；④计划生成 {3,1,3}/{2,1,1}/{2,1,0} 与读模型一致。**缺失依赖诚实呈现**：模型点名 catalog 外的 pricing 仓，界面挂「缺失依赖」「模型补充进来的仓库」不吞（该形态与远端现实意外吻合——pricing-core 真存在而不在 catalog） |
| B2-DAG | 计划 DAG 泳道 | √ | 「3 节点 · 1 批次 · 2 条依赖边」批次泳道、锚点仓徽标、图例、四条诚实脚注（未着色原因/边来源/丢弃依赖只进服务端日志/graph_edges 恒空另立项） |
| B2-clarify | 追问回路 + 强行继续留痕 | √ | 含糊 issue `de2973ab` → 「不充分（0.10）· 3 条追问」+缺失维度+逐条答复框；强行继续按钮自述诚实（永久留痕+审计+不重跑模型）；点击后 `forced_continue {at, by_agent_id, ignored_question_count:3}` 落库、GUI 琥珀留痕、步进器按 §3.2 规则 2 放行 |
| B3 | 物化并开工 | **√（第七走真成功）/ 沿途揪出 A-1·A-3·A-6·A-8·A-9·A-10** | 确认弹窗数字七次全对（3任务3团队 / 2任务2团队×3 / 1任务1团队×3 · 主体派生 · 不可逆文案）。①500=A-1；②500=A-3 首触发；③`35e66beb` **HTTP 200 但收据 `status=failed`**【**勘正**：先前版本称此走「物化 200 成功」，与库中收据矛盾，实为半执行，是 A-5 修复前「记录失败被吞成 200」的产物】；④`96896557` 裸 500=A-6；⑤`425efbbc` 503=A-9；⑥`425efbbc` 重试裸 500=A-10（**任务行首次落库**）；⑦`425efbbc` 重试 → **`status=materialized`、`error=null`、`execution_plan_id` 回填**（A-5 回填语义首个正向实证），GUI 转「第 1 轮交付 · 1 仓 · 第 1/1 批执行中」、工程契约 `AE8312D7 V1 DRAFT`、轮次卡 `计划 v1 · 更新于 08-12`（A-4 的 null 降级文案自然消失=修复后形状分叉）、**DAG 节点首次着执行态色 `pending`**（C-4 活体）、**房间区块渲染出 teamRoom+leaderDM 双房间各 2 成员**（B-11 活体） |
| B3-负路径 | 跨组织物化拒绝 | √（A-1 关闭证据） | ba7e827 + REQUIRED_CHECKS 修正后，GUI 重放 a2c0c2f9 → 弹窗「服务端拒绝 … **HTTP 409 · repository 48ff85ee… already has a leader in another organization (d68a5926…); it cannot join this project's topology**」原文透传（截图 S16） |
| B4 | 执行观测（派工→runner→真编码→真仓分支→PR→CI→merge gate） | **交付段首次放行；到达边界=「候选分支已落真仓，PR 开不出来」** | **绿链达成（issue `2ee58846`，billing 单仓）**：`runner.completed` + `commitSha 32c803fd…` + **`testResults: [{"command":"python scripts/run_tests.py","exitCode": 0}]`**；**`change_set` 首次建立**（`18dc502c…`，`status: ready`，此前十余轮恒为 `{}`）；**候选分支已推真仓**——`gh api` 实查 `repomesh-e2e-billing` 分支列表 = `main, repomesh/a762abba/9dfa78f2`，与 change_set 内记录逐字对得上（`head_sha 32c803fd…` / `base_sha 0a7683a9…`），**分支名形如 `repomesh/{plan8}/{repo8}`，与 B4 判据一字不差**。**未发生**：PR（见 A-24）、CI `tests`、merge gate、auto merge。**关键方法论产出**见 §7 第 14 条 |
| B4（旧账，留档） | 前一前沿 | 记录 | 「真编码已完成并 commit，交付段未发生」 | **纯 GUI 驱动到执行面**（issue `89ed8942` / `f6ecb322`，均 checkout+billing）。①**㉑ 收养语义三次成立**——三个 issue 共用同两个仓库级团队（`rm-team-6c503f02` / `rm-team-e0025f7f`），物化**一按即过无 503**（瞬态窗口经清障后消除）；②派工入房、`start_assigned_task` 调用成功、tasks 转 `running`；③**架构勘正**：聊天 worker 15 秒收工是**设计内正确行为**——编码归服务端 Runner 执行面，worker 职责到 TASK_STARTED 为止（先前把它判为缺陷，此处原位【勘正】）；④Runner 拉起后备好工作区（真仓代码 + 冻结 spec + `.repomesh/` 三件套，task→仓映射逐字正确）；⑤**真编码产出**：`runner.completed` 带 `commitSha 5f9ddef0…`、9 个 changedFiles（新增 `tax_calculator.py` / `service.py` + 4 个测试），工作区 git log 确认 commit 真落。**未发生**：候选分支、PR、CI `tests`、merge gate（`change_set` 仍 None——批次内第二条任务尚在跑） |
| B4（质量证据） | agent 产出的质量下限 | √（本轮最有价值的正面发现） | 编码 agent **读懂并遵守了目标仓库 README 关于「假绿」的约定**：`tax_calculator.py` 的 docstring 明写「它刻意不携带税率数据……把表写死会让本仓测试对着没人同意过的数字变绿」；**自识别跨仓未决问题**（checkout 与 billing 的税额舍入口径）并随变更上报；`order.py` 的改动写明 `tax_estimate` 为 advisory、**billing 对实际计税保持权威**——边界拿捏正确。**它证明的是产线的质量下限，不只是连通性。**（同一份收据里的五条 blocker 未被界面呈现，见 A-18） |
| B4（旧账，留档） | 到达边界的历次前沿 | 记录 | 前一前沿=「worker 真正开工，但停在缺工具与误伤审批上」 | **㉓+㉑ 双验证通过（纯 GUI）**：`96896557` 点「重试物化」→ 收据 `materialized`、**prefix 借用原失败键 `70e0944e…`**（借用语义活体成立）、task_ids 4 个、team_count 2；**三条新点名落房**（checkout teamRoom 18:23:45 / checkout leaderDM 18:23:43 / billing teamRoom 18:23:44；billing leaderDM 仍是 11:42 滞留条，未阻断开工）；**两仓 worker 首次真正开工**——`CoPawAgent.reply: max_iters=200`、LLM rate limiter 初始化、执行 shell、保存会话。**随即停摆**：各跑约 10 秒后静默，房间 `message_count` 恒 1、任务恒 `pending`、`change_set` 空、真仓无候选分支——见 A-14（工具不在容器内）与 A-15（守卫误伤 `rm-` 前缀，卡在无人可批的审批）。**未发生**：runner、候选分支、PR、CI `tests`、merge gate |
| B4（旧账，留档） | 到达边界的历次前沿 | 记录 | 前一前沿=「任务实体+房间齐备，但任务包未投递」。阻断层层剥离共十层，逐层实证逐层修：①宿主→控制面三重墙（⑮ sidecar，`/health/ready` 503→ready）→ ②**B-11** 运行时投影零调用点（⑱，房间首次真长出）→ ③**A-6** 派工异常裸 500（译 503）→ ④runtime/镜像配对坏死（`f16925c`，openclaw→copaw）→ ⑤**A-8** 仓库 leader 争仓 400 穿 503（㉑ 收养语义）→ ⑥**A-9** 巡检迟到一整圈（僵尸清理+守卫）→ ⑦MinIO 凭据转义错（环境，主脑自首）→ ⑧**A-10 发布半** S3 异常裸 500 → ⑨**A-10 重放半** 认领任务却不补写任务包、不重投点名 → ⑩观察哨 c：worker 容器侧 mc 对 storage `Access Denied`（威胁后续交付物上传，未立号）。**到达点**：任务行落库、双房间齐备、GUI 显示「执行中」；**未发生**：任务包写入 MinIO（`teams/…/shared/tasks/` 只有 `.keep`）、新点名消息、worker 动工、runner、候选分支、PR、CI `tests`、merge gate |
| B4（旁证） | 执行面读模型（种子 B） | √ | DAG 执行态着色（succeeded 节点+6 态图例+「本页没有轮询」自述）；teamRoom 消息头像/系统条目结构之别+「控制台投影」脚注（§5.2 合规）；环境窗（变更文件/commit/PR #7/基线快照）；事件时间线四 kind 过滤 |
| B5 | GUI 回滚（整 change set） | **√（GUI 语义闭环）** | 种子 B 轮 `9129f894`：轮次卡「回滚…」入口 → 对话框与 v0.1 §4.6 及设计定稿④逐项吻合（范围表 checkout/未 merge/withhold 免费撤回/逆序第 1 步/PR #7；琥珀条不许诺一键还原；理由必填；主体派生随组织正确切换；确认框门控）。pre/post 三中：merge_gate `{allowed:true}`→`{allowed:false,["an active recovery plan is incomplete"]}`；新增 head-bound `ROLLBACK_REQUIRED` 决策（理由原文入档）；recovery_in_progress false→true。GUI 即时呈现「关注·修复观察」+「已有恢复计划在执行」+决策卡 |
| B5-saga | 回滚 saga 真执行 | **待复走** | 勘正：种子 B 的 catalog URL 是占位符，saga 对它的 close 动作在 URL 解析处必失败——**saga 活体证据不能从种子 B 拿**，须在 B4 真交付产生 change set 后对其走 GUI 回滚获取。`gate_display` 仍 "open" 为 §5.3 合规（映射交付 status 非 merge_gate），亲核契约后判非缺陷 |

### C. 横切核对——**全过**

| 项 | 证据 |
| --- | --- |
| 步进器=读模型 step，前端零自判 | 三条 issue 每步 GUI 与 `GET /discovery` 逐一吻合（1→2→3→4→done；clarify 停 1、强行继续放行 2） |
| 空态/失败态诚实 | 草稿三区块说明文字；DAG 锚点 404 双义如实写明；物化失败弹窗回显「端点+状态码+detail 原文」（500 与 409 两形态均实证） |
| 上游重跑作废下游 | 文案与契约在位；实走未做（保护主链快照） |

## 3. 缺陷清单

分类：**A 类 = 功能存在但坏了**；**B 类 = 闭环缺失**；**C 类 = 可选优化**。

### A 类

| # | 缺陷 | 实证与根因 | 状态 |
| --- | --- | --- | --- |
| A-1 | 物化端点对跨组织仓库返 500（应为业务拒绝） | 首走 500；根因=repository leader 单例全局，跨组织 converge 被 provisioner 诚实拒绝（AgentHierarchyViolation）但端点未翻译。修复 `ba7e827`（异常上提 contracts+译 409+回归测试）+ 环境修正（REQUIRED_CHECKS）双因子后，**GUI 重放实得 409+拒因原文**（S16） | **已修已关**（活体反证成立） |
| A-2 | dev-up 启动器会收养并迁移不是自己起的库 | 代码级实证（`dev-up.sh:150-153`）：`compose ps` 见 postgres 在跑即 `own_database=1` → `alembic upgrade head`——防得住陌生进程占端口，防不住**先于脚本存在的同项目 compose 库**（本机活体 5432 即此形态，谱系不符）。与脚本自述「never migrates into anything it did not start」矛盾 | 入册待修（建议：迁移前比对 alembic 谱系，或无 postgres.started 状态文件时要求显式确认） |
| A-3 | **materialize 非原子：失败留半执行状态** | 两条触发路径实证：①REQUIRED_CHECKS 缺失 → `container.py:1002` 请求期 RuntimeError；②AgentTeams 房间未就绪 → `collaboration._route CollaborationRouteUnavailable` 抛穿。两者都发生在轮次/plan 已落库之后——**§8.2/8.3 的「失败不留可重放收据」只保护了收据，没保护轮次行**。标本：`5c1b3567`（轮次 1+仓 2、任务 0 房间 0）。连带发现：容器工厂配置错误在请求期才爆、以 500 示人（启动期校验缺失，主脑已入 backlog） | **已修已关**。修复=503 诚实翻译 + materialize 可重入重放。**503 的「nothing was started」承诺经活体反证成立**：96896557 首按 503 于 11:41:59，而 `task_orchestration.execution_plans` 该轮建于 11:42:20.834（第二按）——首按零副作用；35e66beb 复走三按 503 同样零写入 |
| A-5 | ~~草稿快照消费不持久化~~ → **原假设被推翻，真缺陷是「轮次记录失败被吞成 200」** | 初判：`5c1b3567`/`35e66beb` 快照行 `execution_plan_id IS NULL`，疑 `link_execution_plan` 在 Postgres 路径不落盘。**⑰ 以真 Postgres 16 六测证伪该假设**——link 本身无缺陷；两标本的真身是「半执行 + 收据缺 prefix/fingerprint」。**真缺陷**=计划已启动但快照记录失败时吞异常返 200。修复 `1555abd`：改具名 500 `RoundNotRecorded`，detail 明说重按物化即可补录 | **已修已合并**；条目按实测改写（**方法论**：SQL 现象与代码假设吻合≠根因，六测正对照才是判据） |
| A-6 | **派工期 Matrix 身份不可用以裸 500 逃逸，且再产半执行轮次** | GUI 实走（96896557，2026-08-12 11:42）：第二次确认物化返 **HTTP 500，响应体 `Internal Server Error`（text/plain）=未捕获异常**，非任何具名错误。收据落 `status=failed, error="AgentTeams recipient Matrix identity is unavailable"`；轮次 `c6101abe` 已建、tasks 0——半执行第三例。定性=A-3 已翻译的 `CollaborationRouteUnavailable` 的同族兄弟漏网 | **关闭候选**。修复 `89ee168`+`f16925c`（译 503 + runtime 入 settings）。**活体实证已取得**：425efbbc 三按均为 `503 · the execution plane is not ready to take this plan (AgentTeams recipient Matrix identity is unavailable)` JSON 原文，裸 500 不再复现。待 §7 终判时正式关闭 |
| A-8 | **「每 issue×每仓一队」与「仓库 leader 全局单例」在控制器侧正面冲突** | 35e66beb 复走重放两按，同一原文：`503 · … (AgentTeams HTTP 400: Worker rm-leader-b-checkout is already a member of Team rm-team-6c503f0227a44e9280b3ab29775c0b76)`——**确定性冲突，重试不好转**。DB 铁证：三个 issue（35e66beb/5c1b3567/96896557）的 checkout 队 `leader_agent_id` 同为 `4160c8de…`、billing 队同为 `996dfd64…`；只有先跑成功投影的 96896557 占住了两个 leader 的 team 归属，另两标本从此拉不到人。铸造点 `project/domain.py:161` 用拓扑行 id 铸 `rm-team-{hex}`=每 issue 新名，而架构事实是团队按**仓库**建（主脑核实）。RepoMesh 侧当年为 `AgentAlreadyExists` 做过 converge，**控制器侧 team 成员归属没有对应 converge** | 修复在途（㉑：仓库级共享团队+收养语义、唯一约束迁移 0024、**AgentTeams 400 改译 409**——即「规格冲突不得穿可重试 503 外衣」，契约 §8.7.1 已裁定）。判据=三标本重放自然并入现有团队，零控制器手术 |
| A-9 | **为一个全新仓从零拉起 agent 这条路，产线从未走通过** | GUI 实走（425efbbc / pricing-core，无争仓干净路径）：team 建成、**房间已落** `!5b3ZRusNdXX7K5k4bX:…`，但三按均 503 `recipient Matrix identity is unavailable`。定位：该仓 leader principal 的 `agentteams_resource_name` = **`agt-leader-cbc9e44a49dc`（RepoMesh 新铸名）**，而 `docker ps -a` 中**不存在任何 `agt-leader-*` 容器**。对照：能走到派工的三标本，其 leader 资源名全是**种子预置的 `rm-leader-b-checkout` / `rm-leader-c-billing`**（控制器上早已存在、且有活容器）。即历史上所有「跑得动」的场景无一例外是**复用既有 worker**。代码侧 `runtime_projection.py:148-160` 的 `ensure_worker(state=RUNNING)` 看似正确。主脑控制器内证补充：**worker reconciler 从未收到该资源的入队事件**（应用日志零记录，同一 reconciler 此刻仍在正常处理 gh-*），team reconciler 每 5 分钟撞一次 `credentials not found for agt-leader…` 死循环；spec 与能跑的 `rm-leader-b` 逐字段一致 | **已修已关（结论翻案）**。㉒ 诊断实证：不是「永不拉起」而是**迟到一整圈**——控制器 worker 巡检串行（并发=1），一圈约 16 分钟且被 25 个僵尸 worker 拖满，我的观察窗短于一圈才见「零日志」；`credentials not found` 循环是同一根因的症状。处置=僵尸清理（8 队墓碑+16 worker+10 死容器）+ `df884bd` 守卫（worker 无 Matrix 身份→物化前 503 拒绝，不让轮次死在派工半途）+ 契约 §8.7.2。**活体反证**：`agt-leader-cbc9e44a49dc` / `agt-worker-cbc9e44a49dc` 其后自行排到队并 Running。**方法论**：观察窗短于系统周期时，「零日志」不等于「零事件」。并发现 **A-9b**：`console-demo-org-leader` 的 manager 容器被宿主端口 18888 写死卡住（4 天前老 manager 占用，Bind 失败无限重试）——一宿主一 manager 的结构性冲突，另立不并入 |
| A-10 | **对象存储发布异常裸 500，且重放不补发布——被认领的轮次永久不开工** | 两半。**发布半**：425efbbc 重试物化返 **HTTP 500 `Internal Server Error`（text/plain，未捕获异常）**，收据 `error="S3 operation failed; code: InvalidAccessKeyId … bucket_name: agentteams-storage"`；此时 `execution_plan_tasks` 已首次落库 1 行——**异常发生在任务行之后，又留半执行**。这是 A-6 同族第三个漏网口（前两个 `CollaborationRouteUnavailable`、Matrix identity 均已译 503）。**重放半**：凭据修复后再重试得 `status=materialized`、`error=null`、`execution_plan_id` 回填，GUI 全绿，**但任务包从未写进 MinIO**（主脑亲证 `teams/rm-team-c51f652f…/shared/tasks/` 只有 `.keep`），leaderDM `message_count` 恒为 1、唯一那条 `task_assignment` 停在 **12:54:45**（第一次失败尝试的产物，早于 worker 容器约 13:10 的出生时刻）。而 copaw worker 是 **Matrix @提及驱动**（mirror 源码：`matrix_channel.py` 缓冲消息、被点名才动工；存储 sync 只管文件与 skills，不触发任务），其 Matrix 会话自容器启动起同步——**出生前的那次点名它永远看不见**。三事实闭合：重放认领任务行却跳过「写任务包 + 重投点名」，本轮永久不启动，而界面显示「物化成功·第 1/1 批执行中」 | 修复在途（㉓）。判据已精确化：**重放须补写任务包 + 重投点名，且真正落为新的 Matrix 事件**——附带坑：transaction_id 若按任务稳定派生，重投会被 Matrix 服务器去重静默吞掉 |
| A-14 | **worker 拿到「去调 `repomesh-task-control.start`」的指令，容器内却没有这个工具** | 96896557 重放后两仓 worker 于 18:23:45/46 起跑，各跑约 10 秒一轮 react 即**静默至今**：房间 `message_count` 恒 1（worker 一条回复都没发）、任务恒 `pending`、`change_set` 为 None、真仓无候选分支。派工消息正文（自 worker react 日志的 `msgs_str` 读出原文）明写 `Call the MCP tool repomesh-task-control.start`。**验收侧初判「8100 未配 URL」被主脑直核推翻**：env 一直配着、controller 里两 worker 的 `mcpServers` 完好含正确 URL、MinIO 的 `agents/{worker}/config/mcporter.json` 也在（282 字节含 Bearer）——**链条断在容器内**：mcporter CLI 报 `No MCP servers configured`，配置文件在容器里有两份却都不在 mcporter 实际查找的路径上（源码称拷至 `workspaces/default/config/`，实测该路径不存在；镜像内 `copaw_worker` 与 `copaw` 库版本漂移，启动日志的 import 错误是旁证）。**并入本条的系统性观察**：`principal_registration.py:45-46` 的 `with_task_control` 在 url 为空时静默跳过挂载，而派工文案**无条件**宣称该工具存在——两端零一致性校验，能力与指令可以各说各话 | 修复在途（㉔：mcporter 真实查找路径 + 存储级投放使其对容器重建免疫）；**根因细节待 ㉔ 定** |
| A-15 | **工具守卫按子串匹配，把资源名里的 `rm-` 当成 `rm` 命令拦下，agent 停在无人可批的审批上** | worker 自救时试图 `cat` 配置文件，`TOOL_CMD_DANGEROUS_RM` 规则以**子串**匹配命中路径中的 `rm-worker-b-checkout`，判 HIGH → 提示「输入 /approve 批准」→ **房间里没有人能批** → agent 就此停在等待审批。验收侧日志里那条 `[TOOL GUARD] HIGH … matched='rm'` 即本条现场。**误伤面全局**：本系统所有资源名都是 `rm-` 前缀 | 修复在途（㉔：守卫规则改词边界匹配；并查「当前卡住的审批能否经房间 `/approve` 就地解锁」） |
| A-18 | **agent 写明「我什么都没执行过，合并前请重跑」，界面把它渲染成绿色的「已交付」** | task `6ba476ab`（89ed8942 / checkout 税费）`runner.completed` 报 `status=succeeded`，commit 真落（`5f9ddef repomesh: complete task 6ba476ab…`），读模型 `display_status=succeeded`，GUI 上 DAG 节点着绿、图例「已交付 succeeded」。**而同一份收据的 summary 里写着五条 blocker**：①**「Nothing was executed. The sandbox refused every `python` invocation ("requires approval") and `git` as well … "code compiles / existing tests pass" is reviewed-by-reading only, not verified. Please re-run before merging.」**；②跨仓一致性测试「cannot be written honestly」，被**显式 skip 并把理由写在行内**（「断言一个本仓自己发明的 billing 数字是自我同意，不是验证」）；③新测试落在 `src/checkout/tests/`，而 `scripts/run_tests.py` 只扫 `ROOT/tests`——**这批测试不会被发现**；④任务词汇在本仓不存在（无 HTTP 层，endpoints 实现为 handler 函数）；⑤两个开放的跨仓契约问题（舍入口径、total 是否含税）。结构化佐证同样在读模型里：`testCommand: null`、`testResults: []`、`artifacts: []`。**GUI 全页文本 grep `Blocker` / `Nothing was executed` / 「未执行」/「沙箱」——零命中。** 实现根因=老 backlog「`result_summary` 仍挖自由文本」：它在读模型里是一个 JSON 串，而 `tasks[].evidence` 是空数组，前端无从渲染 | 修复在途（㉖：`TaskEvidenceView` 扩 `verified`/`blockers`/`skipped_tests`/`test_results` 有无，读模型透出，前端在**任务卡、DAG 节点详情、merge 审批面**三处强制渲染，blocker 原文摆出）。【**勘正**：本条初判为「绿灯直通 merge、`delivery_auto` 下无人过目」，**实测推翻**——交付端有硬性拒收：批次完成触发 `_advance_if_ready → _candidates_for_batch`，抛 `ValueError("Runner evidence has no test results")`（`plan_delivery.py:289`），**无测试结果的 runner 证据根本进不了交付段**。系统比初判更诚实。】**第四面（`runner.failed` 的原因同样无出口，双层缺口）**：74e9701e 两条任务失败后，DAG 节点如实渲染 `failed`（**失败态着色首次实证**，S27，此前 §4 一直是未实走项），但失败原因 `changed_path_denied: tests/test_discount.py` 在 GUI 上**零命中**——界面能说「这个仓失败了」，**不能说为什么、也不能说怎么办**（而该原因恰恰可操作）。主脑核实根因：失败任务的 `result_summary` 在库里**是结构化 JSON**（summary 即原文），但 ㉖ 解析器的进门条件是「非空 `commitSha`」，而失败 run 的 `commitSha` 为 null，**整块证据被丢成 NULL**（live 读模型亲证两条 failed 任务 evidence 均 NULL）。故为**读模型半（解析器拒收失败形态）+ 展示半（前端无失败原因渲染面）**双层缺口。修复在途（㉖ 四度续：判别式放宽为「带 runner 文档键」而非「成功」、`commit_sha` 可空化、失败面 salmon 渲染原文、**琥珀未验证标记不叠在 failed 上**——失败已是更响的真相，不双标）。**第五面**：`runner.failed` 的收据只保留一行机器原因（`test_command_failed: …`），**agent 在失败路径上说了什么全部丢失**——42948c61 那次「零改动却失败」因此不可考，**缺口本身成了唯一的证据**。**第六面**：`gate_display: "waiting"` 不说明在等什么——等 PR？等 CI？等人？界面不说；A-24 那次 PR 开不出来时，用户能看到的只有这个词。两面共同修法=失败载荷同样携带 agent summary、等待态携带其原因，与 ㉖ 的证据面天然衔接（排下批）。**改写后的定性**：不是「假绿放行」，而是**诚实拒收、但拒收本身无声**——异常在后台被吞，既不落状态也不进投影，于是界面留下「两个绿色的已交付任务 + 空的 change_set」，永远僵持，无人知道它被拒了、更无人知道为什么。**沉默的仍然是同一件事**：agent 说的话与系统做的判断都在，只是都没有出口。**普遍性**：本轮两条任务（checkout 与 billing）**全部**是「写完了但一行没跑过」——在当前形态下 `succeeded` 几乎恒等于「未验证」，不是偶发 |
| A-20 | **新端点默认不接异常翻译：裸 500 已复发四次** | 同一形态在本轮出现四次，每次都是**新上线的端点**：①materialize 撞 `AgentHierarchyViolation`（A-1 时代）；②派工 Matrix identity 不可用（A-6）；③对象存储发布 S3 错误（A-10）；④**重新派工 `redispatch`**（本条现场：`POST /deliveries/{id}/redispatch` → `HTTP 500`、`content-type: text/plain`、body `Internal Server Error`；实际根因是 `StringDataRightTruncationError: value too long for varchar(200)`——重派的键派生把浏览器传来的完整幂等键逐字拼进本就很长的派工消息键，超列宽被数据库拒绝）。**每次修的都是那一个端点，下一个新端点照旧裸奔**——说明缺的是层级默认，不是个案疏忽 | 入册（C 类·系统性）。候选修法=API 层统一 JSON 500 信封兜底，**具名翻译仍为首选路径**（信封只保证「不裸奔」，不替代把已知失败译成 4xx/503）；实施排下批，本轮先按端点补齐。**本例的主缺陷记 ㉕ 实施缺口并入 A-13 册面，不另立号**；**零副作用经实测确认**（无新派单、`attempt` 未变、`last_dispatched_at` 原值）——数据库回滚的正确表现 |
| A-24 | **「分支没配保护规则」被当成「仓库不存在」，PR 因此开不出来** | 绿链首段成功后（change_set 建立、候选分支已推真仓），**PR 五分钟未创建**，读模型只显示 `gate_display: "waiting"`、`pull_request_number: null`，界面无任何错误。主脑查 8100 日志定根因：开 PR 前要读 base 分支保护规则（`open_draft_pull_request → get_branch_protection → GET /branches/main/protection`），**GitHub 对「未配置保护」返回的正是 404**，而通用请求层把一切 404 译成 `SCMNotFound`（「仓库或 PR 不存在」）抛出——**发布流程在推完分支之后炸死**。夹具仓 main 无保护规则，于是 GUI 路径第一次踩到「无保护仓」这一形态（W 时代未炸，原因待查：其仓可能配过保护，或路径不同源）。**又一例「零调用点族」**：本轮是第一个成功建立的 change_set，「PR 创建」这条路径**在本轮验收里第一次被走到** | **已修**（㉙：404 译回「诚实的无保护」+ 全消费方审计——含 D-3 时代用保护规则区分「无 CI」的 revert 逻辑不得被破坏、错仓 404 仍须是 `SCMNotFound`；另一处真功夫：光捕 404 不够，预检还会把「无保护」拒成「缺必需检查」，故加 `protected` 标志让严格性比较对无保护分支整体跳过——**放宽开 PR 预检不弱化合并决定，merge gate 仍只信 RepoMesh 自身观测**）。**连带暴露续发缺口（一次性事件死角第五例，见 §7 第 9 条）**：修好 404 后 PR 并未如预期「下一拍自动出现」——`reconcile_and_merge` 对无 PR 候选**直接 `continue`**，而 `_advance_if_ready` 的两个触发源（任务终态 / 观察重放）都不会再来，于是「分支已推、PR 未开」成为死角；㉚ 补 reconciler 巡检「完成缺失的 PR」（分支已在远端只需 `open_draft`，幂等防双开） |
| A-22 | **一轮执行失败，issue 就被判为 `Closed`——契约级缺陷** | 74e9701e 两条任务失败后，issue 头部显示 **`Closed · 执行失败`**。主脑核到规则本体（`mappings.py` `derive_issue_state`，按契约 v0.2 §2.1 顺序求值）：失败轮次既非 active、又无 change set、草稿已消费 → 落进「有过轮次即 Closed」这一支。**决定性证据是规则自己的注释**：「state 回答的是『这事还需要人或 agent 吗』——paused 刻意不关闭」；而**一个执行失败的需求显然还需要人**，规则违反了自己写下的定义。**这是契约缺陷不是实现缺陷**——§2.1 起草时没有料到 failed 形态。验收侧的产品判断：**失败的是这一轮的执行，不是这个需求** | 修复在途（㉘：最新轮次 FAILED 且未归档 → `OPEN`；归档后落回原规则）。**顺带澄清了动线**：轮次卡在失败态才出现的「**归档本轮**」按钮，语义=「我承认这轮失败、到此为止」——**归档才关闭，不归档就是还要修**（重派入口自然在）。两条动线由状态自身分流。**衍生 C 类（backlog）**：失败态目前没有一行指路文案，界面对「接下来该干什么」不表态 |
| A-23 | **agent 说「我没验证」，平台记录 `exitCode: 0`——两句都真，但没有字段说这个 exit 0 是谁跑的** | 两次复现。①`f3fb38d9`（74e9701e/checkout）：`testResults: [{"command":"python scripts/run_tests.py","exitCode": 0}]` + 真 commit，而同一份收据的 summary 写着「every invocation … was rejected by the permission layer, so **I have not verified the suite passes.** Per the self-test skill I won't claim a green run I didn't observe」；②`c0ec138b`（2ee58846/billing，绿链那次）同形复现。**两者都没撒谎**——测试是 **Runner** 在 agent 交工后执行的，agent 自己那次尝试确实被沙箱拒了（沙箱拒绝任意 shell 是设计内安全姿态）。但读模型里两条信息并列摆着，**没有任何字段说明执行者是谁**：只显示 agent 自述→用户以为没验证；只显示 exit 0→用户以为 agent 自测过；而 ㉖ 的 `verified` 该取哪个值也因此歧义 | 入册待修（**优先级已上调至下批第一位**）。**语义已裁定**：`verified` = **平台执行的验证**，故本例 `verified=true` 正确，歧义在展示层。修法=`testResults` 加 `executedBy`（`runner`/`agent`）+ 证据面把「agent 自述」与「平台执证」分两栏 |
| A-21 | **任务的可改路径不覆盖其测试命令所扫的目录——规避者假绿、守规者被拒，两条路都不通** | 同一条链上的两个约定互斥：派单给的测试命令是 `python scripts/run_tests.py`（扫 `ROOT/tests`），而派单给的 allowed paths 是 `src/{repo}/**`（**不含 `ROOT/tests`**）。两侧实证齐全：**守规者被拒**——74e9701e/checkout 的 agent 把测试写进 `tests/test_discount.py`（正是测试命令要扫的地方），`runner.failed` 报 **`changed_path_denied: tests/test_discount.py`**，`commitSha: null`，**5 个合规文件连同 1 个越界文件整轮作废**；**规避者假绿**——89ed8942 的 agent 主动把测试写进 `src/checkout/tests/` 并在 blocker 里**预言了这件事**（原文：「Allowed paths are `src/checkout/**`, which **excludes `tests/`** … Moving them next to `tests/test_order.py` is a file move **once a task grants that path**」），代价是新测试不被 `run_tests.py` 发现；74e9701e/billing 同样规避，于是没被拒，但 `run_tests.py` 跑的**只是仓库原有测试**，agent 新写的那些根本没执行——exit 1 到底是原有测试被改挂还是别的，界面无从判断（「红得不明不白」） | 修复在途（㉗ 续：catalog 显式 `test_paths` + **派单期并集进 allowedPaths**，spec 自带保留、catalog 只增不换，重派即救存量）。**这是「A 侧可选、B 侧必需」的第六个实例**（§7 方法论第八条） |
| A-19 | **派单不带测试命令，于是「自测」环节从未运行过** | 两条任务的 agent 都报告无法执行测试（一条「every `python` invocation … "requires approval"」、一条「denied by the permission layer (6 attempts, various forms)」）。【**根因勘正**：初判为「沙箱禁 python」，主脑核实推翻——**沙箱拒绝任意 shell 是设计内安全姿态**，agent 另有专用 test 工具；真根因是**派单的 `testCommands` 为空**（live payload 实证），源头在 `TaskNode.tests` 的注释：「集成 LLM 不产出，物化调用方补给」——**脚本时代补了，控制台路径没补**。】即：工具没被禁，是**没有命令可跑**。后果=自测环节空转，而 agent 的新测试又落在 `scripts/run_tests.py` 扫不到的目录（`src/checkout/tests/`），**CI 也跑不到它们**——两个验证源同时落空，正是目标仓库 README 自己警告的「假绿」在产线上的完整复现 | **已修，判据达成**。㉗：catalog 加每仓测试命令列 + 物化注入 + 交付拒收落账投影。**端到端实证**：①注入——新轮次（74e9701e）两条派单的 `task_payload.testCommands` = `["python scripts/run_tests.py"]`（对照组：修复前物化的轮次重派后仍为 `[]`，**证明注入点在物化期**，见下）；②执行与回传——`e3b42665` 的 `runner.failed` 带 **`testResults: [{"command":"python scripts/run_tests.py","exitCode":1}]`**，**产线第一次真的跑了测试，`testResults` 首次非空**；③落账——issue `phase: failed` + **`phase_note: "执行失败"`**、轮次 `status`/`phase` 双 `failed`，**失败不再被后台吞掉**。**结果是「真红」**：测试真跑、真挂、如实报失败、不 commit——与本轮此前所有「假绿」形成对照，**证明这条链的验证环节是有牙齿的**。**衍生 C 类（已记）**：注入只在物化期发生，修复上线前的轮次无自救路径（b 线已合并 `84ea55d` 把解析移到派单装配期，存量轮次下次重派自动获救）；**设计教训**：凡在时点 T 烘焙的配置，都要问一句「T 之后配置变了怎么办」 |
| A-17 | **一键启动不含执行面：Runner 进程要手工拉，且其部署配置随部署形态漂移无人守护** | 两半。**覆盖缺口**：`dev-up` 无 Runner 阶段，执行面消费进程（`python -m repomesh_runner`）需按老 E2E 配方手工启动——**「一键打开」对执行段是空承诺**（判据②的新缺口，S 批遗留）。活体后果：`runner_dispatches` 四条派单全部 `queued` 无人认领，工作区不建、代码不写，而 GUI 一路显示「第 1/1 批执行中」。**配置漂移**（主脑拉起后实测）：Runner 每次租到任务即报 `task source returned an unparseable task: workspace path does not match the configured execution-plane prefix`——老配方是「API 在容器里」年代的路径前缀映射（`/runner-workspaces`→宿主），而 8100 现在在宿主上直发 Windows 路径，前缀对不上，于是**每单都被判不可解析后放掉，只续租不开工**。验收侧当时的读法「Runner 活着、在续租、却一条 `runner.started` 都没发」经日志确认准确 | 入册待修：`dev-up` 增 runner 阶段（env 从 `.env` 派生），并按「API 在宿主 / 在容器」两形态生成路径映射。**正面记录**：修好后 40 秒内首单被 accepted；19:15 创建的派单等了 100 分钟照样被正确执行——**执行面的派单本来就是可收敛的**（与聊天面「点名是一次性事件」形成对照，见 A-13 / ㉕） |
| A-12 | **运行时投影不收敛：`runtime_status` 停在 pending，而房间、容器、派工全已就位** | 成功物化那次（13:51:48）明明跑了 reconcile，`project.repository_agent_teams.runtime_status` 仍为 `pending`，同时 `room_id`/`leader_room_id` 双房间落库、worker 容器 Running、派工消息已达。GUI「关联仓库 · 团队」因此写「团队待建」，与同页「房间」区块渲染出的真实双房间自相矛盾。此现象初见于 A-6 期（当时判「修活后应自然收敛」而未立号），本轮**实测否定了该预期** | 入册待修（疑 reconcile 写 `runtime_status` 的条件或时机有缺口）；不阻断 B4 |
| A-4 | **每个刚物化的轮次杀死 issue 详情页** | 全链：读模型对新轮次投影 `updated_at:null`+`plan_version:null`（curl 双标本实证；**数据源头=A-5**）→ `RoundsPanel.tsx:107` `dayLabel(round.updated_at)` → `display.ts:151` 对 null 调 `.match` → TypeError → **无错误边界，SPA 整树卸载**，hash 导航救不回须整页刷新。**产线主流程必踩**（物化后到首个活动 stamp 前该 issue 页必死）；种子轮次全带时间戳，故此前历轮验收未暴露 | 修复在途（⑯：空值容忍+区块级错误边界+「刚物化·尚无活动」与「物化中断产物」两种诚实文案分开；判据=5c1b3567 活标本页面恢复可达且半执行态诚实呈现） |

### B 类

上一轮 **B-1（issue 写端点）与 B-2（工作区列表/切换）修复成立**并在本轮实证；其余
B-3~B-10 未复测，仍以上一轮报告为准。本轮新增一条（编号顺延）：

| # | 缺陷 | 现状（实证） | 缺失能力 |
| --- | --- | --- | --- |
| B-11 | **GUI 物化路径从不向 AgentTeams 投影运行时** | 注册 agent / reconcile 团队房间的代码只接在 `run_pipeline` 脚本，src 零调用点；控制器内 rm-team 系 4 agent/2 team 全不存在、room_id 恒 NULL（主脑诊断代理事实链）。GUI 物化建的团队永远停在 pending——**通路修好房间也不会长出来**，历史上「能跑」全靠脚本旁路 | **已修已关**。⑱（`b908fd1`/`977ce3c`）：materialize 在 start_plan 之前同步注册+reconcile，任何队缺房间→503 不半执行；契约 §8.7 转正。**活体证据**：96896557 两队 `room_id`/`leader_room_id` 落真 Matrix 房间 id（对照旧标本 35e66beb 仍 null），**GUI 房间区块第一次渲染出真实双房间**（teamRoom/leaderDM 各 2 成员，S20） |
| B-12 | **半执行轮次在 GUI 上没有任何重按物化的入口** | 复走第 1 步即撞：35e66beb 的发现面第 4 步之后不是按钮而是一行「本 issue 已物化…」。根因跨两层——服务端 §8.3 收据本就带 `status=failed`+error 原文，但 `GET /discovery` 读投影不透出它；前端 `DiscoveryPanel.tsx:862-888` 因此只能拿 `roundCount > 0` 一刀切换文案。后果=⑮ 给的可重入重放（`7659c89`）在 GUI 上无触发口，只能 curl。原 P1 第 7 条「重试入口前置字段缺失」的说法据此勘正（字段本就存在，缺的是投影） | **已修已关**。⑲（`ba2eec8`/`dfb6a51`）：读投影透出收据 + 前端按 status 分形态渲染；契约 §3.1.1 转正。**活体证据**：35e66beb 页面长出「重试物化」按钮，旁印**上次失败时间与原文**，并写明「重试会补完这一轮而不是另起一轮：服务端按 §8.3 认领上次留下的痕迹」（S22） |

| B-13 | **「收据说成功、实际没投递」的遗留态，GUI 与 API 双向无路** | `425efbbc` 在 ㉓ 之前留下一份 `status=materialized, error=null` 的**假成功**收据（认领了任务行却跳过发布）。此后：**GUI** 按 §3.1.1 的 status 分形态渲染，`materialized` 走「本 issue 已物化」一支，全页零个含「物化」字样的按钮；**API** 亦不可达——新幂等键 → `_replay` 无命中 → 草稿已消费 → **409 `discovery chain is closed`**（实测），原幂等键 → `_replay` 命中 `materialized` 直接返回缓存结果、**根本走不到发布步骤**，而 `_prefix()` 的借用条件是 `status == "failed"`，本条不满足。**第三面**：即便「重新生成计划」产出新草稿，前端「无收据 + roundCount>0」分支仍走「已物化」文案不出按钮——连「另起一轮」的 GUI 路也被挡着 | **不补入口，封状态**（主脑裁决）。该形态在 ㉓ 之后不可再生：首按发布失败必然抛→收据 `failed`（重试入口在），重放亦无「账面齐全即跳过」的假成功口。425efbbc 是前 ㉓ 时代的遗留孤例，**就地封存为活体证据标本**。数据手术（收据翻 failed + `execution_plan_id` 置 NULL + 补 prefix/fingerprint）是唯一理论清偿路径，但指纹派生错会分叉出第二份计划——为一个演示标本不值得，不做。「再生路径已封」以 96896557 的正向重放为**间接**证据，直接实证不可得（㉓ 的目的正是让假成功造不出来）；**复发即重开并升级**，届时才轮到读投影表达「已认领未投递」+ 专用入口 |

诊断附注（素材入档）：物化后 docker 里「长出」新 worker 容器实为巧合——controller
挂着 docker.sock 在轮流重启全部历史容器，Exited(1) 是容器内 baked 的 AUTH_TOKEN
在 08-08 控制器重启轮换后失效、起来即 401 自杀；没有任何团队规格曾送达控制器。

**runtime 配对坏死（A-6 根层，实测账）**：⑱ 首版照 `run_pipeline.py` 抄了
`OPENCLAW` runtime（`agent_team.py:53/63` 的默认值亦然），而本机唯一可活配对是
`copaw`（`provision-repomesh-team.ps1:7` 的默认）。实测：openclaw 落
`…/agentteams/agentteams-worker:latest` 镜像，其入口脚本要 `HICLAW_WORKER_NAME`，
而控制器只传 `AGENTTEAMS_WORKER_NAME` → 容器秒退 `Exited(1)`；copaw 落
`agentteams/copaw-worker:latest`，要的正是控制器传的那个名 → 08-09 建的六个
`repomesh-gh-*` 至今 `Up 2 days`。**此前 E2E 从未暴露的两层原因**：①那次用的是
既有 copaw 活体 worker，全程没新建容器；②GUI 路径被 B-11 挡在更早一步，从没机会
执行到建容器。修复 `f16925c` 把 runtime 提为设置、双路共源。

### C 类

| # | 项 | 实证 |
| --- | --- | --- |
| C-1 | 分类补充仓名未归一化去重 | `supplemented_repos` 存 `repomesh-e2e-pricing` 与 `repomesh-e2e-pricing (not in candidate list)` 两串（LLM 括号注记漏进仓名）；服务端数据，前端诚实渲染出重复。主脑确认入册 |
| C-2 | 「全部工作区」下建 issue 隐式落入非预期组织 | a2c0c2f9 在「全部工作区」创建，处理者静默派生为 e2e-fix-verify 的 org leader——用户无从预期 issue 会落进哪个组织（本例直接造成跨组织不可物化）。建议：全部工作区下建 issue 时要求显式选组织，或弹窗内明示目标工作区 |

## 4. 未实走路径（如实列举）

1. **B4 主链的执行段**（worker 动工→runner→推真仓候选分支→PR→CI `tests`→merge gate）——物化、建团、双房间、任务实体均已达成，**任务包投递及其之后全部未发生**，阻断见 A-10 重放半；
2. **B5 saga 真执行**——依赖 B4 产出真 change set（GUI 语义半已于新头复核通过，见 B5 行与 S21）；
3. **5c1b3567 / 35e66beb / 96896557 / 425efbbc 四个半执行标本的重放转正**——35e66beb 已试并撞 A-8（零副作用），其余三个待 ㉑/㉒ 落地后走；
4. dev 档一键冷路径（A-2，本机不可安全实走）；
5. 上游重跑作废下游（§4.4）实走；
6. 多批次 DAG 泳道（本轮计划均单批次）；~~失败态着色~~ **已实证**（74e9701e 两条任务
   真失败，DAG 节点渲染 `failed`，S27）；
7. 「回答追问并重新分析」（clarify 的 a 出路，只验了强行继续）;
8. replay 模式；审批/物化幂等重放 409 族（跨组织 409 已验，指纹漂移/重放族未构造）。

## 5. 验收产生的数据（种子重置清单）

| 对象 | 内容 | 处置 |
| --- | --- | --- |
| `a2c0c2f9`（e2e-fix-verify） | 发现链全走+批准+计划 v1；物化 409 负样本 | 随种子重置清理 |
| `5c1b3567`（console-demo） | **A-3/A-4 活体标本**：半执行轮次（轮次 1+仓 2+任务 0+房间 0） | **保全勿动**——A-3 重放收敛与 A-4 页面恢复的修复验证件 |
| `35e66beb`（console-demo） | **半执行标本**（勘正，非「物化成功」）：HTTP 200 但收据 `status=failed`；轮次 1+仓 2+团队 2（pending，room_id null） | **保全勿动**——A-8 修复（㉑ 收养语义）的验证件；复走已证其重放撞争仓 400 |
| `96896557`（console-demo） | **㉓+㉑ 双验证件、现为 A-14/A-15 活体标本**：GUI 重试物化成功（prefix 借用、四 task_ids、三新点名）、两仓 worker 真正开工后停在「缺工具 + 无人可批的审批」 | **保全勿动**——worker 那句「等待审批」是 A-15 现场，㉔ 修复的验证件 |
| `425efbbc`（console-demo，pricing-core） | **A-10 标本（形态已推进）**：物化成功（收据 `materialized`、`execution_plan_id` 回填）、轮次 `1dcdfea7`、**任务 1 行**、双房间齐备、DAG 着 `pending`；但任务包未落 MinIO、worker 未动工 | **保全勿动**——㉓「重放补完」的验证件；判据=任务包落库+新点名入房+worker 出现任务处理迹象 |
| `repomesh-e2e-pricing-core` | 经 GUI 添加仓库卡片注册进 catalog（注册 1/跳过 0/失败 0） | 随种子重置清理 |
| `de2973ab` | clarify 验证：不充分判定+forced_continue 留痕 | 随种子重置清理 |
| `9129f894`（种子 B） | 已改性：+ROLLBACK_REQUIRED 决策+恢复计划（占位 URL 上不会真执行） | 种子重置时复位；不再是「唯一 approve 待放行」形态 |
| `e6b251db` | 空文本 issue，非验收产物；其三次 materialize 500 已归因 **A-1 同族**（跨组织 AgentHierarchyViolation，发生在写行前无半执行，ba7e827 已覆盖） | 主脑已入清理清单 |
| catalog | checkout/billing 两行 URL 被主脑外科更新为真仓地址（原占位符） | 入种子重置清单（主脑留档） |
| compose console 栈 | 已 `down -v` 清理 | 无残留 |

## 6. 截图取证（图像存于验收会话记录）

| # | 内容 | 关键可见证据 |
| --- | --- | --- |
| S1 | 登录门（变更前形态，已被用户裁决取消） | 历史留档 |
| S2 | 新建 issue 弹窗 | 派生处理者+需求文本 |
| S3 | B2-1 完成态 | 充分 0.90+关键词；步进器 1✓ |
| S4 | B2-2 评分展开 | 4 仓评分条+rationale 原文 |
| S5 | B2-3 改档待提交 | 「待提交: 排除」+「本次改档 1 项」+指纹 |
| S6 | B2-3 批准后 | 改档留痕+意见原文+审批绑定指纹 |
| S7 | 四步全✓+默认管理员（无登录门形态） | 步进器 ✓✓✓✓ |
| S8 | 计划 DAG 泳道 | 3 节点/1 批次/2 边、锚点、图例、脚注 |
| S9 | 物化确认弹窗 | v1·任务·团队·主体·不可逆文案 |
| S10 | B4 旁证：teamRoom+环境窗+事件时间线 | 头像/系统条目之别、PR #7、四 kind |
| S11 | 回滚对话框 | 范围表+琥珀条+派生主体+门控提交 |
| S12 | 回滚后决策夹+轮次卡 | 「关注·修复观察」+「已有恢复计划在执行」+ROLLBACK_REQUIRED 卡 |
| S13 | A2 冷启动控制台（8180） | 直入+默认管理员+空态诚实 |
| S14 | clarify 追问面板 | 不充分 0.10+3 追问+答复框+强行继续自述 |
| S15 | 强行继续留痕 | 琥珀留痕+步进器放行 |
| S16 | **A-1 关闭证据：物化 409** | 「服务端拒绝 … HTTP 409 · already has a leader in another organization … cannot join this project's topology」原文 |
| S17 | **B-12 立项证据**：35e66beb 无重按入口 | 第 4 步之后只有「本 issue 已物化…」说明文字，全页 button 枚举无「物化/重试」 |
| S18 | 96896557 物化弹窗 | v1 · 2 任务 · 2 团队 · 主体 console-demo-org-leader |
| S19 | **A-3 的 503 诚实形态** | 「rooms … has not created rooms for rm-team-6c503f02…, rm-team-e0025f7f… yet); nothing was started」原文 |
| S20 | **B-11 关闭证据：房间首次真实渲染** | 96896557 房间区块 teamRoom/leaderDM 双房间、各 2 成员 |
| S21 | **B5 GUI 语义新头复核** | 范围表+琥珀条+**「已有恢复计划在执行 … 换一个理由再提交会被 409 拒绝；重复提交同一份表单则是重放（后端零写入）」**+三道闸 |
| S22 | **B-12 关闭证据：重试物化入口** | 「重试物化」按钮 + 上次失败时间与原文 + 「重试会补完这一轮而不是另起一轮」 |
| S25 | **A-18 第一面：DAG 节点未验证标记** | 两节点 `succeeded` + `未验证`，图例新增「未验证（标记，非状态）」（首查未见，刷新后出现——见方法论第 11 条） |
| S26 | **重新派工弹窗（本轮文案标杆）** | 两档单选、第二档「**这一条是真的写**」、任务清单带 agent 与时间戳、「服务端按后端状态判定，不是界面挑的」、「**界面不替你判断『卡住了』**」 |
| S27 | **失败态着色首次实证** | 74e9701e：checkout 节点 `failed`、billing `running`；**但失败原因 `changed_path_denied` 全页零命中**（A-18 第四面） |
| S32 | **交付段全帧：环境窗 + PR #3** | 状态「门禁运行中」、变更 `src/billing/invoice.py` `tests/test_invoice.py`、`commit 32c803fd`、**PR `catbobyman/repomesh-e2e-billing#3`**、基线快照 `110405f1`、事件时间线四条按序（PLAN→MATRIX→RUNNER accepted→completed）；轮次卡同时长出「1 个仓库有已发布候选可撤销」+「回滚…」 |
| S33 | **一次性事件死角第六例活体标本** | **CI `tests` 已绿，而 PR 永远停在 draft**；界面只显示「门禁运行中」，`merge_gate` 从未被计算。取证纪律：验收方有 gh token 但**不手动 undraft**，以保全这一帧 |
| S28 | **A-18 第四面「修复前」基线：issue 头部** | `Closed · 执行失败` + `failed` 徽标（A-22 现场：一轮失败即关闭需求） |
| S29 | **同上：DAG 与轮次卡** | 刷新后两节点均 `failed`；轮次卡 `第 1 轮 failed`，失败态专属的「**归档本轮**」按钮出现；**三处说「失败」，零处说「为什么」** |
| S23 | **执行面首次越过：425efbbc 物化成功** | 「第 1 轮交付 · 1 仓」「第 1/1 批执行中」+ 工程契约 `AE8312D7 V1 DRAFT` + 轮次卡「计划 v1 · 更新于 08-12」+ **DAG 节点着 `pending` 执行态色** + **teamRoom/leaderDM 双房间各 2 成员**（leaderDM 末条为 12:54 的 `task_assignment`——A-10 重放半的界面侧证据） |

瞬态与白屏备注：materialize 500 两次、A-4 白屏（空 a11y 树+console TypeError）以
DOM/console 实读留档；A-4 崩溃页面无帧可拍（白屏本身即证据形态）。

## 7. 总结论（待复走更新）

**当前判定：不可进入推送。** 判据②全过（compose 冷路径首跑即通为本轮重要正面结论）。
判据①：发现链、审批、计划、DAG、回滚语义、clarify 全部实走通过且契约红线零违例；
**物化本身已在活体上真正走通**（第七走 `425efbbc`：收据 `materialized`、任务实体落库、
双房间齐备、执行态着色生效）；**但执行面止步于「任务包未投递」**——worker 从未动工，
runner / 候选分支 / PR / CI `tests` / merge gate 五项均未发生（A-10 重放半，㉓ 在修）。

**已关闭**：A-1、A-3、A-5（改写）、A-9、A-10（㉓ 已合并并由 96896557 正向重放实证）、
B-11、B-12。**待修**：A-2、A-6（关闭候选，待终判正式关）、A-8（㉑ 已合并，两标本收养
重放待验）、A-12（投影不收敛）、A-14 / A-15（㉔ 在修）、A-9b（manager 端口冲突）、
B-13（封状态不补入口）、C-1、C-2；观察哨 A-11（org leader 上行消息 503）、观察哨 c
（worker 侧 mc 对 storage `Access Denied`，威胁交付物上传）、观察哨 d（leader 滞留点名
不补投，未阻断开工故未立 A-13）挂起。

终判前置：㉔ 落地 → 96896557 的 worker 真正产出 → B4 全链（分支/PR/CI/gate）→
真 change set 上的 B5-saga → 35e66beb / 5c1b3567 收养重放 → §7 终判。

### 方法论沉淀（本轮）

1. **走生路才验得出东西**：本轮全部 A 类与 B 类缺陷，只在「发现链亲产数据 + 全新轮次 +
   活体 Postgres/控制面」的形态下触发。种子数据是熟路、测试 store 是替身、脚本旁路是
   暗门——三者合起来能让一条从未跑通的链看上去一直是绿的。
2. **零调用点族**：`B-11`（运行时投影只接在脚本，`src/` 零调用点）与 `A-9`（为全新仓
   从零拉起 agent，历史上全靠复用既有 worker）是同一形态的两例——**「能跑」的历史证据
   可能全部来自旁路**。判别法：问「这条路第一次跑是什么时候」，而不是「它跑过吗」。
3. **SQL 现象与代码假设吻合 ≠ 根因**（A-5）：`execution_plan_id IS NULL` 与
   「link 不持久化」严丝合缝，但六测正对照证伪了它，真因是记录失败被吞成 200。
4. **探针通 ≠ 凭据对**（A-10）：MinIO `/minio/health/live` 不需要鉴权，200 只证明网络
   通。与「信号对≠原因对」同族。
5. **观察窗短于系统周期时，「零日志」不等于「零事件」**（A-9 翻案）：控制器巡检串行
   一圈约 16 分钟，我的观察窗短于一圈，于是把「迟到」误读成「永不发生」。
6. **界面绿 ≠ 链路通**：本轮已出六例——A-10（收据 `materialized` 而任务包没写、点名停在
   收件人出生之前）、A-12（`runtime_status` 说待建而房间容器全在）、**A-14**（系统给 agent
   下了一条「调用 X 工具」的指令，而 X 是否挂载是条件性的、两端零校验，于是 agent 收到
   一条它不可能执行的命令而无人报错）、**A-15**（agent 停在一个没有人能批准的审批上）、
   **A-17**（执行面进程根本没在跑，而界面一路显示「第 1/1 批执行中」）、**A-18**（agent
   写明「我什么都没执行过、合并前请重跑」，界面渲染成绿色的「已交付」）。
   前五例的共同形状是**状态机说成功，物理世界没发生**——系统自己也不知情；**A-18 是另一
   种：系统完全知情**（agent 写下了 blocker，交付端也确实按规矩把它拒了），**只是知情的
   两端都没有出口**——收据不渲染，拒收被吞。于是界面上是两个绿勾加一个永不推进的空
   change_set。验收必须落到最外层的真实副作用——仓库分支、对象存储里的字节、容器日志里
   的动作、**收据里 agent 自己说的话**——不能停在读模型的状态字段。
11. **验收自己也会看到旧渲染**（本轮救了我三次，建议置顶）：㉖ 落地后我第一次查 DAG 节点，未验证
   标记**没有出现**，差点记成「前端未生效」；刷新后标记就在。原因是这张页面自述过的
   「本页没有轮询」——**我导航进来看到的是上一版数据**。教训：**核对读模型与界面是否一致
   时，必须先确认界面这一版是什么时候取的数**；否则验收者会用「界面绿」的同一个坑，去
   误判一个其实已经修好的缺陷。**本轮命中三次**：①未验证标记（差点记成「前端未生效」）；
   ②失败节点首查显示 `pending`；③billing 节点首查显示 `running` 而其 run 已 failed。
   三次都是「先刷新再判断」把误判挡住的。
12. **好文案是把判断权还给人**（S26 重新派工弹窗，本轮质量标杆）：该弹窗把「会不会写」
   「写什么」「谁判定的」三件事分开讲清——两档单选、第二档明写「**这一条是真的写**，
   它们已记录的结论会被清掉」、计数与任务清单带 agent 与时间戳、并注明「服务端按后端
   状态判定，不是界面挑的」以及「**界面不替你判断『卡住了』**——只要还有未完成的任务，
   这个入口就在，用不用你说了算」。对照本轮那些「绿勾但什么都没发生」的地方，这段文案
   是反例：**它既不替用户乐观，也不替用户决定。**
14. **同一件事，靠自觉会失败，靠指令会成功**（本轮最有产品价值的对照，绿链的钥匙）：两个
   夹具仓 `main` 上都埋着同一个多币种失败测试。**任何 agent 无论被派来做什么需求，都必须
   先修好这个它没被要求修的既有失败**，否则 `run_tests.py` 必红、交付永不放行——而任务
   文档里没有一个字提到它。前三次尝试全靠 agent 自己读 README、自己发现、自己顺手修，
   结果是**成一次、败两次**。第四次把「同时修复既有失败测试」写进**需求文本**，它经
   发现四步 → 计划 → 任务文档全链传导到 agent，**agent 就照做了，测试绿、分支落地**。
   **同仓、同红、同一个模型，自觉失败、指令成功。** 推论有二：a) 任何依赖 agent「顺手
   做点没被要求的事」的设计，都是在赌硬币；b) **把隐含前提写成显式指令，是最便宜的修复**
   ——这条同时验证了「物化期把已知基线红写进任务指令」的修法方向。
15. **测基线要在没人动过的地方测**（验收自身的第三次失误）：我曾据一次实跑断言「billing
   基线是绿的」，据此选定了整条终局路径——**而那个工作区属于另一个任务，agent 已经改过
   七个文件并顺手修了 currency**。两仓其实埋着同一个红。这是方法论第三条「测到的≠想测的
   对象」发生在验收人自己身上：**取基线必须取未被修改的副本**。本轮验收自身共三次失误，
   均已原位记录（`querySelector` 误建 issue、旧渲染险些误判已修好的缺陷、此条），**保留它们
   正是这份报告可信度的来源**。
13. **正确的拒绝也要说出来**（A-18 勘正后的核心教训）：交付端拒收无测试结果的证据是**对的
   判断**，但它以「后台异常被吞」的方式执行，等价于没发生过。**一个没有出口的正确判断，
   在用户眼里与故障无异**——它甚至比放行更难诊断，因为界面上每一格都是绿的。凡是会终止
   流程的判断，都必须落成可见状态（谁拒的、拒什么、补什么能继续），而不只是抛一个异常。
9. **一次性事件死角：本轮最有分量的架构级发现（五例同族）**。这条链上每一次「跨系统副
   作用」，只要被实现成**一次性事件**而不是**可收敛状态**，都在本轮以「界面说在进行、
   物理世界停住」的形式暴露了一次。五例及其修复：

   | # | 副作用 | 死角形态 | 修复 |
   | --- | --- | --- | --- |
   | 1 | **派工点名**（Matrix） | 发出即消耗；收件人容器出生更晚 → 永远收不到（A-13） | ㉕ 重新派工入口（重发包+重投点名） |
   | 2 | **任务包发布**（对象存储） | 重放认领了任务行却跳过发布，报 `materialized` 而包不存在（A-10 重放半） | ㉓ 重放补写任务包 |
   | 3 | **测试命令注入** | 物化期一次性烘焙；修复上线前的轮次重派也拿不到（A-19 衍生 C 类） | b 线移到派单装配期解析 |
   | 4 | **路径白名单** | 同上；且**权限可在派单期重装，指令不能**——agent 不会用它不知道自己拥有的权限（A-21 边界注记） | ㉗ 派单期并集，文档层留待设计轮 |
   | 5 | **开 PR** | 分支已推真仓、PR 未开；reconciler 对无 PR 候选**直接 `continue`**，两个触发源都不会再来（A-24 之后的续发缺口） | ㉚ 巡检补「完成缺失的 PR」 |
   | 6 | **undraft** | **CI 已绿而 PR 永远停在 draft**：`undraft_when_allowed` 的唯一调用方是 `observation_processor:136`——**webhook 事件路径**；而本环境无 webhook，CI 状态是 reconciler 轮询自己读回并直接落账的（绕过 processor），**于是 undraft 永远没人调**（8100 日志零 `undraft`/`ready_for_review` 记录佐证）。活体标本 S33 | ㉚ 续修（同一接缝加 undraft 步，幂等；并顺带核「gate 等非 draft、undraft 等 gate」是否构成死锁） |

   **共同界面面孔**：一个不说明在等什么的 `waiting` / 「门禁运行中」（A-18 第六面）。
   **第 6 例还多出一层**：它挂的不只是「一次性触发」，而是**一条这套部署里根本不存在的
   触发通道**（webhook）——同一份 CI 状态，轮询路径读得到、事件路径永不到达，**功能因此
   悬在一条从未接通的线上**。判别法：问「这个动作的调用方是谁」，如果答案是某个事件
   处理器，再问一句「这个事件在本部署里真的会来吗」。
   **共同规律**：**产线的每个跨系统副作用，要么幂等可重放，要么有收敛巡检；一次性即失踪。**
   对照组同样清楚——**执行面的派单从一开始就是可收敛状态**：19:15 创建的派单等了 100
   分钟，Runner 一上线照样被正确认领执行。设计一次跨进程交接时，先问它是事件还是状态。
10. **配置随部署形态漂移，而没有任何一层守护**（A-17 后半）：Runner 的路径前缀映射是
   「API 在容器里」年代的配方，部署形态改成「API 在宿主」后它每单都判不可解析、静静
   放掉，只续租不开工。**症状是「安静地续租」而不是报错**——这类漂移不会自己喊疼，
   只能靠对账（派单在租、事件流水为空 = 有人租了活却没干）发现。
7. **子串匹配的安全规则会咬自己的命名规范**（A-15）：`TOOL_CMD_DANGEROUS_RM` 以子串匹配
   `rm`，而本系统所有资源名都是 `rm-` 前缀，于是每一次 `cat …/rm-worker-…/…` 都被判成
   危险删除。**命名空间与安全词表要一起设计**；规则用词边界，不用子串。
8. **两端各说各话的配置**（A-14 系统性观察）：能力挂载是条件性的（url 空则静默跳过），
   而指令文案是无条件的。任何「A 侧可选、B 侧必需」的配对都需要一道一致性校验，否则
   缺失只会在最远端以「什么都没发生」的形式出现。
