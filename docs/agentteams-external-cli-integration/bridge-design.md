# 外部 CLI 接入 AgentTeams：远程成员桥接设计

本文记录 Claude Code 以「远程成员」身份接入 AgentTeams 的设计思路与取舍。实现位于
`components/agentteams/plugins/teamharness/remote/claude-code/`。

配套文档：[外部 CLI Agent 接入开发规范](integration-guidelines.md)——动手前先读那份。

---

## 一、要解决的问题

让操作者自己机器上的 Claude Code（用本人订阅登录，不掏 API key）作为一等公民加入
AgentTeams 团队：能被创建、被编排、收派活、干活、按团队协议回报。

上游 issue [#399](https://github.com/agentscope-ai/AgentTeams/issues/399) 是同一诉求，
2026-03-22 提出，标签 `area:roadmap-ecosystem`，至今开着。

## 二、为什么不能「加一个 runtime」

这条路已经有人走过两次，同一天被否。

| PR | 做法 | 结局 |
|---|---|---|
| [#569](https://github.com/agentscope-ai/AgentTeams/pull/569) | 加 `runtime: codex`，挂载 `~/.codex/auth.json`，40 文件 | 2026-07-18 closed |
| [#828](https://github.com/agentscope-ai/AgentTeams/pull/828) | 加 harness runtime，`HarnessType: claude\|gemini\|opencode\|codex`，49 文件 | 2026-07-18 closed |

Maintainer 关闭 #828 时说清了原因：

> the current architecture has moved away from adding another standalone runtime
> that **duplicates Matrix, file sync, policy, and session layers**. Those
> capabilities now live in the current runtime and plugin boundaries.

关键在于**方向没被否定，做法被否定了**。要复用现有能力边界，不要再造一套。

## 三、三个既成事实（本设计的地基）

动手前先确认了三件上游已经支持、但没人串起来用过的事：

**1. `containerManaged: false` 已经实现，而且切得很干净。**
[`member_reconcile.go`](../../components/agentteams/agentteams-controller/internal/controller/member_reconcile.go)
的跳过只在 `ReconcileMemberContainer` 一处：

```go
if !m.Spec.DesiredContainerMan() {
    log.FromContext(ctx).Info("container management disabled for member, skipping", ...)
    return reconcile.Result{}, nil
}
```

也就是说 controller 照样建 Matrix 身份、建房间、配存储 prefix、写 CR status，**唯独不起
Pod**。这正是「人在本地跑，但在团队里是正式成员」所需的全部。

**2. `runtime` 字段对远程成员完全无关。**
`ValidRuntime` 只被 backend 用来选镜像，而 `containerManaged: false` 直接绕过 backend。
**所以不需要动 CRD 枚举，不需要改 controller 一行代码。**

**3. TeamHarness MCP 已经接管了 Matrix 出站。**
`message` / `artifact` / `filesync` / `taskflow` 都是自己发 Matrix HTTP 请求的。
挂上这个 MCP server，agent 就能在房间说话、传文件、报任务状态——不用实现任何 Matrix 客户端。

> 这三点合起来解释了为什么 #828 那 49 个文件里大部分可以删掉。

## 四、架构

```
┌──────────────────────────────────────────────────────────┐
│ AgentTeams Controller（零改动）                            │
│   Worker CR { containerManaged: false }                   │
│   → Matrix 身份 + 房间 + storage prefix + status           │
│   ✗ 不起 Pod                                              │
└──────────────────────────────────────────────────────────┘
                          │ 身份 / 房间 / 存储坐标
                          ▼
┌──────────────────────────────────────────────────────────┐
│ 操作者本机                                                 │
│                                                           │
│  ┌────────────────────────────────────────────────┐      │
│  │ bridge（唯一常驻进程）                           │      │
│  │  入站：inbox MCP → 识别 @我 的指派                │      │
│  │  驱动：headless 协议跑一个 turn                   │      │
│  │  监督：超时 / 取消 / 崩溃重启 / 事件去重           │      │
│  │  会话：task-id ⇄ CLI session/thread              │      │
│  └───────────────┬────────────────────────────────┘      │
│                  │ stdio                                  │
│                  ▼                                        │
│  ┌────────────────────────────────────────────────┐      │
│  │ Claude Code（操作者自己的订阅）                   │      │
│  │   ↑ 资产由 projector 投影：                       │      │
│  │     prompts → CLAUDE.md（marker 托管区段）        │      │
│  │     skills  → .claude/skills/                    │      │
│  │     MCP     → .mcp.json（${VAR} 引用）           │      │
│  │   ↓ 出站走 TeamHarness MCP，不碰 Matrix           │      │
│  └────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

**一句话职责划分：出站交给 MCP，入站交给 bridge，资产交给 projector，身份交给 controller。**

## 五、模块与依赖

```text
bridge/
├── protocol.py        AssetProjector + RuntimeDriver 两个契约
├── dedup.py           游标 + seen-set + turn ledger + ack 水位线
├── session_store.py   task → runtime session 句柄，崩溃可续
├── bootstrap.py       操作者手写本地配置（上游契约子集）
├── supervisor.py      主循环：poll → claim → turn → forward → ack
├── drivers/
│   └── claude_code.py RuntimeDriver：stream-json
└── projectors/
    └── claude_code.py AssetProjector：CLAUDE.md / skills / .mcp.json
```

`drivers/` 和 `projectors/` 是**唯二**的 runtime 特化叶子。换 Codex 只该动这两个文件——
如果动了别的，说明抽象画错了。

## 六、关键设计决策

### 6.1 执行与资产投影拆成两个协议

`AssetProjector`（写 `CLAUDE.md`/skills/MCP）和 `RuntimeDriver`（跑一个 turn）分开。
合成一个的话，supervision 逻辑要按 runtime 重写一遍——#828 之所以是 49 文件，根子在这。

### 6.2 `run_turn` 是 generator，返回值即结果

```python
def run_turn(self, request: TurnRequest) -> Generator[TurnEvent, None, TurnResult]
```

Supervisor 掌表：到点停止消费 + `close()`，driver 的 `finally` 收尸子进程，
**`timeout`/`cancelled` 由 supervisor 合成**——只有它知道为什么打断。

消费方必须用 `yield from` 或显式取 `StopIteration.value`。裸 `for` 循环会丢弃结果，
而 driver 可能在第一次 yield 之前就 return（如 spawn 失败），裸循环会把它看成「空的成功」。

### 6.3 `session_ref` 必须在事件时刻落盘

driver 一拿到 resume 句柄就 yield `session_ref` 事件，supervisor 立刻写 session store，
**不等 turn 结束**。#828 就是反着做的：`matrix_relay.py` 恒传 `None`，`--resume` 从未触发。

### 6.4 session 按 task，不按 room

一个房间可以并行多个任务，按 room 键会串味（对应上游 issue #603「同一 team 下不同任务并行
出现污染」）。MVP 落地：`threadRootId` 作 task_id，同 thread 续接同一会话。

### 6.5 至少一次投递 → 至多一次执行

三件常被揉在一起的事拆开：

| 状态 | 规则 | 不这么做会怎样 |
|---|---|---|
| 游标 | **只在 ack 时前进**，读取不推进 | 崩溃 = 静默丢工作 |
| seen-set | 有界持久化 | 重放会重复执行 |
| turn ledger | 键 `(task_id, trigger_event_id)` | 崩溃重启会重跑同一个 turn |

启动时看到 `in_flight` 要**重新授权**（上个 bridge 死在半路，重试才对）；只有到达终态的才拒绝。
`timeout` 故意不算终态，留着能被捞回来。

### 6.6 两道时间闸

seen-set 只存 mention 事件，所以「我处理过吗」对更早的历史无法回答。两个补丁：

- **`first_run`**：无游标的首次 sync 返回的是*历史*，首轮只 ack 不执行，否则会把上周的
  @mention 当新任务打进活的 workspace。
- **ack 水位线**：mention 稀疏的房间里，gap 回填可能翻页翻过上个游标、一路进史前历史。
  水位线是时间下界，`ts <= watermark` 视同已知领域。

### 6.7 转发幂等靠 Matrix txnId

`txnId = sha256(f"{task}#{event}#fwd")[:32]`。崩溃重启后重发同 txnId，服务端自己去重。
比在本地状态里加「已转发」标志少一份要保持崩溃一致的状态。

### 6.8 `CLAUDE.md` 用 marker 托管区段，绝不覆盖

这是操作者自己的机器、自己的项目笔记。投影只替换 marker 之间的内容：

```
<!-- BEGIN AGENTTEAMS TEAMHARNESS (managed; edits inside are overwritten) -->
...
<!-- END AGENTTEAMS TEAMHARNESS -->
```

`unproject` 只移除自己写的，用户内容逐字保留。

### 6.9 凭证红线

- `.mcp.json` 里只写 `${VAR}` 引用，**变量名**由 `mcp_env_passthrough: tuple[str, ...]` 携带。
  类型上就堵死「把 token 值写进文件」这条路——`dict[str, str]` 会让违规变成一行笔误。
- `probe()` 只对凭证文件做**存在性检查**，从不打开。
- bridge 不认证 runtime，操作者自己登录。
- 错误文本里注入的 env 值（≥8 字符）一律打码，防止崩溃的 CLI 把 token 回显进房间。
- bootstrap 文件出现疑似 secret 值直接**拒绝加载**——手写配置的笔记本正是真 token 被
  「先粘上试试」的地方。

## 七、验证

**109 个单测**（`components/agentteams/plugins/tests/teamharness/remote/`），零网络、零
mock.patch、不起真实 claude——全部走注入依赖。fake driver 用真 generator 语义写，所以
「裸 for 丢结果」这个契约违规会被测试当场抓住。

**真机 E2E**（embedded 模式部署）走通：

创建 → 列出 → 加入 Team → 自动接受邀请进房 → 收派活 → 资产投影 → 真实 Claude Code 执行 →
thread 回复 → 同 thread 会话续接 → MCP `taskflow` 确认/提交 → 团队协议格式回报。

controller 日志里的决定性证据：

```
"msg":"container management disabled for member, skipping"  worker=bohan-local
"msg":"worker created"
```

Element 房间时间线里的对照：容器化 worker `dev-agent` 停在 `invited`，远程成员
`bohan-local` 走到了 `joined`。

## 八、已知缺口

| 缺口 | 说明 |
|---|---|
| `runtime.yaml` 无人写 | 上游契约定义了它，但 controller 尚未实现写入；当前靠操作者手写 bootstrap 兜底。**配置写错，agent 会理直气壮地信错**——实测踩过 |
| 共享存储 | 见下方修正与 §9.10–9.12。凭证不是缺口，已端到端跑通；剩下的是 `mc` 二进制依赖，以及 embedded 部署没给 MinIO 宿主机入口 |
| 安全等价性做不到（已声明） | qwenpaw adapter 是进程内插件，能包装每个工具结果、热改 file guard；CLI 只能靠 hooks 近似，Codex 连近似都没有。已在 `docs/teamharness-boundary-and-contracts.md` 的 Credential Eligibility 一节声明远程成员不接收敏感 credential binding，而不是假装等价 |

### 修正：共享存储的凭证不是缺口

本文初稿把「共享存储不可用」归因于「`mc` 二进制 + MinIO 凭证，笔记本上都没有」。
**后半句是错的**，而且错法很典型——正是本设计一直在提防的那种：*因为没有容器，就默认能力也没有*。

事实是 provisioner 在**身份 provision 阶段**就给每个成员建好了 scoped MinIO 用户
（`EnsureUser` + `EnsurePolicy`），这一步排在 `ReconcileMemberContainer` 的
`containerManaged` 跳过点**之前**。远程成员有 MinIO 账号，和它有 Matrix 账号是同一个原因。
用户名就是成员名，密码是 worker credentials Secret 里的 `WORKER_MINIO_PASSWORD`——
和操作者已经在取的 `AGENTTEAMS_WORKER_MATRIX_TOKEN` 同一个 Secret。

消费端也不用写代码：`_filesync_mc_env` 本来就支持从环境变量拼 `MC_HOST_agentteams`。
操作者设四个变量（`AGENTTEAMS_FS_ENDPOINT` / `_ACCESS_KEY` / `_SECRET_KEY` /
`AGENTTEAMS_STORAGE_PREFIX`）即可，四个都已进 `DEFAULT_MCP_ENV_PASSTHROUGH`，
投影仍然只写 `${VAR}` 引用，凭证红线不变。**零文件同步代码**——正是 #828 被拒的那一项。

真正剩下的：`mc` 不在笔记本上（长期应换纯 Python MinIO 客户端，可独立提上游），
以及 endpoint 从宿主机的可达性——**后者实测下来是真缺口，见 §9.11**。另外 cloud/incluster 模式走
`POST /api/v1/credentials/sts` + K8s TokenReview，笔记本没有 ServiceAccount token，
**声明不支持**：embedded 模式有完整共享存储，集群模式没有。

## 九、第二个 runtime：Codex CLI（已落地）

抽象验过了，**结论是「基本成立，但那句『只动两个文件』是错的」**。如实记录。

### 9.1 目录必须先动一次

`bridge/` 原本住在 `remote/claude-code/` 下面。第二个 runtime 一来就没法 import——
`claude-code` 不是合法标识符，而且让 claude-code 目录当共享代码的家本身就是错的。
已按 `bridge/__init__.py` 里早就写下的计划把它提升到 `remote/bridge/`，
`remote/claude-code/` 随之消失（它只剩一个 README）。

这不是抽象错了，是**一笔早就记在账上的债，第二个 runtime 让它到期**。

### 9.2 实际动了几处

| 处 | 内容 |
|---|---|
| `drivers/codex_cli.py` | 新增，协议翻译 |
| `projectors/codex_cli.py` | 新增，`AGENTS.md` + `.codex/skills` |
| `runtimes.py` | **新增的注册表**，一个 runtime 一条目 |
| `drivers/_process.py` | **新增**，抽出两个 driver 共用的子进程管道 |
| `projectors/_assets.py` | **新增**，抽出两个 projector 共用的 marker/技能过滤 |
| `supervisor.py` | 加 `--runtime` 开关；`build_supervisor` 走注册表 |

`dedup.py` / `session_store.py` / `protocol.py` **零改动**——这才是分层成立的真正证据。
supervisor 改的是入口装配，不是监督逻辑：它仍然不知道自己在驱动哪个 CLI。

那两个 `_` 开头的共享模块是主动抽的：第二个 driver 本来会把「terminate → 宽限 → kill」
和 marker 托管区段算法各抄一份，**而进程收尸抄两份的后果，是其中一份哪天悄悄不收尸了**。

> 修正判据：与其说「只动 drivers/ 和 projectors/」，不如说
> **「`dedup` / `session_store` / `protocol` 不动，且 supervisor 只在装配处动」**。

### 9.3 协议选型：`codex exec`，不是 `app-server`

本文初稿写的是 `codex app-server` JSON-RPC。**错了**——`codex --help` 里 `app-server`
标着 `[experimental]`，而 `codex exec` 才是受支持的非交互入口，`--json` 出 JSONL，
`resume` 是一等子命令。

事件形状（对 `codex-cli 0.145.0` 实测抓的，不是猜的）：

```
{"type":"thread.started","thread_id":"<uuid>"}   ← session_ref，第一帧就有
{"type":"turn.started"}
{"type":"item.started"|"item.completed","item":{"type":"agent_message"|"command_execution"|...}}
{"type":"turn.completed","usage":{...}}
{"type":"turn.failed",...}
```

### 9.4 实测踩出来的坑（每条都花了一次探测）

| 坑 | 表现 | 对策 |
|---|---|---|
| **`codex exec` 即使 prompt 是参数也读 stdin** | 等 EOF，turn 永久挂起且零输出 | `stdin=DEVNULL` |
| **`error` item 不等于 turn 失败** | 成功的 turn 里混着「skills 预算被压缩」的 error item | 只有 `turn.failed` 定生死 |
| **一个 turn 有多条 `agent_message`** | 前几条是旁白（「我这就去读…」「shell 还没返回…」） | 答案取**最后一条**，全转发等于把 prompt 明令禁止的过程播报发进房间 |
| **`resume` 是子命令且选项集更窄** | `--sandbox` 放 `resume` 后面直接 `unexpected argument` | 全局 flag（含操作者的 `driverArgs`）一律放 `resume` 前面 |
| **npm 装的 CLI 在 Windows 是 `.CMD` 垫片** | 裸名 spawn 报 `WinError 2`，看起来像「没装」；错误文本还是 GBK 乱码 | `shutil.which` 解析后再 spawn。**Claude driver 有同样的潜在 bug**，只是本机 `claude` 恰好是原生 `.exe` 才没暴露 |

### 9.5 MCP 配置：Codex 没有项目级配置

这是唯一没能对称映射的资产。Claude Code 读 workspace 里的 `.mcp.json`；
Codex 只有全局 `~/.codex/config.toml`，和操作者自己的全部配置混在一起。

**取舍已定：不写任何文件**，改用 per-invocation 的 `-c mcp_servers.teamharness.*` 覆盖。
加入团队不该改人家全机器的配置，而且没写就不用卸。

代价与红线：`-c` 的值进的是进程参数表，同机其他进程可读——**比文件更差**。
所以那里只放非密的 role 和编码 pin，凭证一律走环境继承，不序列化到任何地方。

> **该假设已实测，结论是不成立**：Codex 一点自己的环境都不传给 stdio MCP 子进程，
> 而且 `env` 表是子进程的**全部**环境、不是叠加层——声明了三个变量，server 就只有那三个。
> 但取舍没有被迫重开，因为 Codex 另有一个字段正好管这件事：
> `mcp_servers.<id>.env_vars` 是一个**变量名列表**，按名字从父进程继承。
> 形状和 `AssetContext.mcp_env_passthrough` 一样、和 Claude Code 投影里的 `${VAR}` 一样，
> **没有任何值被序列化**。顺带一提，Codex 在这里拒绝 table（`invalid type: map,
> expected a sequence`），于是连"手滑写个值进去"都做不到。
>
> 这条假设值得单独验证的理由是：它错了也**不报错**。见 §9.10。

### 9.6 真实 Team 房间闭环（已跑通）

141 单测，零网络、零 mock.patch。真机 embedded 部署，团队 `e2e-remote`，
成员 `bohan-codex`（`containerManaged: false`，**无容器**）：

| 环节 | 结果 |
|---|---|
| 建成员 | `agt apply` 建 CR，`docker ps` 查无此容器 |
| 身份 | controller 照常发 Matrix 身份 + 房间（约 40s 后就绪） |
| 自动接受邀请 | bridge 收下 `@admin` 的团队房邀请并 join |
| 首轮基线 | `recorded N event(s) as baseline and executed none`，不重放历史 |
| 资产投影 | `AGENTS.md` + 5 个按角色筛的 skill，**`mcp=none`**，无 `.mcp.json` |
| 收派活 | `@mention` 驱动真实 Codex turn（61s），写出 `CODEX-LOOP-OK` |
| 回报 | 答案以 thread 回复发回团队房 |
| 会话续接 | 同 thread 追问，resume 同一 thread id，答出上轮暗号 `ORCHID`，`turn_count: 2` |

### 9.7 只有真机才能抓到的两件事

**bug：每个 runtime 都把自己记成 `claude-code`。**
`Supervisor.__init__` 的 `driver_name` 默认是 `"claude-code"`，而 `build_supervisor`
从来没覆盖过它——于是 Codex 的 thread id 被记在 Claude Code 名下。
单测抓不到，因为测试直接构造 `Supervisor`，走不到真实装配那条路。

今天只是记错，但隐患是实的：`resume_ref` 原本不校验 driver，一旦成员换 runtime，
就会把 Codex 的 thread id 喂给 Claude 的 `--resume`。已一并修掉两处——
传真实 runtime 名，且 `resume_ref` 对不上 driver 就当没有句柄（重开一轮永远是安全的）。

**MCP 工具「看得见、调不动」——Codex 版的第二道授权。**
好消息：`-c` 方案成立，Codex 看得见全部 7 个 teamharness 工具，
而且 `message` **不在列表里**——这恰好证明 env 块送达了 MCP 子进程，
因为隐藏 `message` 正是基于 `AGENTTEAMS_AGENT_ROLE` 的角色过滤。9.5 的假设成立。

坏消息：真去调用返回
`{"isError":true,"content":[{"text":"user cancelled MCP tool call"}]}`。
这就是规范§五.3 那条「找到了工具、调用被拒，看起来像 MCP 坏了」，Codex 版。

**已找到正解**（从二进制里读出配置结构体字段）：

```yaml
    - -c
    - mcp_servers.teamharness.default_tools_approval_mode="approve"
```

三个看着像但没用的岔路，值得记下来：`enabled=true` 无效；
`projects.<path>.trust_level="trusted"` 管的是项目不是 MCP；
`default_tools_approval_mode` 四个值里只有 `approve` 行（`auto`/`prompt`/`writes` 照拒）。
`--dangerously-bypass-approvals-and-sandbox` 也能通，但为换一个授权把沙箱整个扔了，`approve` 不用。

按本设计一贯原则，**这个默认仍不由 bridge 决定**——和 `driverArgs` 默认为空同理。

### 9.8 还有一个只有真调用才暴露的坑

授权通了之后 `ack_task` 返回 `{"ok":false,"error":"workspaceDir is required"}`。

因为 MCP server 是靠 `QWENPAW_WORKING_DIR` / `COPAW_WORKING_DIR` 推断工作区的，
**远程成员一个都没有**。两个 runtime 现在都往 MCP 环境里投
`TEAMHARNESS_SHARED_DIR=<workspace>/shared`——是路径不是凭证。

这条投影侧的代码从一开始就缺，但**只有真的去调用工具才会暴露**：
projector 单测断言的是文件形状，形状一直是对的。

### 9.9 taskflow 闭环（已跑通）

补上授权和 `TEAMHARNESS_SHARED_DIR` 之后，团队协议整条打通：

| 环节 | 结果 |
|---|---|
| leader 侧 | `projectflow create_project` + `taskflow delegate_task` 建出 `shared/tasks/codex-taskflow-1/` |
| `ack_task` | `{"ok": true, ...}` |
| 干活 | 交付物写进 `shared/tasks/<id>/workspace/taskflow-proof.txt` = `TASKFLOW-OK` |
| `submit_task` | 任务状态转 `submitted` |
| 回报 | `@dev-agent TASK_COMPLETED: codex-taskflow-1 - Result: ...`，团队协议格式 |

顺带一提：中途 agent 被要求 ack 一个不存在的任务时，**它拒绝了**——没有伪造 ack、
没有创建交付物，而是回报「任务不存在，需要 Leader 先 delegate」。角色提示和
task-execution 协议是生效的。

## 九点五、共享存储实测（已跑通）

### 9.10 一个"成功"其实什么都没干

装上 `mc` 之后第一次让 Codex 推文件，工具返回 `{"ok": true}`，
`remotePath` 却是 `shared/tasks/.../real-object.txt`——**没有 `agentteams/agentteams-storage/` 前缀**。
那不是对象存储路径，那是个**本地相对路径**：`mc cp <file> shared/a/b.txt` 老老实实
在本地复制了一份、退出码 0。agent 被告知交付物已进共享存储，其实它一步没离开过这台机器。

两个独立的毛病叠在一起才让它这么安静：

1. **Codex 不传环境**（§9.5），所以 `AGENTTEAMS_STORAGE_PREFIX` 到不了 MCP server。
2. **`filesync` fail-open**：拿不到前缀时 `_default_shared_prefix()` 退化成裸的 `"shared"`。
   而 `_filesync_mc_env` 的凭证检查是**以 remote 带 alias 为触发条件**的——
   丢了 alias，恰恰就跳过了那个"用来发现 alias 丢了"的检查。

第 1 条按 §9.5 用 `env_vars` 修。第 2 条改成拿不到前缀就报错：

```
shared storage is not configured; set AGENTTEAMS_STORAGE_PREFIX ... so filesync
targets object storage instead of a local directory
```

这条修在 `mcp/server.py`，两个 runtime 和容器成员共享，不是 remote 专属的补丁。

### 9.11 部署形状：宿主机没有 MinIO 入口

embedded 模式下 MinIO 只监听容器内的 `127.0.0.1:9000`，靠 Istio/Higress mesh 做容器间互通，
**9000 端口没有发布**。所以远程成员没有任何可拨的 `AGENTTEAMS_FS_ENDPOINT`——
和 Matrix 要走网关 `18080`（而不是容器内的 `6167`）是同一类问题，但 MinIO 连网关都没有。

这不是 bridge 的 bug，是**部署形状的缺口**：真要支持远程成员用共享存储，
embedded 部署得给 MinIO 一个宿主机入口。实测时用一个只绑 `127.0.0.1` 的 socat 旁路顶上，
没有动现有部署。

### 9.12 两个 runtime × 真实存储（都已跑通）

| | Codex CLI | Claude Code |
|---|---|---|
| 成员 | `bohan-codex` | `bohan-local` |
| MCP 配置 | `-c` 覆盖 + `env_vars`（不落文件） | `.mcp.json`（只有 `${VAR}` 引用） |
| 上下文文件 | `AGENTS.md` | `CLAUDE.md` |
| 授权 | `default_tools_approval_mode="approve"` | `--allowedTools mcp__teamharness` + `--permission-mode` |
| 房间闭环 | ack → 建文件 → `filesync push` → `stat` → submit → `TASK_COMPLETED` | 同左 |
| 对象独立核验 | admin 凭证 `mc cat` 读到 `MANIFEST-V1` | 读到 `CLAUDE-RELEASE-1` |

两件顺带被真机确认的事：

- scoped 凭证的边界是真的：`bohan-codex` 能列 `shared/`，列 `agents/dev-agent/`
  和桶根都是 `Access Denied`。**零文件同步代码**，正是 #828 被拒的那一项。
- 上一轮修的 `driver_name` 在真实状态文件里对上了：两个 `sessions.json` 分别记着
  `codex-cli` 和 `claude-code`。修之前两个都会记成 `claude-code`——单测抓不到，
  因为测试直接构造 `Supervisor`。

## 九点六、启动就绪：探针从"警告"升级为"门禁"

### 9.13 发现本地 CLI 只能在笔记本侧做

controller 结构上看不见操作者的机器：没有容器、没有上报通道（契约文档明写远程成员
不接收 worker desired-state apply loop）。唯一的连接是 Matrix，而 Matrix 里能看到的
只有"已经跑起来的 bridge"——要让 controller 知道机器上有什么，得先有东西在机器上跑
并上报，那个东西就是 bridge 自己。循环论证。

所以模型是 **operator 声明，bridge 验证**，不是自动发现：

- 声明：`local.runtime`（新增）或 `--runtime`，前者写进文件、后者临时覆盖。
  之前只有命令行，于是同机两个成员**只能靠启动方式区分**，配置文件里看不出谁是谁。
- 验证：`probe()` —— `shutil.which` → `--version` → 凭证文件存在性检查。

`member.runtimeName` 是**另一回事**（`agents/{runtimeName}/` 存储前缀），两个词撞车
是继承来的，值得在代码里留一条注释：把 CLI 名写进 `runtimeName` 会悄悄挪走存储前缀，
两边都不报错。

### 9.14 探针失败必须拦住启动，否则会吃掉消息

原来 probe 失败只打 warning 然后照常进主循环。这不是 UX 问题，是**丢消息**：

```python
handled_ids = _event_ids(events)   # 整批，不是"成功的那些"
...
self._inbox_state.ack(next_batch, handled_ids, watermark_ts=...)
```

未登录就启动 → 首轮 baseline 把积压全标记掉 → 之后每个 turn 失败但照样 ack 进
seen-set。**事后登录不会补跑**，那些任务越过游标就没了，房间里也没有任何提示。

现在改成:不可用就不启动，并且**等待重试**——因为让 runtime 不可用的两件事
（没装、没登录）都是在另一个终端里修好的，而 probe 只是一次 `--version`。默认等
300 秒，`--wait-for-runtime 0` 立即失败（受 supervisor 托管的启动要这个，重启策略
本身就是重试循环）。

实测：等待中途创建凭证文件，下一次探测就接上并正常启动，无需重启。

### 9.15 门禁堵不住的那一半，堵在 ack 前

`authenticated` 是**文件存在性检查**（红线禁止打开凭证文件），Codex 那边 `config.toml`
存在就算数——**假阳性很容易**：有配置没登录，照样过门禁。加上 token 中途过期，
门禁天然覆盖不全。

补法是在另一侧：turn 失败后**重新探测**，若 runtime 已不可用，则

- 不 forward（房间不会收到一条稍后会被重放推翻的"我做不了"）
- `settle_turn(..., "interrupted")` —— 非终态，claim 可重新获取
- `request_stop()` —— 复用既有语义：`_stopping` 时**不 ack**，游标留在原地

于是操作者重新登录再启动，这批消息原样重放。这条完全复用了 Ctrl-C 中断 turn 已有的
那套机制，没有新增状态。

代价是失败路径上多一次 `--version`；成功路径零开销（有测试盯着）。
探测本身抛异常时**默认判定为可用**——这道闸会停掉 bridge，而一个自己都出错的探针，
证据强度远低于手上已有的那次 turn 失败。

## 十、下一步

- `filesync` 换纯 Python MinIO 客户端（可独立提上游）。修了它就不用装 `mc` 二进制，
  §9.11 的 endpoint 问题也少一层
- embedded 部署给 MinIO 一个宿主机入口（§9.11），否则远程成员的共享存储只能靠旁路
- `plugins/tests/teamharness/mcp/tools/test-filesync.rb` 里的路径断言写死了正斜杠，
  在 Windows 上跑不了（先于本轮改动存在）
