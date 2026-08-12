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
| B3 | 物化并开工 | **√（第三走）/ 途中揪出 A-1·A-3·A-4** | 确认弹窗数字三次全对（3任务3团队/2任务2团队×2 · 主体派生 · 不可逆文案）。第一走 500=A-1（跨组织异常未翻译）；第二走 500=A-3 首触发（REQUIRED_CHECKS 请求期爆炸→半执行）；**第三走（35e66beb）物化 200 成功**：轮次 1 + 仓 2 + 团队 2 落库 |
| B3-负路径 | 跨组织物化拒绝 | √（A-1 关闭证据） | ba7e827 + REQUIRED_CHECKS 修正后，GUI 重放 a2c0c2f9 → 弹窗「服务端拒绝 … **HTTP 409 · repository 48ff85ee… already has a leader in another organization (d68a5926…); it cannot join this project's topology**」原文透传（截图 S16） |
| B4 | 执行观测（新 issue） | **待复走** | 35e66beb 团队 runtime_status=pending、任务 0、房间 0——全部堵在宿主→AgentTeams 控制面通路（§1 三重墙）后面；sidecar 修复在途。复走判据之一：**pending 团队在通路恢复后应被 reconcile 自动收敛**（不收敛=新缺陷） |
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
| A-3 | **materialize 非原子：失败留半执行状态** | 两条触发路径实证：①REQUIRED_CHECKS 缺失 → `container.py:1002` 请求期 RuntimeError；②AgentTeams 房间未就绪 → `collaboration._route CollaborationRouteUnavailable` 抛穿。两者都发生在轮次/plan 已落库之后——**§8.2/8.3 的「失败不留可重放收据」只保护了收据，没保护轮次行**。标本：`5c1b3567`（轮次 1+仓 2、任务 0 房间 0）。连带发现：容器工厂配置错误在请求期才爆、以 500 示人（启动期校验缺失，主脑已入 backlog） | 修复在途（派工进重试队列；判据=同 issue 重放收敛到完整状态），标本保全 |
| A-4 | **每个刚物化的轮次杀死 issue 详情页** | 全链：读模型对新轮次投影 `updated_at:null`+`plan_version:null`（curl 双标本实证）→ `RoundsPanel.tsx:107` `dayLabel(round.updated_at)` → `display.ts:151` 对 null 调 `.match` → TypeError → **无错误边界，SPA 整树卸载**，hash 导航救不回须整页刷新。**产线主流程必踩**（物化后到首个活动 stamp 前该 issue 页必死）；种子轮次全带时间戳，故此前历轮验收未暴露 | 修复在途（空值容忍+区块级错误边界；判据=5c1b3567 活标本页面恢复可达且半执行态诚实呈现） |

### B 类

本轮实走范围内未新增 B 类；上一轮 **B-1（issue 写端点）与 B-2（工作区列表/切换）修复成立**并在本轮实证。其余 B-3~B-10 未复测，仍以上一轮报告为准。

### C 类

| # | 项 | 实证 |
| --- | --- | --- |
| C-1 | 分类补充仓名未归一化去重 | `supplemented_repos` 存 `repomesh-e2e-pricing` 与 `repomesh-e2e-pricing (not in candidate list)` 两串（LLM 括号注记漏进仓名）；服务端数据，前端诚实渲染出重复。主脑确认入册 |
| C-2 | 「全部工作区」下建 issue 隐式落入非预期组织 | a2c0c2f9 在「全部工作区」创建，处理者静默派生为 e2e-fix-verify 的 org leader——用户无从预期 issue 会落进哪个组织（本例直接造成跨组织不可物化）。建议：全部工作区下建 issue 时要求显式选组织，或弹窗内明示目标工作区 |

## 4. 未实走路径（如实列举）

1. **B4 主链**（AgentTeams 建团→派工→runner→推真仓候选分支→PR→CI `tests`→merge gate）——宿主→控制面通路修复在途；
2. **B5 saga 真执行**——依赖 B4 产出真 change set；
3. **5c1b3567 幂等重放**——主脑明令暂缓（§8.3 收据在半执行下的行为正是修复代理要查清的）；
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
| `35e66beb`（console-demo） | **物化成功标本**：轮次 1+仓 2+团队 2（pending） | **保全勿动**——通路恢复后 reconcile 自动收敛的验证件 |
| `de2973ab` | clarify 验证：不充分判定+forced_continue 留痕 | 随种子重置清理 |
| `9129f894`（种子 B） | 已改性：+ROLLBACK_REQUIRED 决策+恢复计划（占位 URL 上不会真执行） | 种子重置时复位；不再是「唯一 approve 待放行」形态 |
| `e6b251db` | 空文本 issue，非验收产物（曾对 materialize 打过三次 500，来源待查） | 主脑已入清理清单 |
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

瞬态与白屏备注：materialize 500 两次、A-4 白屏（空 a11y 树+console TypeError）以
DOM/console 实读留档；A-4 崩溃页面无帧可拍（白屏本身即证据形态）。

## 7. 总结论（待复走更新）

**当前判定：不可进入推送。** 判据②全过（compose 冷路径首跑即通为本轮重要正面结论）；
判据①：发现链、审批、计划、DAG、回滚语义、clarify 全部实走通过且契约红线零违例，
但 **A-3（物化非原子）与 A-4（新轮次杀死详情页）落在产线主动脉上**，且 B4 执行观测
被宿主→AgentTeams 通路阻断未走。三项修复（派工重试队列 / RoundsPanel 容错+错误边界 /
sidecar 通路）落地后复走 B4 + B5-saga，再作终判。

方法论沉淀：本轮四个 A 类中三个（A-1/A-3/A-4）都只在「发现链亲产数据 + 全新轮次」
的形态下触发——种子数据是熟路，**终态验收的价值恰恰在走生路**。
