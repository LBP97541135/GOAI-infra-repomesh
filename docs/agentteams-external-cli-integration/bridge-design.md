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
| 共享存储不可用 | `taskflow`/`filesync` 依赖 `mc` 二进制 + MinIO 凭证，笔记本上都没有。任务状态是本地的所以 ack/submit 正常，pull/push 降级为错误返回 |
| 安全等价性做不到 | qwenpaw adapter 是进程内插件，能包装每个工具结果、热改 file guard；CLI 只能靠 hooks 近似，Codex 连近似都没有。应当在契约里声明远程成员不接收敏感 credential binding，而不是假装等价 |

## 九、下一步

`remote/codex-cli/` 复用同一 bridge 骨架，只换协议层（`codex app-server` JSON-RPC）。
**如果那一步只需要动 `drivers/` 和 `projectors/`，说明本设计的分层是对的。**
