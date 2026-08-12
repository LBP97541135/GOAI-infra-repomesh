# 全流程闭环终态实机验收报告（2026-08-12）

- 文档状态：**验收中断待复走**——主链在物化环节被 A-1 阻断（见 §3），其余批次全部实走完毕
- 验收人：验收检测2（独立验收会话，只验收不修改；本文件是该会话唯一写入物）
- 验收对象：`feat/console-v2`。**开走基线 = 9290061**；验收中途用户裁决「取消登录门」
  合并入库（**00d33a2**，纯前端+文案，8100 未重启），此后帧均为无登录门形态，
  之前少量带 jack1 登录态的帧标注「变更前形态」
- 验收判据（用户定，两条）：①纯 GUI 跑通全流程闭环——凡不能通过 GUI 完成的动作与
  行为闭环一律视为缺陷；②一键打开功能可用
- 方法论：亲自实证不采信转述；写动作前后固定 pre/post curl 对照（只读留档，非操作路径）；
  只报告不修改；未实走路径如实列举

## 1. 环境与形态

| 项 | 值 | 验证方式 |
| --- | --- | --- |
| 前端 dev | http://127.0.0.1:5280（vite，默认数据源 live） | 浏览器实走 + 页脚自述 |
| 后端 API | http://127.0.0.1:8100/api/v1（uvicorn，单 worker） | curl 实调 |
| 数据库 | cons-live-pg@5533，迁移头 0023 | 经读模型间接验证 |
| 读模型鉴权 | `Authorization: Bearer console-dev-token` | curl 200 |
| 身份 | **无登录门（00d33a2 起）**，默认管理员 · 管理员 | 侧栏亲证；验收人未输入任何凭据 |
| LLM | DeepSeek（shell env `DEEPSEEK_API_KEY`，8100 继承）——发现链四步走真模型 | 分析/评分/分类结果实证 |
| SCM 凭据 | **未配置**：`.env` 无 `REPOMESH_GITHUB_APP_ID`/`WEBHOOK_SECRET`，无 `.secrets/`；`REPOMESH_DELIVERY_AUTO_ENABLED` 未设（默认 false） | env 名清点（值未读） |
| 活体边界 | 5432/8000/agentteams 容器全程未操作 | — |
| 截图链路 | 前段=应用内 Browser 面板，后段=chrome-devtools（用户指示切换） | 图像存于验收会话记录 |

**环境边界声明**：无 GitHub App 凭据且 delivery_auto 关闭，推分支/PR/CI/merge 执行器/
回滚 saga 真执行均不可达，按主脑口径记「环境边界」不记缺陷。配齐
`REPOMESH_GITHUB_APP_ID` / `REPOMESH_GITHUB_APP_PRIVATE_KEY_FILE` /
`REPOMESH_GITHUB_WEBHOOK_SECRET` 并以 `REPOMESH_DELIVERY_AUTO_ENABLED=true` 重启 8100
后，B4 后半与 B5 的 saga 落地可复走。

## 2. 验收清单

### A. 一键打开（判据②）

| # | 项 | 结果 | 证据 |
| --- | --- | --- | --- |
| A1 | `dev-up.sh --no-browser` 重入路径 | √ | 四阶段逐项「已在提供服务，跳过」，直达 URL 摘要；零副作用（容器 uptime 未断、工作树未动）；输出全文留验收会话 |
| A2 | compose console 冷路径（**首次实跑**） | √ | `REPOMESH_CONSOLE_PORT=8180 docker compose --profile console up -d --build` 一次通过：console-postgres/console-api/console-web 三容器 healthy，console-api 启动自迁移成功，8180→200。**界面（重建后新形态）**：直入控制台、默认管理员、空态诚实（Open 0 / Closed 0 /「没有进行中的 issue」/ live 页脚——私库无种子，空是对的） |
| A2 收摊 | `down -v` 作用域 | √ | 先 `--dry-run` 实证只删 `repomesh-console-postgres` 卷；实际执行只移除 console 三容器+该卷，平台栈（api/postgres@5432）无损，共享网络因在用被正确保留 |
| A1 冷路径 | dev 档从零起 | **未实走** | 本机不可安全实走：见缺陷 A-2——8100 停着时脚本会「收养」本机活体 5432（同 compose 项目的 postgres 服务）并对其迁移，而该库谱系与本分支不符。冷路径的合法实证即 A2 的 compose 孤立栈 |

### B. 全流程 GUI 闭环（判据①，全程只用界面；curl 仅作 pre/post 对照取证）

主链 issue：`a2c0c2f9-2219-55b9-ba40-4c1e92176005`「结算流程支持优惠码…」（对 e2e
多仓有真实跨仓影响的需求，GUI 新建）。

| # | 项 | 结果 | 证据（实读值） |
| --- | --- | --- | --- |
| B1 | 新建 issue 真创建 | √ | 弹窗提交 → 详情页直出 `#a2c0c2f9`；处理者「AGENT rm-org-leader-e2e-fix-verify」花名册派生；上一轮 B-1 缺陷（只 toast 不创建）已修复成立 |
| B2-1 | 需求分析（真 LLM） | √ | 判定「充分（confidence 0.90）」+ 7 关键词；步进器 1✓→2 当前，与 `GET /discovery` step=2 一致 |
| B2-2 | 候选评分 | √ | 4 候选：checkout 0.95 / billing 0.90 / api 0.85 / client 0.60，「LLM 评分」徽标、评分条、**rationale 英文原文原样展开**（不摘要不美化） |
| B2-3 | 三档生成+行内改档+审批 | √ | api/billing/checkout=required、client=maybe（各带置信+完整判据）；**缺失依赖诚实呈现**（模型点名 catalog 外的 `repomesh-e2e-pricing`，界面挂「缺失依赖」与「模型补充进来的仓库」不吞）；行内改档 client maybe→excluded，界面「已由审批人调整 · 模型原判 可能」+「待提交: 排除」+「本次改档 1 项」；批准后**改档留痕**（可能→排除 · AGENT · 时间戳）与**批准记录**（意见原文）上屏；`approval.evidence_version` 精确绑定界面指纹 `e8578f9b`；`effective_tiers` 记 `adjusted:true, original_tier:maybe` |
| B2-4 | 生成计划 | √ | 「计划已生成 v1 · 3 个任务节点 · 1 个执行批次 · 3 份接口契约」与读模型 `integration {3,1,3}` 一致；「物化并开工」按钮出现并自述「第二个不可逆动作」 |
| B2-DAG | 计划 DAG 泳道渲染 | √ | PLAN DAG 纸面：「3 节点 · 1 批次 · 2 条依赖边」，批次 1 泳道 api→billing→checkout，checkout 锚点仓徽标；图例（锚点仓/计划内仓库/未解析）；四条诚实脚注（未着执行态色原因、边只来自 `task_dag.depends_on`、丢弃依赖只进服务端日志、`graph_edges` 恒空另立项）全部在位 |
| **B3** | **物化并开工** | **× A-1** | 确认弹窗数字正确（快照 v1 · 任务 3 · 团队 3 · 主体派生 · 不可逆文案）；点击确认 → **`POST …/discovery/materialize` HTTP 500**，界面诚实回显「服务端拒绝 + 端点 + 状态码」。失败干净：issue 聚合 0 轮次/0 仓、rooms 0、teams 零引用——无半执行状态。**非契约 §8.2 三类 409 中任何一类** |
| B4（主链） | 新 issue 执行观测 | **受阻于 A-1** | 未实走 |
| B4（旁证） | 执行面读模型（种子 B `9129f894`） | √ | DAG 按执行态着色（checkout 节点印 `succeeded`，图例「执行态 · 第 1 轮」+ 6 态色系+「本页没有轮询」自述）；teamRoom 房间流：message 带头像气泡、RUNNER/门禁系统条目无头像+「控制台投影，非房间内真实发生」脚注（§5.2 合规）；环境窗：状态/变更文件/commit/CHANGESET 本仓位置/PR console-demo#7/基线快照 b0626c42；事件时间线四 kind 过滤（MATRIX/RUNNER/GATE/PLAN 各条目在） |
| **B5** | **GUI 回滚（整 change set）** | **√** | 种子 B 轮 `9129f894`（唯一有已发布候选的交付）。轮次卡展开「交付 · 1 个仓库有已发布候选可撤销」+「回滚…」；对话框与契约 v0.1 §4.6 及 GUI 设计定稿④逐项吻合：**范围表**（repomesh-e2e-checkout · 未 merge · withhold 免费撤回 · 逆序第 1 步 · PR #7）、琥珀条不许诺一键还原（revert PR 要过 CI/冲突转人工任务/未 merge 才免费）、回滚理由必填、决策主体派生（console-demo-org-leader，随 issue 组织正确切换）、确认框不勾则提交禁用 |
| B5 对照 | pre/post 三项 | √ | pre：merge_gate `{allowed:true, reasons:[]}`、决策仅 1 条 ready、recovery_in_progress=false。post：merge_gate **`{allowed:false, reasons:["an active recovery plan is incomplete"]}`**、新增 **`rollback_required`** 决策（head-bound `c1a0b2c3d4e5…` 全长、decided_by `7f181c57`（派生主体）、理由原文入档、09:12:29Z）、recovery_in_progress=**true**。GUI 侧即时变化：决策夹长出「关注 · 修复观察：repomesh-e2e-checkout」、交付行变「已有恢复计划在执行」、ROLLBACK_REQUIRED 决策卡上屏 |
| B5 备注 | `gate_display` 仍 "open" | 合规非缺陷 | §5.3 映射交付 status（merge_requested→open），与 merge_gate 是两个投影，亲核契约后判 |
| B5 saga | withhold 真执行（close PR#7） | 环境边界 | 需 delivery_auto=true + GitHub 凭据；恢复计划留「未完成」态属预期 |
| clarify | 追问回路 + 强行继续留痕 | √ | 故意含糊 issue `de2973ab`「把系统整体优化一下…」→ 判定「不充分（confidence 0.10）· 3 条追问」+ 缺失维度（业务场景、行为描述、变更类型）+ 3 条可答复追问框（留空即不回答）；「忽略 3 条追问，强行继续」按钮自述诚实（永久留痕+写审计事件+**不重跑模型**+拼接规则在服务端）；点击后服务端 `forced_continue {at, by_agent_id, ignored_question_count:3}` 落库、GUI 琥珀留痕「已强行继续 · 忽略 3 条追问 · AGENT · 时间戳」、步进器按 §3.2 规则 2 放行到 2。该 issue 就此停住（验证产物） |

### C. 横切核对

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 步进器位置=读模型 step（前端零自判） | √ | 每步实测：1 idle→2 idle→3 idle→4 idle→4 done→（clarify issue）1 idle→2 idle，GUI 与 `GET /discovery` 逐一吻合；面板自述「本面只渲染不自判」属实 |
| 空态/失败态诚实 | √ | 草稿 issue 的范围/DAG/房间三区块各有说明文字；DAG 锚点回退时 404 双义如实写明「服务端把两者写成同一个 404，界面无从分辨」；materialize 500 原样回显端点+状态码 |
| 错误显示服务端原文 | √ | 「服务端拒绝 POST …/materialize → HTTP 500 · Internal Server Error」 |
| 上游重跑作废下游 | 文案在位，未实走 | 每步下方「重跑本步会作废其下游步骤（契约 §4.4）」；实际重跑未走（避免消耗主链状态） |

## 3. 缺陷清单

分类沿上一轮：**A 类 = 功能存在但坏了**；**B 类 = 闭环缺失**；**C 类 = 可选优化**。

### A 类

| # | 缺陷 | 实证 | 影响 |
| --- | --- | --- | --- |
| **A-1** | **物化端点对发现链亲产的计划快照返 500** | `POST /issues/{id}/discovery/materialize` → HTTP 500（非 §8.2 三类 409）；前置全绿（step=4 done、approved、integration {3,1,3}）；失败干净无半执行。触发形态特殊性：这是**第一个由发现链四步在活体产出快照再喂物化**的案例——既有种子 issue 的快照来自 seed 脚本/run_pipeline 路径。8100 日志在起它的会话手中，traceback 已向主脑索取（发稿时未达） | **阻断判据①**：prompt→merge 的 GUI 链在物化处断裂；B3/B4 主链不可走 |
| **A-2** | **dev-up 启动器会收养并迁移不是自己起的库**（安全守卫与自身声明不符） | 代码级实证（未实走触发以保护活体）：`scripts/dev-up.sh:150-153` 以 `docker compose ps … | grep -qx postgres` 判「compose 的 postgres 在跑」即 `own_database=1` → 对其 `alembic upgrade head`。守卫只防非 compose 进程占端口，防不住**先于脚本存在的同项目 compose 库**——本机活体 5432 恰是该形态，谱系与本分支不符。与脚本头部自述「never … migrates into anything it did not start」直接矛盾 | 8100 停着时在本机跑 dev-up = 对活体库迁移。建议修法：迁移前比对 `alembic current` 与本分支迁移链，或无 `postgres.started` 状态文件时要求显式确认 |

### B 类

本轮实走范围内**未新增 B 类**（上一轮 B-1 issue 写端点已修复成立并实证；B-4~B-10 未复测，仍以上一轮报告为准）。

### C 类

| # | 项 | 实证 |
| --- | --- | --- |
| C-1 | 分类结果的补充仓名未归一化去重 | `classification.supplemented_repos` 存 `repomesh-e2e-pricing` 与 `repomesh-e2e-pricing (not in candidate list)` 两个串（LLM 括号注记漏进仓名）；一条候选的 missing_dependencies 同样携带注记串。curl 实证为服务端数据，前端诚实渲染出重复。主脑已确认入册待修 |

## 4. 未实走路径（如实列举）

1. **B3→B4 主链**（物化→建团→派工→执行观测→新 issue DAG 着色）——A-1 阻断，修复后须复走；
2. **dev 档一键冷路径**——本机不可安全实走（A-2），compose 档冷路径已代之实证；
3. **推分支/PR/CI/merge 执行器/回滚 saga 真执行**——环境边界（无 GitHub App 凭据、delivery_auto 关）；
4. **上游重跑作废下游**（§4.4）——文案与契约在，实走未做（避免消耗主链快照状态）；
5. **多批次 DAG 泳道**——本轮两个计划都只有 1 个批次，跨列依赖边形态未见；
6. **失败态着色**（failed 赭红）——实走中无失败任务；
7. **回答追问并重新分析**（clarify 的 a 出路）——只验了强行继续（b 出路），答复拼接重分析未走；
8. **replay 模式**——本轮验收对象是 live；
9. **审批/物化幂等重放与 409 族**——materialize 的三类 409、approval 的指纹漂移 409 未构造。

## 5. 验收产生的数据（种子重置清单）

| 对象 | 内容 | 处置建议 |
| --- | --- | --- |
| issue `a2c0c2f9` | 优惠码主链：发现四步全走完 + 已批准分档（含 client 改档留痕）+ 计划 v1；**物化失败无执行面残留** | 随种子重置清理 |
| issue `de2973ab` | clarify 验证：不充分判定 + forced_continue 留痕 | 随种子重置清理 |
| issue `9129f894`（种子 B） | **已被本验收改性**：新增 1 条 ROLLBACK_REQUIRED 治理决策 + 1 个 OPERATOR_REQUESTED 恢复计划（未完成态，saga 因环境边界不会执行）；决策夹出现 watch 项 | 种子重置时须一并复位；在此之前该 issue 不再是「唯一 approve 待放行」锚点形态 |
| issue `e6b251db` | 「提交空文本…」——**非验收产物**（登录门拆除期间出现，来源他处），如实备案未动 | 待主脑核 |
| compose console 栈 | 已 `down -v` 清理，无残留 | 无需处置 |

## 6. 截图取证（图像存于验收会话记录）

| # | 内容 | 关键可见证据 |
| --- | --- | --- |
| S1 | 登录门（变更前形态，已被用户裁决取消） | 用户名/密码表单 + bootstrap 入口——历史留档 |
| S2 | 新建 issue 弹窗（变更前形态帧） | 派生处理者 + 需求文本 + 创建按钮 |
| S3 | B2-1 完成态 | 「充分 0.90」+关键词；步进器 1✓ 2当前 |
| S4 | B2-2 评分展开 | 4 仓评分条+分数+rationale 英文原文 |
| S5 | B2-3 改档待提交 | client「待提交: 排除」+「本次改档 1 项」+指纹 e8578f9b |
| S6 | B2-3 批准后 | 改档留痕「可能 → 排除」+ 意见原文 + 上次审批绑定 e8578f9b |
| S7 | 四步全✓ +默认管理员（无登录门形态，chrome-devtools 补拍） | 步进器 ✓✓✓✓；身份区「默认管理员 · 管理员」 |
| S8 | 计划 DAG 泳道 | 3 节点/1 批次/2 依赖边、锚点仓、图例、四条脚注 |
| S9 | 物化确认弹窗 | v1 · 任务 3 · 团队 3 · 主体 · 不可逆文案 |
| S10 | B4 旁证：teamRoom+环境窗+事件时间线 | 头像/系统条目之别、PR #7、基线快照、四 kind 过滤 |
| S11 | 回滚对话框 | 范围表全行 + 琥珀条 + 派生主体 + 禁用的提交按钮 |
| S12 | 回滚后决策夹+轮次卡 | 「关注·修复观察」+「已有恢复计划在执行」+ ROLLBACK_REQUIRED 决策卡（head/主体/理由原文） |
| S13 | A2 冷启动控制台（8180，重建后） | 直入+默认管理员+空态诚实+live 页脚 |
| S14 | clarify 追问面板 | 「不充分 0.10 · 3 条追问」+缺失维度+答复框+强行继续按钮及其诚实自述 |
| S15 | 强行继续留痕 | 「已强行继续 · 忽略 3 条追问 · AGENT · 时间戳」+步进器 1✓ 2当前 |

瞬态备注：物化 500 的「服务端拒绝」行以 a11y 树实读留档（弹窗内 `服务端拒绝
POST …/materialize → HTTP 500 · Internal Server Error`），未单独成帧。

## 7. 总结论

**不可进入推送。** 判据②（一键打开）两档全过——compose 冷路径首跑即通过是本轮
重要正面结论；判据①（纯 GUI 全流程闭环）的八个环节中七个实走通过（建 issue、
发现四步、审批改档、生成计划、DAG 渲染、执行面观测、GUI 回滚闭环），但 **A-1
（物化 500）恰好断在把计划变成执行面的那一跳**，prompt→merge 的完整链条不成立。
A-1 修复并复走 B3→B4、连同 A-2（启动器安全守卫）修复后，方可再判推送。

契约红线（状态映射唯一实现、诚实数据、§5.2 系统条目、§3.2 步进器判定、head-bound
治理、§4.6 回滚范围表）在实走全程零违例——断裂是实现缺陷，不是设计缺陷。
