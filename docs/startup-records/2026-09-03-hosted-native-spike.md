# 波次 0 实证：手工任务包 v2 给活着的 pricing-core copaw worker（2026-09-03）

> 依据：`docs/development/agentteams-native-execution-mode-spec-20260902.md` §4.2 M3（任务包 v2）、§4.3（时序）、§7 波次 0；目的文档 §6、§7。
> 零代码：没有改任何源码；不经 RepoMesh 任务表；不推 GitHub；候选结果只停在共享盘。
> 时间：2026-09-02 19:59–20:49Z（本机 12:59–13:49，UTC−7）。执行人 AI，用户在 19:58 放行包内容。
> 产物目录：`2026-09-03-hosted-native-spike/`（包、审阅包、三次尝试的结果、房间全文、审批记录、重启记录、驱动脚本）。

## 0. 三个答案

| 问题 | 答案 | 证据 |
|---|---|---|
| copaw + DeepSeek 能不能独立做完多币种任务 | **能。** 尝试 1：ack 20:00:51Z → submit 20:27:04Z，其中约 20 分钟是我三次误发审批消息造成的停顿，净施工 5 分钟（20:22:08 init 通过 → 20:27:04 submit）。改 `src/pricing_core/quote.py` + `tests/test_quote.py`，9 测试绿。独立容器复验（从 `base.bundle` 克隆、`git fetch candidate.bundle`、跑冻结命令）：父提交 = `882231dd`、改动只在白名单、`python scripts/run_tests.py` 9/9 绿。尝试 3（加了「校验币种代码」的返工需求）净施工 10 分钟，16/16 绿，同样复验通过。Leader（同样 DeepSeek）审阅包 70 秒出 `VERDICT: ACCEPT`，理由抓到了「base 已有 `QuoteCurrencyTests`、"mandatory" 与默认 USD 冲突」这一真实矛盾并合理处置 | `results/ca0ef2b0…/`、`results/cfe30c99…/`、`results/review-93e1e9c6…-result.md`、§2 时间线 |
| 帮手脚本三条命令它照不照做 | **照做，一条不落、顺序不乱、参数不改。** 三次尝试共 10 次 shell 调用请求（2 次被我的错误审批拒掉、1 次被重启作废），真正执行的 7 次全是 `cd <任务目录> && bash base/tools/rm-work.sh init|test|bundle`（尝试 3 的 `init` 前串了 `ls -la` 与 `tail -n 20 spec.md`），没有一次绕过脚本自己 `git` 或手写 `candidate/`。**但每一条都被 copaw Tool Guard 拦下要人工 `/approve`**：规则 `TOOL_CMD_DANGEROUS_RM` 命中的是脚本名 `rm-work.sh` 里的 `rm`。审批协议苛刻（见 S-2）。另有一次模型「假装等审批」：20:39:00 只发了一段仿冒的「⏳ Waiting for approval」文字，工具根本没调用，停了 9 分钟，被我点醒后才真调（S-5） | `rooms-transcript.md`、`approvals.jsonl`、`sent.jsonl` |
| 重启后会不会往旧目录交结果 | **不会，但原因不是它懂 fencing。** 20:33:15Z 在尝试 2 改完三个文件、正等 `test` 审批时 `docker restart`（7 秒）。之后：① worker 不自发恢复（待审批在内存里丢了，Matrix sync token 落盘所以旧消息不重放），5 分钟内房间零消息；② `/work/<尝试2>` **连同 3 个未提交改动原样保留**（`docker restart` 不丢可写层，只有容器重建才丢）；③ 收到尝试 3 的通知后它在新目录从 base 重做，没有 `cp` 旧工作区（它说「implement the same solution as generation 2」——靠会话记忆，不靠文件）；④ 尝试 2 的共享目录自 20:31:23Z 后零写入，`meta.json` 停在 `in_progress`、无 `result.md`、无 `candidate/`；⑤ 平台侧真相：谁也没往旧目录交东西，因为没有人再提旧目录。**结论**：fencing 必须由平台做（D-8/D-9 的方向对），不能指望 worker；且目的文档 §7「容器重启导致工作区消失」这句要改成「容器重建」 | `restart_drill.log`、`watch.attempt2.stdout`（output）、§2 时间线 20:31–20:49 |

## 1. 环境与方法

- 活体：09-02 的编制原样；worker `agentteams-worker-agt-worker-dfb8a4cda6f7`（copaw v1.2.0，DeepSeek `deepseek-chat` 经 AgentTeams 网关），Leader `agentteams-worker-agt-leader-dfb8a4cda6f7`。团队房 `!3IU075BSWiAQORHR4e…`，Leader 房 `!NRE6tUlYtVZwKDXyBN…`。
- 包：按 spec §4.2 M3 手工打，见 §3；写入 `teams/repomesh-team-dfb8a4cda6f74ee795e4197963151308/shared/tasks/<attempt_id>/`（`docker cp` 进控制器 + `mc cp --recursive`）。
- 通知：`@admin` 在房间发消息，`m.mentions.user_ids` 带成员，正文以 Matrix id 开头，形状同 `src/repomesh/integrations/agentteams/matrix.py:178-187`。
- 观察：`watch.py` 10 秒一轮抓两间房 `m.room.message`、`mc ls -r` 任务目录、`mc cat meta.json/result.md`、`docker exec` 看 `/work` 与本地任务目录；`auto_approve.py` 代平台自动批 `rm-work.sh` 命令（S-2）。
- 复验：一次性 `agentteams-copaw-worker:v1.2.0` 容器（`--entrypoint sleep`），从 `base.bundle` 克隆、fetch `candidate.bundle`、断言父提交、`git diff --name-only`、跑冻结命令。它就是 spec M4 `verify.sh` 的手工版。
- 不做：不碰 RepoMesh 任务表；不推远端；不改 worker 配置（Tool Guard 保持出厂设置，好看清它挡什么）。

## 2. 时间线（UTC）

| 时间 | 事件 |
|---|---|
| 19:59:43 | 尝试 1 `ca0ef2b0…` 六个对象写入 MinIO；manifest `content_hash` 与本地一致 |
| 20:00:47 | 团队房 @worker 派单 |
| 20:00:51 | worker `ack_task`：`meta.json` → `in_progress`（413 B，`repomesh` 块被丢掉）；本地任务目录出现 |
| 20:01:05 | worker 读完 spec 与脚本，调 `execute_shell_command` 跑 `init` → **Tool Guard 拦截**，要 `/approve` |
| 20:02:41 | 我发裸 `/approve`（无 mention）→ 群房默认 requireMention，被当历史记录吞掉 |
| 20:08:19 | 我发 `@worker /approve`，Git Bash 把 `/approve` 改写成 `D:/Git/approve` → 被当「任意消息」= **拒绝** |
| 20:12:46–20:17:00 | 再触发；`@worker /approve`（正文带 mention 前缀）→ 又被拒（正文不等于 `/approve`，见 S-2） |
| 20:21:42–20:22:08 | 再触发；正文只有 `/approve` + `m.mentions` → **通过**，`init` 成功，`/work/ca0ef2b0…` HEAD=882231d |
| 20:22:10–20:22:45 | worker 读源码与 README，改 `quote.py`、追加测试（changed=2），调 `test` → 拦截 |
| 20:26:44 | 批准 → 9 测试绿 |
| 20:26:48–20:26:50 | 调 `bundle` → 拦截 → 自动批 → `candidate/` 四件写出 |
| 20:27:04 | `submit_task SUCCESS`，四个 deliverables，`result.md` 落 MinIO |
| 20:27:14 | worker 在团队房 @Leader `TASK_COMPLETED` |
| 20:27:26 | **Leader 在团队房被 @ 后陷入身份混淆**（「the worker… seems to actually be me?」），只发一条就停 |
| 20:29:16 | 审阅包 `93e1e9c6…` 写入（spec 内嵌 diff/changes/evidence，`review/` 三件） |
| 20:29:57 | Leader 房 @leader |
| 20:29:59 | Leader `ack_task` 成功（taskflow 不限角色，D-3 成立） |
| 20:30:23–20:30:47 | Leader 找本地仓库没找到，自己想通「I review based on the diff」 |
| 20:31:07 | Leader `submit_task SUCCESS`，summary 首行 `VERDICT: ACCEPT`；20:31:16 房间 `REVIEW_DONE … VERDICT: ACCEPT` |
| 20:30:40 | 尝试 2 `fb1e42bc…`（generation 2，附 REVISION 说明：校验币种代码）写入 |
| 20:31:15–20:31:31 | 派单 → ack → `init` 拦截 → 20:32:21 批准 |
| 20:32:24–20:33:01 | worker 改 `quote.py`、`tests`、`README.md`（changed=3），补发 `Progress:`，调 `test` → 拦截 |
| 20:33:15 | **`docker restart` worker**（7 秒）；重启后 `/work/fb1e42bc…` 与 3 个改动原样在；worker 无任何自发动作 |
| 20:37:46 | 尝试 3 `cfe30c99…`（generation 3，说明尝试 2 已 fenced）写入 |
| 20:38:15–20:38:23 | 派单 → ack → `init` 拦截 → 自动批 |
| 20:38:30–20:38:57 | worker 确认「workspace source is identical to base」，重做改动，`test` 拦截 → 自动批 → 16 测试绿 |
| 20:39:00 | worker 发一段**仿冒的**「⏳ Waiting for approval — Tool Guard for `bash base/tools/rm-work.sh bundle`」，实际没调工具，回合结束 |
| 20:43:59 | 我发 `/approve` → copaw 回「No pending approval」 |
| 20:48:18 | 我点醒它「工具没调」→ 20:48:21 真调 `bundle` → 拦截 → 自动批 → 20:48:32 `submit_task SUCCESS` |
| 20:48:41 | Leader 又在团队房被 @ 后身份混淆 |

审批消息全文与事件 id 在 `sent.jsonl`、`approvals.jsonl`；房间全文 `rooms-transcript.md`（99 条）。

## 3. 包内容（与 spec §4.2 M3 的对照）

`package-attempt1/` 是尝试 1 的原件（尝试 2/3 只差 attempt_id、generation 与 spec 末尾的「Note from the previous attempt」）。

| 文件 | 与 spec 的关系 |
|---|---|
| `spec.md` | 任务正文与判据逐字取自任务表 `b6e0bc59` 的 `instruction`/`acceptance`；加了「`tests/test_quote.py` 基线用例原样通过」「路径白名单」两条；三条命令、`submit_task` 的四个 deliverables、`TASK_COMPLETED` 写法 |
| `meta.json` | 原生四字段 + `repomesh` 块。**`repomesh` 块在 ack 后被 copaw 丢掉**（S-3），只够观察器首读 |
| `manifest.json` | `schema: repomesh.agentteams-task.v2`，`files[]` 全部文件、`file_digests`、`file_sizes`、`content_hash` = 按路径排序的 `path\0sha256\n` 串的 sha256 |
| `base/base.bundle` | 从公开夹具仓打，`HEAD` + `refs/heads/main` = `882231dd`，7.4 KiB |
| `base/package.json` | **spec 里没有的新文件**：attempt_id / generation / base_sha / test_commands / allowed_paths / denied_paths / budget / workspace_root。帮手脚本只读它，不读 meta.json |
| `base/tools/rm-work.sh` | `init`（clone bundle → `checkout -B work <base>` → 本地 user.name）、`test`（逐条跑 `test_commands`，记录退出码与末 40 行，跑完算整棵工作树 tree hash）、`bundle`（工作树 hash 必须等于最近一次 test 的；`add -A`+commit，多提交则 squash 成 base 上恰好一个；写 `candidate/{candidate.bundle, candidate.diff, changes.json, evidence.json}`）、`clean`。工作区 `/work/<attempt_id>`，状态 `/work/.rm-work-state/<attempt_id>/`，都在 copaw 同步根之外 |

审阅包 `package-review/`：`spec.md` 内嵌 diff、changed files、evidence 摘要、冻结判据、审阅清单、`submit_task` 状态到 `ACCEPT/REVISION/BLOCKED` 的映射与 `VERDICT:` 首行约定；`review/` 三件是 `candidate/` 的拷贝；`meta.json.assigned_to` = Leader localpart，`room_id` = Leader 房。

## 4. 观察与发现（S-n）

| # | 严重度 | 发现 | 证据 | 落点 |
|---|---|---|---|---|
| S-1 | **高（阻断托管原生）** | copaw Tool Guard 出厂开启（`security.tool_guard.enabled=true`，`guarded_tools=null`），`execute_shell_command` 命中 `TOOL_CMD_DANGEROUS_RM` 就要房间里人工 `/approve`，600 s 不批即拒。命中原因是脚本名 `rm-work.sh` 含 `rm`。三次尝试 8 次 shell 全被拦 | `copaw.log` `[TOOL GUARD] HIGH … rule=TOOL_CMD_DANGEROUS_RM`；worker `.copaw/config.json` `security` 段 | spec §5.4.4「不改 worker 镜像」不够：要么帮手脚本改名并确认 `git`/`python` 不触发别的规则，要么平台在注册 worker 时下发 `security.tool_guard.disabled_rules`/`guarded_tools`（控制器 `config.json` 由谁写待查），要么 M2 观察器兼做自动审批者（S-2 的协议） |
| S-2 | 高 | `/approve` 的生效条件极窄：群房默认 `requireMention`，裸 `/approve` 被吞；正文 `@worker /approve` 因 `approve` 不在 channel 的 `_SLASH_COMMANDS` 里，会被加上「sender: 」前缀送进 runner，`_is_approval` 只认恰好 `/approve`/`approve`，于是**任何带前缀的审批都等于拒绝**。唯一可行形状：正文恰好 `/approve` + `m.mentions.user_ids` 带 worker。Git Bash 还会把 `/approve` 改成 `D:/Git/approve`（`MSYS_NO_PATHCONV=1`） | `channel.py:1146-1240,1920-2010`；`runner.py:50-67,251-345`；房间 20:02/20:08/20:17/20:22 四次对照 | 若平台自动批，`auto_approve.py` 就是适配器原型；若走配置免批，此条只作坑记 |
| S-3 | 中 | `ack_task`/`submit_task` 用 `TaskMeta` 原生字段重写 `meta.json`，`repomesh` 块被丢；后台推送也不会带回。平台控制数据必须放 `base/`（不重推）或平台自己的库 | 三次尝试 `meta.json` 813 B → 411/451 B，`repomesh_block=False` | M2 观察器只能按目录名 = attempt_id 读自己库里的行；M3 加 `base/package.json`；契约 `meta.schema.json` 的 `repomesh` 对象标为「发布时有效」 |
| S-4 | 中 | Leader 在团队房被 worker 的 `TASK_COMPLETED` @ 到时两次陷入身份混淆（「the worker seems to be me」），没有做任何协调动作也没造成破坏；在自己房里收结构化审阅包时则干净利落（ack → 读 diff → submit ACCEPT，70 秒） | 20:27:26、20:48:41 团队房；20:29:59–20:31:16 Leader 房 | 房间文案：worker 的 `TASK_COMPLETED` 不要 @Leader（改 @admin 或不 @）；或 spec.md 明说「notify @admin」。D-3 成立：taskflow 不限角色 |
| S-5 | 中 | DeepSeek 一次「仿冒审批提示」：只输出等待文字不调工具，回合结束后无限期等。平台观察器看到「Waiting for approval」不能当真，要以 copaw 的真实 pending（或工具调用事件）为准；预算超时是唯一兜底 | 20:39:00 消息 vs `copaw.log` 无 TOOL GUARD 记录；20:43:59「No pending approval」 | D-12 预算到期路径要真的存在；房间文字不是事件源（spec M2 已这么写，实证支持） |
| S-6 | 中 | `docker restart` 不丢工作区：`/work/<attempt2>` 与未提交改动、本地 `shared/tasks/<attempt2>/` 都在；只有容器重建才丢。目的文档 §7「容器重启导致工作区消失也视为该尝试中断」的前半句事实不成立，但「视为中断」的裁决仍应保留（会话内存丢了，worker 不会续） | `restart_drill.log` 前后快照 | 目的文档 §7 措辞改「容器重启或重建都视为尝试中断；重建才丢工作区」；D-12 的 `phase != Running` 探测在 `docker restart` 7 秒内可能根本看不到，需要以 copaw 进程启动时间或 attempt 目录的心跳为准 |
| S-7 | 低 | worker 遵守白名单但会主动改 `README.md`（尝试 2/3），并在聊天里贴容器绝对路径（违反它自己的 AGENTS.md）；都不越界 | `results/cfe30c99…/changes.json`；房间消息 | 审阅包已把 changed files 交给 Leader；路径策略由复验器兜底（D-14） |
| S-8 | 低 | ack 到 init 之间 worker 会先 `cat` 脚本读懂再跑（尝试 1 花 14 秒）；三条命令的提示词写在 spec 里足够，不需要技能安装 | 20:01:02 消息 | spec §5.4.4「帮手脚本随任务包分发」成立 |
| S-9 | 低 | `submit_task` 后本地任务目录只推 `candidate/`、`result.md`、`meta.json`（`spec.md`、`base/` 被排除），共享盘无 `.git`/`workspace/`；三个尝试目录共 30 个对象 | `mc ls -r` 终态 | D-5 成立 |
| S-10 | 低 | `git clone` bundle 需要 bundle 里有 `HEAD`，否则「remote HEAD refers to nonexistent ref」；`git bundle verify` 必须在仓库内跑；`git -c init.defaultBranch=main` 免 hint | 自测容器 | M6 `BaseBundleBuilder` 打 `HEAD` + 分支两个 ref |

## 5. 对 spec §3 决策的挑战（单独列出，未裁决）

| 决策 | 挑战 | 实证 | 建议口径 |
|---|---|---|---|
| D-2「容器内不启动任何 coding CLI，copaw 是唯一施工者」 | 成立，但**前提是 shell 可用**：出厂 copaw 的 Tool Guard 让每条 shell 都要人批。不解决 S-1，托管原生模式就是「每个任务三次人工点头」 | S-1、S-2 | 波次 1 加一条 D-23：平台负责 worker 的 Tool Guard 策略（配置下发或自动审批），并把「无需人工审批即可跑完三条命令」写进 AC-02 |
| D-6「第一阶段以原生 `ack_task`/`submit_task` 为事件源，观察适配器幂等摄取」 | 成立，但 `meta.json` 在 ack 后只剩原生字段：观察器**不能**靠 `meta.repomesh.*` 认领事件 | S-3 | 观察器以目录名 = attempt_id 关联自己库里的行；`meta.json` 里的 `repomesh` 块只在发布时有效 |
| D-12「worker `phase != Running` 立即中断」 | `docker restart` 7 秒内控制器很可能一直看到 Running；中断的真正信号是 worker 进程重启（会话内存丢失） | S-6 | 探测改为 copaw 进程启动时间（或容器 `StartedAt`）晚于尝试的 `notified_at` 即中断；`phase` 只作补充 |
| D-3「Leader 通过派给它的原生任务审阅」 | 成立（S-4 后半），但 worker 按 AgentTeams 技能 @Leader `TASK_COMPLETED` 会把 Leader 拖进团队房的身份混淆 | S-4 | 派单文案让 worker 通知 `@admin`（平台身份）而非 Leader；Leader 只在自己房里收审阅包 |
| D-21 帮手脚本命令行 `rm-work.sh init|test|bundle` | 名字里的 `rm` 命中 `TOOL_CMD_DANGEROUS_RM` | S-1 | 契约里改名（例如 `repomesh-work.sh` 或 `work.sh`），并在契约测试里跑一遍 copaw 的 tool guard 规则集 |
| 目的文档 §7「容器重启导致工作区消失」 | 事实相反：`docker restart` 保留工作区 | S-6 | 措辞改「重启或重建都视为中断；重建才丢」 |

## 6. 对 spec §8 开放项的答案

- §8.2「Leader 用 DeepSeek 审 diff 的可靠性」：一次样本 ACCEPT，理由正确（识别出 base 已有币种测试、默认 USD 与 "mandatory" 的张力、路径在白名单、证据与 diff 一致）。它想找本地仓库但没找到也没卡住。可靠性至少「能用」，多样本另测（尤其 REVISION 分支没测）。
- 新增开放项（已写进 spec §8）：Tool Guard 策略归属；`/approve` 自动审批适配器还是配置免批；`meta.json` 非原生字段的存活期；重启探测信号；帮手脚本命名；worker `TASK_COMPLETED` 的通知对象。

## 7. 终态与残留

- MinIO `teams/repomesh-team-dfb8…/shared/tasks/`：新增 4 个目录（`ca0ef2b0…` 11 对象、`fb1e42bc…` 6 对象、`cfe30c99…` 11 对象、`93e1e9c6…` 7 对象）。09-02 的 `b6e0bc59…` 未动。
- worker 容器：`/work/{ca0ef2b0…, fb1e42bc…, cfe30c99…}` 三个工作区；本地 `shared/tasks/` 三个目录；容器 20:33:15Z 重启过一次。
- RepoMesh 库：任务/尝试/调度/预留计数与基线相同（4 / 1 / 1 / 1）。
- GitHub 夹具仓：零推送。
- 清理（需要时再做，本次没做）：

```bash
docker exec agentteams-controller sh -c 'for d in ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a fb1e42bc-1974-4925-bb25-64474093735c cfe30c99-47be-4027-a5f8-4282cbac8776 93e1e9c6-d832-40e7-8d39-711bf27c29f6; do mc rm -r --force agentteams/agentteams-storage/teams/repomesh-team-dfb8a4cda6f74ee795e4197963151308/shared/tasks/$d/; done'
```

```bash
MSYS_NO_PATHCONV=1 docker exec agentteams-worker-agt-worker-dfb8a4cda6f7 sh -c 'rm -rf /work /root/.copaw-worker/agt-worker-dfb8a4cda6f7/.copaw/workspaces/default/shared/tasks/{ca0ef2b0,fb1e42bc,cfe30c99}-*'
```

（Leader 本地的 `shared/tasks/93e1e9c6-*` 同理。）

## 8. 截图

- `团队房_尝试1至3与ToolGuard审批.png`：控制台 RoomView 里的团队房时间线（1440×900 深色）。
- `Leader房_审阅包与ACCEPT.png`：Leader 房里的审阅包派单与 `REVIEW_DONE … VERDICT: ACCEPT`。
- 三次尝试的探针原文与 watcher 日志在 `output/hosted-native-e2e/2026-09-03/spike/`（不提交）。
