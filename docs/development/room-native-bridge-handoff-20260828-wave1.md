# Room-Native Bridge 交接文档（波次 0–1 收口 + 波次 2 停车现场）

> 日期:2026-08-28
> 分支:`feat/room-native-agent-bridge`(main 之上 65 提交,未推送;头 `ccc74b65`)
> 状态:**波次 0/1 全部收口(V1 治理活体 PASS=M5、AC-03 关闭=M6 前半);波次 2 按用户指令停车,现场完整保留**
> 上一份交接:`room-native-bridge-handoff-20260827-pr5.md`(仍有效,其 §0.5 的 B 类「改码只有纸面」**已被本轮推翻——活体已证**)
> 工单台账权威:`room-native-bridge-final-acceptance/wave0-baseline-20260828.md`
> 读者:零上下文接手本线的工程师或 agent 会话

---

## 0. 从这里开始(接手第一屏)

```bash
git -C <repo> log --oneline -20         # 波次 1 的 19 个提交 + 契约基线
git -C <repo> status --short            # M/?? 都是他线的,别动
git worktree list                       # 三个代理 worktree,现场见 §5

# 门禁(全量约 8 分钟)
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q   # 期望见 §6 末行的数字

# 只跑本线快测(秒级)
.venv/Scripts/python.exe -m pytest tests/agent_bridge tests/runner -q -m "not packaging"
```

**当前一句话**:六大环节里「Worker 在场对话、受治理真改码、自动接单」已全部活体贯通;
停车等裁决的是 **S-1(服务端先宣告后授权的排序缺陷,稳态自动接单在它修复前 FAIL)**、
**W-A2 合入**(已验收未合)、**W-B2/W-C2 恢复**(一个接近完工一个零改动)。

---

## 0.5 达成度对照(对 PR 5 交接 §0.5 的增量修订)

| PR 5 交接的分类 | 本轮变化 |
|---|---|
| A 类「在场与对话已活体证」 | 不变 |
| **B 类「改码只有纸面」** | **已推翻**:V1(run `2c00225e`)与 AC-03(run `215216df`)两次真机全链——真 codex 在 Low IL 受限进程真改码、真 commit、仓库测试 exit 0、task=succeeded 走事件通道、房间四条叙事终态纯 evidence、**审批被策略逐条评估**(15A/3D 与 8A/0D) |
| **C-1「不会自己接活」** | **已关闭**(PR 6):平台任务包通知直接触发治理启动,零人工 UUID;但见 §3 S-1——**Bridge 在线时的稳态路径被服务端排序缺陷挡住,当前只有「重启续读」路径全通** |
| C-2/C-3/C-5/C-6(Windows-only、codex-only、backfill、问答闭环) | 不变,仍是档位 C |
| C-4「平台无在线态」 | 不变(契约有意) |

**新增的最重要认知**:PR 5 的八条治理验收自动化绿了 20 天,而真 codex 在治理轨下
从未成功执行过一次工具调用——V1 揪出 9 个缺陷(§3)。**「fake-only 覆盖的审批/协议代码
=纸面」是本轮最贵的教训。**

---

## 1. 波次 0 基线(全部 tracked)

- **契约基线 commit `e3eec185`**(所有波次 1 工单的 baseline):
  `contracts/agent-bridge/v2/`(enrollment/binding v2=v1+必填 `role`,org leader 结构性不可表达;6 fixture 含错误件;测试机检 v2/v1 零漂移+双向 round-trip)、
  `contracts/leader-actions/v1/`(assignment package 四相/plan/review decision/双 receipt/结构化错误 13 码/error-matrix;五条不变量 README 规范化+对共享 fixture 可执行钉死)、
  `project/contracts.py` 的 `TeamDecompositionMode`+`TeamDecompositionModeReader`。
- **迁移预留**:0037=PR5.5B、0038=PR7 纵切、0039=PR9、0040=W-A2(链序=合并序,主分支现链 0036→0038→0037,0040 在 W-A2 分支上 down=0037)。
- **E0a**:`.env` 已配 `REPOMESH_MODEL_API_KEY`(DeepSeek,实测 200);单 Worker 身份沿用 `repomesh-preflight-probe`,principal 钉 `4d1e6f00-...-0004`;全部凭据与开跑清单在 gitignored 的 `output/bridge-team/e0a-live-env.md` + `secrets/`。delivery 全程零配置。

## 2. 波次 1 工单与合入账(执行模式=fable 主脑裁决/验收 + opus 子代理工单制,逐单亲审+独立复跑+cherry-pick+每次合入独立全量门禁)

| 工单 | 内容 | 合入提交 | 状态 |
|---|---|---|---|
| W-B1 | PR 7 核心纵切:`_assign_batch` 按 mode 分叉(LEADER 停在派 leader task+落 planning 记录)、planning GET 全链(401/403×2/404/409 结构化错误)、迁移 0038、`LeaderDecisionLane` | `91b2e85e`/`492e715f`/`2b882089` | ✅ 合入 |
| W-A1a | PR 5.5A:external member v2 provision/preflight(新兄弟路由 `GET /api/v1/runtime/v2/external-members/{id}/binding?role=…`;role 真相源=directory;org leader 双 409;role-aware 房间;顺手修 leader skills 硬编码) | `41b5decb` | ✅ 合入 |
| W-A1b | PR 5.5B:adoption 一读一决(`_adopt_leader` 从既有 get_worker 读同时拿 team 名+containerManaged)、domain 单向闩锁(不降级)、迁移 0037(down=0038)、真 mode reader 换静态桩、前端 Teams 只标「Leader 自拆」 | `8f706382`/`71a74756`/`0b89f959` | ✅ 合入 |
| W-C1 | V1 治理活体 E2E:**PASS**(三处对账+治理活着);过程修复 6 提交见 §3 | `234222cc`~`b1e00c21` | ✅ 合入 |
| W-C1(PR 6) | 自动接单:`assignment_directive`(指令→命令→会话轨;共用幂等键与 `_start_governed`)+附属批 C-9/obs-1/2/3;**AC-03 四条全过** | `91c92080`/`314db5cf` | ✅ 合入 |
| W-A2 | PR 7 完整状态机:POST /plan(五族 clamp/节点键幂等派发/指纹先于相位)、review_due 快照证据、approve/rework/escalate 三路、迁移 0040 | `c43dfecd`/`b74f47fc`/`33596046`(在 A worktree 分支) | ✅ 验收、**未合入** |
| W-B2 | PR 8 底座(LeaderActionPort/HTTP+memory adapter/v2 消费/协调会话/cli role 门;supervisor 禁区) | 未提交 | ⏸ 停车,接近完工(§5) |
| W-C2 | PR 9(RoomTimelineIngest)+Q2(D-7 收窄) | 无 | ⏸ 停车,零改动 |

## 3. 活体揪出的缺陷账(9 条;⑧条已修一条待裁)

| # | 缺陷 | 修复 |
|---|---|---|
| D-1 | 工具词表错配:服务端 grant `{read,edit,test}` vs codex 审批项 `{commandExecution,fileChange}`(executor.py:127 按名判)→ 真 codex 全拒,无部署配置可解 | C-2:Bridge 侧词表翻译(只追加、只对 codex),`234222cc` |
| D-3 | codex 0.149.1 Windows fs sandbox helper 在 Low IL 必死(os error 5,审批之前) | C-3:governed codex-home 配 `sandbox_mode="read-only"`+关两个 windows sandbox feature(`57f93532`/`e38bd5df`);apply_patch 仍死但 codex 自回退 shell,获批命令脱沙箱落盘 |
| D-4 | worktree 打 Low 标签失败被静默丢弃 | C-4:spawn 前拒绝运行,走既有失败路径(`234222cc`) |
| D-5 | 权限决策零日志 | C-5:`_GovernedApprovalPolicy` 包装记录(含 in-workspace 路径,`cd81e691`)——本轮定位根因全靠它 |
| — | codex 审批项恒带 `cwd`=worktree 根,generic 策略当写目标 → 全拒 | C-7(c′):只剥「=平台自备根」的 cwd,其余原样受审(`6fd4e1f1`) |
| **C-8a** | **runner `_answer_approval` 词表 `approved/denied/abort` 从未上过真 wire,真 codex 只认 `accept/decline/cancel`**;request id 从 0 起(falsy 判断会吞首个审批) | **红线窄口一次**:活体探针捕获报文后修 `b1e00c21`,tests/runner 246→255(4 个钉死旧词表的断言字面量被迫同步);**窄口已关,runner 此后零触碰** |
| — | fileChange 审批按名恒拒(`_approval_tool_name` 回退 method 串) | C-9:`edit` 别名追加该 method 名,路径钳制保留(`314db5cf`) |
| obs-1/2/3 | 坏 config 锁死启动序 / 整写抹 codex 自写键 / 房间指路日志无日志 | PR 6 附属批全落(`314db5cf`) |
| **S-1** | **平台先投派活通知后写执行许可**(`task_orchestration/application.py:728` assign 一体建任务+投包+发房间消息,`:746` 才 `_ensure_specification`)→ Bridge 在线自动接单必落窗口,`SpecificationNotFound` 拒绝且按设计不重试,派活即丢(两轮复现,task 停 blocked 而 spec 事后 frozen) | **未修,待裁决**。修法方向=拆开「派发」与「宣告」(spec 以 task 为键,非换序两行);AC-03 取证走的是「Bridge 重启期间派活、cursor 续读」真实运维场景 |

**三条方法论教训**:①deny-all/审批/协议应答类代码若只有 fake 测试=纸面,第一个真实提问就塌;②「A 全过」与「治理活着」必须同一次运行同时验,分开各验会漏掉 danger-full-access 这类「全通但空转」;③活体排障的最大杠杆是先加可观测性(C-5 两次迭代把根因从「模型不干活」收敛到「cwd 触发路径规则」)。

## 4. 波次 1 关键裁决(细账在台账与各工单往来)

A-1 v2=新兄弟路由,v1 字节稳定|A-2 只加别名不改名|A-3 mode reader 读持久化不打活体|A-4 malformed token map=401+ERROR 日志(与 agent_runtime 503 不一致,记偏差)|A-5 rework 复用 `TaskOrigin.REWORK`(三消费者审计过)|A-6 evidence 进 review_due 一次性快照|B-1 生产默认 SERVER 静态 reader(5.5B 换真)|B-3 纵切含 HTTP GET 不含 POST|C-2~C-9 见 §3|**C-8a 红线窄口:一次、窄、以捕获为准、回归钉死、即刻关闭**|C-8c `.repomesh/**` 不进 allowed_paths(它同时是提交门禁)|D-2 实测非阻断(相对路径 shell 读被 allow)。
另:422=框架语义(冻结矩阵只辖 401/403/404/409);冻结契约在整个波次 1 **零修改**,没有一处需要动。

## 5. 停车现场(恢复时照此接)

- **W-A2(已验收未合)**:worktree `\.claude\worktrees\agent-ac465c0fee699f4c1`,分支 `worktree-agent-ac465c0fee699f4c1`,3 提交 `c43dfecd`/`b74f47fc`/`33596046`(基于 `b016f058`)。合入=cherry-pick 三个 + 迁移 0040 的 down_revision **无需改**(其分支含 0037)+ 全量门禁 + 台账。已裁决偏差已记台账(planRevision 恒 1、rework 回执 in_progress 不动行、许可回落包络根、save 无版本列)。
- **W-B2(接近完工未提交)**:worktree `\.claude\worktrees\agent-a38e6b03ce68112fc`(基 `842e1416`)。已 staged:`repomesh_binding.py`(v2 preflight client)/`cli.py`/`contracts.py`/`tests/agent_bridge/test_member_v2.py`;未 staged:`coding_session.py`/`ports.py`/`memory.py`/`adapters/__init__.py`;未跟踪新文件:`adapters/leader_actions.py`、`adapters/leader_session.py`、`leader_lane.py`、`tests/agent_bridge/test_leader_lane.py`。被停时全量套件在跑、正要分逻辑提交。恢复=给该代理发消息续做(它的 transcript 完整)或人工收尾。**supervisor.py 禁区已可解除**(PR 6 已合)——第二段(supervisor 集成)可以开单了。
- **W-C2(零改动)**:worktree `\.claude\worktrees\agent-a0eb6ff2878b836cf` 干净,在 `842e1416`。恢复=原工单重发即可(工单全文在主会话 transcript;要点:迁移 0039 down 写 0037、一次性 pg @15550、Q2 经 task_orchestration contracts 窄 reader、白名单/去重/如实未知三裁决)。
- **S-1(待裁)**:见 §3 末行。建议作为一张小工单派给 task_orchestration 线(改动面=拆分 assign 的「建任务/授权/宣告」次序),修完必须重跑一次「Bridge 在线稳态自动接单」活体取证。

## 6. 门禁演进(每次合入独立全量,全绿)

| 时点 | 计数 |
|---|---|
| PR 5 收口(08-27) | 1777 / 21 |
| 契约基线 e3eec185 后 | 1841 集(worktree 实测 1827/18,环境差异=主树 untracked 他线测试等) |
| W-B1 合入后 | 1862 / 23 |
| W-A1a 合入后 | 1928 / 23 |
| C 线修复批+5.5B 合入后 | 1966 / 23 |
| PR 6 合入后(本交接头 `ccc74b65`) | **1977 / 23,exit 0**(分项 bridge 390 + runner 255/10,ruff 干净) |

## 7. 已知偏差与记账(不阻塞)

401-vs-503(两面对 malformed token map 答法不一)|to_wire 超长截断|fileChange 别名只覆盖观测过的形状|runner generic 策略把 cwd 当写目标(runner 线的真缺陷,Bridge 已绕过)|E1 时真机只读核验两条(leader skills、containerManaged 读取路径)|PR 8 侧 enrollment v2 解析与 v2 BINDING_PATH 全归 W-B2|夹具无 .gitignore 时 `__pycache__` 入 commit(门禁行为正确)|materialize 幂等重放不重发房间消息(与 A-10 注释层次不同)|`.repomesh-v1-live/workspaces` 曾授当前用户 Full Control(D-4 的环境前提)。

## 8. 环境与凭据

- 全部凭据指针:`output/bridge-team/e0a-live-env.md`(gitignored);token 文件在 `output/bridge-team/secrets/`。**任何 token 不入 tracked 文件、不入报告文本。**
- 活体证据:`output/bridge-team/v1-evidence/`(48 件,含协议捕获逐字报文与探针脚本);现场 `D:/Project4work/.repomesh-v1-live/`(1.1 MB,夹具仓+全部 worktree+PR 4 state 备份)。
- 环境三坑不变:`MSYS_NO_PATHCONV=1`;控制面与 Bridge 同跑 Windows 宿主;5432 活体库不碰。拆环境按 PID(`pkill -f` 杀不掉 nohup 链)。他线端口 5432/55432/8080/3000/5280/8100 全程未碰,验收后活体环境已全拆(容器零残留)。
- governed codex-home 的 `config.toml` 现由 Bridge 管理(哨兵注释界定 managed 块,保留式合并)。

## 9. 编排机制知识(继续多代理施工必读)

1. **worktree 陈旧基线**:代理 worktree 三次被切在 `f3d343b0`(非分支头)。**每张工单第一步必须核对基线头**(`git log --oneline -3` 见指定 commit,否则确认零独有提交后 `reset --hard` 到位)。
2. **venv 不随 worktree**:`export PYTHONPATH="$(pwd)/src"` + 主仓 `.venv` 解释器;跑前验证 `import repomesh` 指向 worktree src(PYTHONPATH 实测压过 editable 安装)。
3. **600 秒看门狗**:超 5 分钟命令(全量 pytest/npm)一律后台跑+轮询输出文件,否则会话被掐(W-A1 中过一次)。
4. **并发写者规程**:子代理**永不 `--amend`**(发生过一次 amend 进他线提交的事故,已恢复);主脑在子代理活跃编码窗口内不往分支合入;C 线(主树)与 A/B 线(worktree)天然隔离,合并冲突由主脑做 integration owner。
5. 迁移多线并行:topic 分支 down_revision 指自己基线链尾,合并时只改 down_revision;链序=合并序,与编号序可以不同(0036→0038→0037→0040→…)。

## 10. 下一步(推荐顺序,恢复时逐单)

1. **裁决并修 S-1**(小工单;修完重跑稳态自动接单活体取证)——它挡着 AC-03 的稳态口径与将来六实例的一切自动派活。
2. **合入 W-A2**(cherry-pick 三提交+全量门禁+台账)。
3. **恢复 W-B2 收尾**(提交+报告+验收),随后开 **W-B2 第二段**(supervisor 集成,PR 6 已合、禁区解除)。
4. **重启 W-C2**(PR 9+Q2)与 **W-C3**(PR 10+Q1+Q3a,依赖 W-C2)。
5. **E1**(六身份开通/enrollment/auth.json 复制/启停脚本;5.5A 已部署在代码里,活体开通排队)。
6. 波次 3 串行收口:M8(Room/UI)→ M7(一 leader 一 worker,需 W-A2+W-B2 全合)→ E1 soak → E0b → V2/Q3b(六前置显式核验,门禁 #10)。

**红线现状**:`src/repomesh_runner/**` 零改动**重新生效**(C-8a 窄口已关);冻结契约(v1/v2/leader-actions)零修改纪律保持;`room-observation.v1` 只收投影;THINKING/协议帧/stderr 永不入房——V1 两轮活体各抓到一次模型收尾谎言被正确压制,这条线的核心信任模型经受住了真实场景。
