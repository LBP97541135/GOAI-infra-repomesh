# 外部 CLI Agent 接入 AgentTeams 开发规范

面向要把 Claude Code / Codex / Gemini CLI / Cursor CLI / qwen-code / opencode 这类
**外部编码 CLI** 接入 AgentTeams 的开发者。

配套文档：[远程成员桥接设计](bridge-design.md)——本文是规范，那份是已落地的实例。

---

## 一、先理解上游的态度

这不是「有没有人想做」的问题——想做的人很多，做过的人被拒过两次。**先搞清楚上游为什么拒，
比先写代码重要得多。**

### 1.1 社区诉求（全部开着）

| Issue | 诉求 | 状态 |
|---|---|---|
| [#399](https://github.com/agentscope-ai/AgentTeams/issues/399) | 直接拉起 codex/claude code 当 agent | open，`area:roadmap-ecosystem` |
| [#117](https://github.com/agentscope-ai/AgentTeams/issues/117) | 支持 Codex OAuth（不止 API Key） | open |
| [#808](https://github.com/agentscope-ai/AgentTeams/issues/808) | 挂载 qwen-code / opencode 作 worker | open |
| [#93](https://github.com/agentscope-ai/AgentTeams/issues/93) | 支持 Cursor CLI | open，已指派 |

Maintainer 在 #93 的表态定了调子：

> Cursor CLI 接入这类问题会牵涉 **Worker runtime 的适配边界**，不只是加一个命令入口。
> 后面可以和 qwen-code/opencode 这类 CLI runtime 诉求一起收敛。

### 1.2 两次被拒的尝试

| PR | 做法 | 结局 |
|---|---|---|
| [#569](https://github.com/agentscope-ai/AgentTeams/pull/569) | 新增 `runtime: codex`，40 文件 | closed 2026-07-18 |
| [#828](https://github.com/agentscope-ai/AgentTeams/pull/828) | 新增 harness runtime 覆盖 4 种 CLI，49 文件 | closed 2026-07-18 |

关闭 #569 的原话：

> A direct Codex runtime is **not part of the accepted architecture** ... A future
> direct CLI runtime should **begin with a design proposal or issue** against the
> current runtime interfaces.

关闭 #828 的原话：

> the current architecture has moved away from adding another standalone runtime
> that **duplicates Matrix, file sync, policy, and session layers**. Those
> capabilities now live in the current runtime and plugin boundaries ... should
> **reuse the current shared capability and execution-adapter interfaces**.

### 1.3 结论

**方向是被欢迎的，「新增 standalone runtime」这个做法是被拒的。**

被接受的落点是 TeamHarness plugin + execution adapter（[PR #939](https://github.com/agentscope-ai/AgentTeams/pull/939) 已合并）。
这也解释了为什么 `plugins/teamharness/adapters/claude-code/` 早就有目录却只是个空壳——
位置留好了，实现没人做。

---

## 二、硬性约束

以下每一条都对应上游被拒的具体理由或明文契约。**违反任何一条，PR 大概率白写。**

### ❌ 不要做

1. **不要往 CRD 的 `runtime` 枚举里加值。**
   `enum: [openclaw, copaw, hermes, qwenpaw]`。它只被 backend 用来选镜像。
2. **不要新造一套 Matrix 客户端 / 文件同步 / 会话管理 / 策略层。**
   这四样正是 #828 被拒的原文。TeamHarness MCP 已经提供。
3. **不要在 adapter 里查 `agt` CLI 要团队/成员身份。**
   `docs/teamharness-boundary-and-contracts.md` 明文禁止，要读 controller 写的 runtime 配置。
4. **不要把凭证值写进任何投影出的文件。**
   只写变量名引用，让 runtime 在 spawn 时展开。
5. **不要读、拷、打包、日志任何 CLI 的凭证文件**（`~/.claude/.credentials.json`、
   `$CODEX_HOME/auth.json` 等）。bridge 不负责认证，操作者自己登录。
6. **不要交 49 文件的大 PR。** 先开 design issue。

### ✅ 应该做

1. 复用 `containerManaged: false`——controller 侧零改动。
2. 出站走 TeamHarness MCP 工具，入站走 `inbox` 工具。
3. runtime 特化只落在两个叶子：driver（执行）+ projector（资产投影）。
4. 先在 [#399](https://github.com/agentscope-ai/AgentTeams/issues/399) 提设计评论，
   锁定目录位置和边界，再动手。

---

## 三、必读文件清单

按顺序读。**每一份都对应一类会让你返工的知识。**

### 3.1 边界与契约（不读必踩）

| 文件 | 为什么读 |
|---|---|
| `docs/teamharness-boundary-and-contracts.md` | TeamHarness 拥有什么、**不拥有**什么。"does not own" 那节比 "owns" 更重要 |
| `docs/member-runtime-config-contract.md` | controller → runtime 的字段契约。**注意每行的 `# master current:` 注解——大部分尚未实现** |
| `plugins/README.md` | 插件包契约、生命周期脚本、`teamharness/remote/` 的用途 |

### 3.2 唯一完整的实现先例

| 文件 | 为什么读 |
|---|---|
| `plugins/teamharness/adapters/qwenpaw/plugin.py` | **唯一完整的 adapter**。重点看 `render_team_context`（团队事实怎么渲染）、`_skill_names_for_role`（角色怎么筛 skill）、marker 常量（可托管区段怎么做） |
| `plugins/teamharness/plugin.yaml` | manifest 结构；`skills.*.roles` 决定哪些 skill 归哪个角色 |

> ⚠️ qwenpaw adapter 是**进程内插件**（QwenPaw 有 Python plugin API，能挂 `register(api)`）。
> 外部 CLI 没有这种扩展点，只能进程外。所以能借鉴它的**语义**，借鉴不了它的**形态**。

### 3.3 可用能力

| 文件 | 为什么读 |
|---|---|
| `plugins/teamharness/mcp/server.py` | 你能白嫖的全部工具。看 `TOOL_NAMES`、`TOOL_SCHEMAS`、`MESSAGE_TOOL_BLOCKED_ROLES` |
| `plugins/teamharness/prompts/agent/*.md` | 角色提示。`remote-member.md` 是外部 CLI 对应的角色 |
| `plugins/teamharness/skills/team/task-execution/SKILL.md` | 任务执行协议——agent 怎么 ack/submit |

### 3.4 控制面（确认你的假设）

| 文件 | 为什么读 |
|---|---|
| `agentteams-controller/internal/controller/member_reconcile.go` | `ReconcileMemberContainer` 里 `containerManaged` 的跳过点——整个方案的地基 |
| `agentteams-controller/internal/backend/interface.go` | `ValidRuntime` / `Runtime*` 常量，确认 runtime 只影响选镜像 |
| `agentteams-controller/config/crd/workers.agentteams.io.yaml` | Worker CRD 全字段，尤其 `containerManaged` 的描述 |

### 3.5 历史（别重复别人的错）

| 链接 | 为什么读 |
|---|---|
| [PR #828](https://github.com/agentscope-ai/AgentTeams/pull/828) 全部评论 | 看清「什么样的实现会被拒」，以及作者踩过的 session 持久化坑 |
| [PR #569](https://github.com/agentscope-ai/AgentTeams/pull/569) 关闭评论 | maintainer 对「直接 CLI runtime」的完整表态 |

---

## 四、接入检查清单

### 阶段 0：动手前

- [ ] 读完第三节全部文件
- [ ] 在 #399 提交设计评论，写明目录位置、边界、不动什么
- [ ] 确认目标 CLI 有 **headless/非交互协议**（无则无法接入）
- [ ] 确认目标 CLI 有 **会话续接机制**（`--resume` / thread id 等）

### 阶段 1：最小闭环

- [ ] `containerManaged: false` 的 Worker CR 能创建且**不起容器**
- [ ] bridge 能从宿主机连上 Matrix 并 `inbox poll`
- [ ] **首轮只建基线不执行**（否则会重放历史指派）
- [ ] 收到 @mention 能驱动一个 turn 并把结果转发回房间

### 阶段 2：可靠性

- [ ] `session_ref` 在事件时刻落盘，不等 turn 结束
- [ ] generator 被 `close()` 后**不留孤儿进程**
- [ ] 崩溃重启不重复执行已完成的 turn
- [ ] 转发用确定性 txnId，重放不产生重复消息

### 阶段 3：团队公民

- [ ] 自动接受 team 房间邀请（**带信任白名单**，不能谁邀请都进）
- [ ] 已加入房间从服务端读取，不靠记忆（邀请只出现一次）
- [ ] 资产投影：团队契约 + 角色提示 + 运行时事实 + 按角色筛的 skills + MCP 配置
- [ ] agent 能自主调用 `taskflow` 完成 ack/submit

### 阶段 4：安全

- [ ] 凭证文件只做存在性检查
- [ ] 投影文件里零 token（用假 token 跑一遍 grep 验证）
- [ ] 错误文本对 env 值打码
- [ ] 状态文件里零 token

---

## 五、已知陷阱

以下每条都是**实际踩过并付出调试时间**的，不是理论风险。

### 5.1 Matrix 语义

| 陷阱 | 后果 | 对策 |
|---|---|---|
| Team 房间是**邀请制**，容器化 worker 在 entrypoint 里自己接受 | 无容器的成员永远停在 `invited`，Team 显示 `READY 0/1`，房间里查无此人 | bridge 必须自动接受邀请 |
| 邀请在 sync 里**只出现一次** | 靠邀请重建房间列表的实现，重启后就忘了 | 从 `/joined_rooms` 读，别靠记忆 |
| sync timeline 会被截断（`limited: true`） | 消息突发时任务指派被静默吞掉 | 透传 `gaps` 并回填，别做事后切片 |
| 游标读取即前进 | 崩溃 = 静默丢工作 | 只在 ack 时前进 |
| 首次 sync 返回历史 | 上周的 @mention 被当新任务执行 | 首轮只建基线 |

### 5.2 CLI 驱动

| 陷阱 | 后果 | 对策 |
|---|---|---|
| 等 turn 结束才存 session 句柄 | 崩溃后 resume 永远不触发（#828 的原 bug） | 一拿到就存 |
| 裸 `for` 消费 generator | 静默丢弃 `TurnResult`，spawn 失败被看成空的成功 | `yield from` 或取 `StopIteration.value` |
| 只读 stdout 不排空 stderr | 子进程填满 stderr 管道 → 双向死锁 | 后台线程排空进有界缓冲 |
| 打断后不收尸 | 孤儿进程继续往 workspace 写 | `finally` 里 terminate → 宽限 → kill |
| session 按 room 键 | 同房间并行任务上下文串味（issue #603） | 按 task 键 |

### 5.3 权限与环境（最容易误诊的一类）

| 陷阱 | 表现 | 对策 |
|---|---|---|
| headless CLI 默认拒绝写文件 | agent 回「我需要写权限」 | 操作者显式配 `--permission-mode` |
| **MCP 工具需要第二道授权** | agent 找到了工具、调用被拒，**看起来像 MCP 坏了** | 还要配 `--allowedTools mcp__<server>__<tool>` |
| MCP server 用字面量 `python` 启动 | CLI 的 spawn 环境里可能不是同一个解释器 | 用 `sys.executable` 绝对路径 |
| **中文 Windows 上 MCP 协议直接失效** | `tools/list` 永远解析失败，客户端看到**零个工具**，且失败信息毫无编码线索 | server 端把 stdio 钉死 UTF-8；投影侧加 `PYTHONIOENCODING=utf-8` |
| 工具抛非 `ValueError` 异常 | 冒到 stdio 循环把 server 打死，客户端丢掉**全部**工具 | dispatch 处兜底转成工具级错误 |
| `mc`（MinIO 客户端）不在笔记本上 | 裸 `WinError 2`，读起来像路径 bug | 报错说人话，指明缺哪个二进制 |

> 后三条已在本仓库修复并提交，对任何 Windows 用户都成立，与远程成员无关——
> 属于可以独立提给上游的价值。

### 5.4 配置

| 陷阱 | 后果 | 对策 |
|---|---|---|
| 操作者手写 bootstrap 写错 team 名 | **agent 会理直气壮地信错**并据此产出 | 尽早改用 controller 写的 `runtime.yaml` |
| 直接覆盖 `CLAUDE.md` | 毁掉操作者自己的项目笔记 | marker 托管区段，区段外逐字保留 |

---

## 六、提交前自检

- [ ] `python -m compileall -q plugins/teamharness/...`
- [ ] `python -m unittest discover -s plugins/tests/teamharness/remote -p "test_*.py"`
- [ ] `ruby plugins/scripts/validate-plugin.rb plugins/teamharness/plugin.yaml`
- [ ] `ruby plugins/tests/teamharness/test-contracts.rb`
- [ ] `ruby plugins/tests/teamharness/mcp/test-server.rb`
- [ ] `git diff --check`（行尾——上游对 Windows CRLF 敏感，见 issue #1133 / PR #1134）

> ⚠️ 工具列表在 **4 处**硬编码：`server.py` 的 `TOOL_NAMES`、`plugin.yaml` 的
> `mcp.servers[].tools`、`test-contracts.rb`、`mcp/test-server.rb`。加工具必须全改，
> 漏一处 CI 必挂。

### PR 拆分建议

按可独立评审的边界切，别堆成一个：

1. 对上游有独立价值的通用修复（如编码、健壮性）——**这些可以单独先提**
2. 新增的共享能力（如 `inbox` 工具）
3. bridge 核心（契约 + 状态层）
4. runtime 特化叶子（driver + projector）
5. 主循环 + 文档

参考本仓库的提交序列：`git log --oneline` 查 `feat(teamharness)` / `fix(teamharness)`。

---

## 七、判断抽象对不对的唯一标准

**接第二个 CLI 时，如果只需要动 `drivers/` 和 `projectors/` 两个文件，抽象就是对的。
如果要动 `supervisor.py` / `dedup.py` / `session_store.py`，说明抽象画错了，
趁早重画比将来重写便宜。**
