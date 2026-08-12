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
| B4 | 执行观测（派工→runner→真仓分支→PR→CI→merge gate） | **未达；到达边界=「任务实体+房间齐备，但任务包未投递」** | 阻断层层剥离共十层，逐层实证逐层修：①宿主→控制面三重墙（⑮ sidecar，`/health/ready` 503→ready）→ ②**B-11** 运行时投影零调用点（⑱，房间首次真长出）→ ③**A-6** 派工异常裸 500（译 503）→ ④runtime/镜像配对坏死（`f16925c`，openclaw→copaw）→ ⑤**A-8** 仓库 leader 争仓 400 穿 503（㉑ 收养语义）→ ⑥**A-9** 巡检迟到一整圈（僵尸清理+守卫）→ ⑦MinIO 凭据转义错（环境，主脑自首）→ ⑧**A-10 发布半** S3 异常裸 500 → ⑨**A-10 重放半** 认领任务却不补写任务包、不重投点名 → ⑩观察哨 c：worker 容器侧 mc 对 storage `Access Denied`（威胁后续交付物上传，未立号）。**到达点**：任务行落库、双房间齐备、GUI 显示「执行中」；**未发生**：任务包写入 MinIO（`teams/…/shared/tasks/` 只有 `.keep`）、新点名消息、worker 动工、runner、候选分支、PR、CI `tests`、merge gate |
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
| A-12 | **运行时投影不收敛：`runtime_status` 停在 pending，而房间、容器、派工全已就位** | 成功物化那次（13:51:48）明明跑了 reconcile，`project.repository_agent_teams.runtime_status` 仍为 `pending`，同时 `room_id`/`leader_room_id` 双房间落库、worker 容器 Running、派工消息已达。GUI「关联仓库 · 团队」因此写「团队待建」，与同页「房间」区块渲染出的真实双房间自相矛盾。此现象初见于 A-6 期（当时判「修活后应自然收敛」而未立号），本轮**实测否定了该预期** | 入册待修（疑 reconcile 写 `runtime_status` 的条件或时机有缺口）；不阻断 B4 |
| A-4 | **每个刚物化的轮次杀死 issue 详情页** | 全链：读模型对新轮次投影 `updated_at:null`+`plan_version:null`（curl 双标本实证；**数据源头=A-5**）→ `RoundsPanel.tsx:107` `dayLabel(round.updated_at)` → `display.ts:151` 对 null 调 `.match` → TypeError → **无错误边界，SPA 整树卸载**，hash 导航救不回须整页刷新。**产线主流程必踩**（物化后到首个活动 stamp 前该 issue 页必死）；种子轮次全带时间戳，故此前历轮验收未暴露 | 修复在途（⑯：空值容忍+区块级错误边界+「刚物化·尚无活动」与「物化中断产物」两种诚实文案分开；判据=5c1b3567 活标本页面恢复可达且半执行态诚实呈现） |

### B 类

上一轮 **B-1（issue 写端点）与 B-2（工作区列表/切换）修复成立**并在本轮实证；其余
B-3~B-10 未复测，仍以上一轮报告为准。本轮新增一条（编号顺延）：

| # | 缺陷 | 现状（实证） | 缺失能力 |
| --- | --- | --- | --- |
| B-11 | **GUI 物化路径从不向 AgentTeams 投影运行时** | 注册 agent / reconcile 团队房间的代码只接在 `run_pipeline` 脚本，src 零调用点；控制器内 rm-team 系 4 agent/2 team 全不存在、room_id 恒 NULL（主脑诊断代理事实链）。GUI 物化建的团队永远停在 pending——**通路修好房间也不会长出来**，历史上「能跑」全靠脚本旁路 | **已修已关**。⑱（`b908fd1`/`977ce3c`）：materialize 在 start_plan 之前同步注册+reconcile，任何队缺房间→503 不半执行；契约 §8.7 转正。**活体证据**：96896557 两队 `room_id`/`leader_room_id` 落真 Matrix 房间 id（对照旧标本 35e66beb 仍 null），**GUI 房间区块第一次渲染出真实双房间**（teamRoom/leaderDM 各 2 成员，S20） |
| B-12 | **半执行轮次在 GUI 上没有任何重按物化的入口** | 复走第 1 步即撞：35e66beb 的发现面第 4 步之后不是按钮而是一行「本 issue 已物化…」。根因跨两层——服务端 §8.3 收据本就带 `status=failed`+error 原文，但 `GET /discovery` 读投影不透出它；前端 `DiscoveryPanel.tsx:862-888` 因此只能拿 `roundCount > 0` 一刀切换文案。后果=⑮ 给的可重入重放（`7659c89`）在 GUI 上无触发口，只能 curl。原 P1 第 7 条「重试入口前置字段缺失」的说法据此勘正（字段本就存在，缺的是投影） | **已修已关**。⑲（`ba2eec8`/`dfb6a51`）：读投影透出收据 + 前端按 status 分形态渲染；契约 §3.1.1 转正。**活体证据**：35e66beb 页面长出「重试物化」按钮，旁印**上次失败时间与原文**，并写明「重试会补完这一轮而不是另起一轮：服务端按 §8.3 认领上次留下的痕迹」（S22） |

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
6. 多批次 DAG 泳道（本轮计划均单批次）；失败态着色（无失败任务）；
7. 「回答追问并重新分析」（clarify 的 a 出路，只验了强行继续）;
8. replay 模式；审批/物化幂等重放 409 族（跨组织 409 已验，指纹漂移/重放族未构造）。

## 5. 验收产生的数据（种子重置清单）

| 对象 | 内容 | 处置 |
| --- | --- | --- |
| `a2c0c2f9`（e2e-fix-verify） | 发现链全走+批准+计划 v1；物化 409 负样本 | 随种子重置清理 |
| `5c1b3567`（console-demo） | **A-3/A-4 活体标本**：半执行轮次（轮次 1+仓 2+任务 0+房间 0） | **保全勿动**——A-3 重放收敛与 A-4 页面恢复的修复验证件 |
| `35e66beb`（console-demo） | **半执行标本**（勘正，非「物化成功」）：HTTP 200 但收据 `status=failed`；轮次 1+仓 2+团队 2（pending，room_id null） | **保全勿动**——A-8 修复（㉑ 收养语义）的验证件；复走已证其重放撞争仓 400 |
| `96896557`（console-demo） | **A-6 标本**：裸 500 半执行；轮次 `c6101abe` tasks 0；**两队 room_id 落真 Matrix 房间**（B-11 修复的活体正面证据同源） | **保全勿动**——A-8 的争仓占位方与 A-6 断点续上的验证件 |
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
| S23 | **执行面首次越过：425efbbc 物化成功** | 「第 1 轮交付 · 1 仓」「第 1/1 批执行中」+ 工程契约 `AE8312D7 V1 DRAFT` + 轮次卡「计划 v1 · 更新于 08-12」+ **DAG 节点着 `pending` 执行态色** + **teamRoom/leaderDM 双房间各 2 成员**（leaderDM 末条为 12:54 的 `task_assignment`——A-10 重放半的界面侧证据） |

瞬态与白屏备注：materialize 500 两次、A-4 白屏（空 a11y 树+console TypeError）以
DOM/console 实读留档；A-4 崩溃页面无帧可拍（白屏本身即证据形态）。

## 7. 总结论（待复走更新）

**当前判定：不可进入推送。** 判据②全过（compose 冷路径首跑即通为本轮重要正面结论）。
判据①：发现链、审批、计划、DAG、回滚语义、clarify 全部实走通过且契约红线零违例；
**物化本身已在活体上真正走通**（第七走 `425efbbc`：收据 `materialized`、任务实体落库、
双房间齐备、执行态着色生效）；**但执行面止步于「任务包未投递」**——worker 从未动工，
runner / 候选分支 / PR / CI `tests` / merge gate 五项均未发生（A-10 重放半，㉓ 在修）。

**已关闭**：A-1、A-3、A-5（改写）、A-9、B-11、B-12。**待修**：A-2、A-4 的余项无、
A-6（关闭候选，待终判正式关）、A-8（㉑ 已合并，三标本收养重放待验）、A-10（㉓ 在修）、
A-12（投影不收敛）、A-9b（manager 端口冲突）、C-1、C-2；观察哨 A-11（org leader 上行
消息 503）与观察哨 c（worker 侧 mc 对 storage `Access Denied`，威胁交付物上传）挂起。

终判前置：㉓ 落地 → `425efbbc` 重放补完 → 三标本收养重放 → B4 全链 → B5-saga。

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
6. **界面绿 ≠ 链路通**（A-10 重放半）：物化收据 `materialized`、GUI 写「第 1/1 批执行
   中」、DAG 着 `pending`——而任务包根本没写进对象存储、点名消息停在收件人出生之前。
   **状态机说成功，物理世界没发生**：验收必须落到最外层的真实副作用（仓库分支、对象
   存储里的字节、容器日志里的动作），不能停在读模型。
