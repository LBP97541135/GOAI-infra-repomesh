# 托管原生施工模式：端到端验收剧本（2026-09-02）

> 形态对标：[`docs/startup-records/2026-09-02-console-demo-screenshots/`](../startup-records/2026-09-02-console-demo-screenshots/README.md)
> （17 张，一页一图，按编号讲一条从登录到风险边界的故事；文件与幕次的对应表见该目录 README）。
> 与之不同的一点：**每一幕除了截图，必须配一条机器探针**。演示截图里「团队就绪」「Running」「6 已启用」全绿的同时，
> 日志页（`10_统一日志.png`）却满屏 `unhandled error on POST /api/v1/mcp/worker`，团队房（`13_团队房间_…png`）里同一条派单发了三遍——
> 截图证明「看得见」，探针才证明「是真的」。
> 依据：`agentteams-native-execution-mode-spec-20260902.md` §7 波次与 L2 的 AC 表；`CONTEXT.md` 最小证据集。

## 1. 规则

| 项 | 规则 |
|---|---|
| 目录 | `docs/startup-records/<日期>-hosted-native-e2e.md` 为正文；截图放同名子目录 `<日期>-hosted-native-e2e/NN_页面_状态.png`；原始探针输出落 gitignored 的 `output/hosted-native-e2e/<日期>/` |
| 截图 | 1440×900、深色主题、侧栏在画面内、页面自带的时间戳或「同步 hh:mm」要入镜；含 token 的输入框先遮罩；文件名 `NN_页面_状态.png` 与样张目录 `2026-09-02-console-demo-screenshots/` 一致 |
| 探针 | 每幕一条，写「命令 / 预期 / 实际 / 退出码」四元组；探针必须是**链路死了就会变红的东西**，不能是徽章本身 |
| 判定 | `PASS` / `BLOCKED` / `WORKAROUND` / `SKIP`，口径同 `docs/startup-records/README.md`；改了命令或手工补文件才过的一律 `WORKAROUND` |
| 假绿防线 | 幕 25–27 三条演练是反证，不做不算验收；幂等类判定用整行内容哈希前后比对，不用计数 |
| 分工 | 登录、初始化管理员、控制台里填模型密钥、翻档案开关：用户在浏览器操作；其余由 AI 用 action token、`docker exec`、`mc`、`psql` 驱动并截图 |
| 报告 | 步步截图 + 每幕记录 + 九节骨架，写法见 §5；没有截图和探针原文的幕不能判 PASS |
| 外发 | `REPOMESH_DELIVERY_AUTO_ENABLED=false`，全程不推 GitHub；幕 18 停在候选分支就绪 |

## 2. 两个版本

- **基线版**（现在就能跑，不改代码）：只跑标 `B` 的幕，在活体上留下「改造前」的对照组。它会复现演示截图里的失败面：房间派单无人干活、日志页 MCP 500、dispatch 永远 queued。
- **托管原生版**（波次 1 合并后）：全部 30 幕。

## 3. 幕表

`AC` 列对应 spec L2 表的编号。

| # | 版本 | 页面 / 入口 | 操作 | 截图里应看到 | 探针（命令 → 预期） | AC |
|---|---|---|---|---|---|---|
| 01 | B | 登录页 | 用户登录 | 「登录控制平面」 | `GET /api/v1/setup/status` → `administrator: true` | — |
| 02 | — | 设置 → 执行面 | 只看 | `execution_plane: ready`，verifier 最近心跳 < 45 s | `GET /api/v1/setup/status` → `checks.execution_plane == "ready"` | 05 |
| 03 | B | 仓库页 | 只看 | 四个夹具仓，每仓「验证配置」有测试命令 | `GET /api/v1/console/repositories` → 四行 `test_commands` 非空 | — |
| 04 | — | 仓库页 → 接入团队模态 | 选「托管原生」建团 | 模态有施工模式单选，默认托管原生 | `psql: select construction_mode from project.repository_agent_teams` → `hosted_native` | 13 |
| 05 | B | 团队页 | 只看 | 每团队「团队就绪」+ 模式徽章 + 运行时 Running | `docker exec agentteams-controller agt get workers -o json` → 8 个 `phase: Running` | 05 |
| 06 | B | 智能体页 | 只看 | 8 个 copaw Running（组织 Manager 不要求 Running） | `docker ps --filter name=agentteams-worker-` → 8 行 Up | — |
| 07 | B | 新建 issue | 输入多币种需求 | 201，列表出现 issue | `POST /api/v1/issues` → 201 | — |
| 08 | B | issue 详情 | 跑完发现链四步 | 「计划已生成 v1 · 4 个任务节点 · 3 个执行批次」+ DAG | `psql: select execution_batches from repository_intelligence.plan_snapshots ...` → 三批 | — |
| 09 | — | 开工模态 | 打开 | 「执行面」块：verifier ready、每团队 Worker/Leader Running；「物化并开工」可点 | `GET /issues/{id}/discovery/readiness` → `members` 全 ready、`services[verifier]` ready | 05 |
| 10 | — | 开工模态 | 点「物化并开工」 | 若房间未建好显示 `provisioning` 并自动重查，90 s 内转绿后自动开工，不需重按 | api 日志：无 503 抛给前端；`POST .../materialize` 最终 200 | 05 |
| 11 | B | 团队房 pricing-core | 只看 | 一条派单 @worker，含 `tasks/<attempt1>` 路径；**不再**出现 `start_assigned_task` 字样（基线版会出现） | `mc ls agentteams/agentteams-storage/teams/<team>/shared/tasks/<attempt1>/` → `spec.md meta.json manifest.json base/` | 06 |
| 12 | — | 团队房 + 终端 | 等 worker ack | 房间里 worker 说「收到」；终端截图 `docker exec <worker> ls /work/<attempt1>` 有仓库文件 | `mc cat .../meta.json` → `acknowledged_at` 非空且 `status: in_progress` | 02, 06 |
| 13 | B | 团队房 | 等 worker 交包 | 托管原生版：`TASK_COMPLETED`；基线版：`BLOCKED`（对照） | `mc ls .../candidate/` → `candidate.bundle candidate.diff changes.json evidence.json`；基线版：`result.md` 首行 `STATUS: BLOCKED` | 02, 08 |
| 14 | — | Leader 房 | 等 Leader 审阅 | 审阅包派单 + Leader 回 `VERDICT: ACCEPT` | `mc cat .../tasks/<review>/result.md` → `STATUS: SUCCESS` 且首行 `VERDICT: ACCEPT` | 03 |
| 15 | — | issue 详情 | 只看 | 任务节点小字「第 1 次尝试 · verifying」 | `psql: select status, task_payload->>'adapterId' from agent_runtime.runner_dispatches` → `leased`, `repomesh-verifier` | 04, 06 |
| 16 | — | 终端 | 复验进行中 | `docker ps` 里有 `rm-verify-<run8>`，结束后消失 | `docker events --since 5m --filter name=rm-verify` → create + destroy 各一 | 07 |
| 17 | — | issue 详情 | 复验通过 | 任务 succeeded，批次 1/3 → 2/3 | `git -C .repomesh-workspaces/repositories/<repo>.git rev-parse refs/repomesh/candidates/<attempt8>`；`git -C <worktree> rev-parse HEAD` 等于任务证据 `commitSha` | 14 |
| 18 | — | 交付读模型 | 只看 | 候选分支就绪、未推送 | `GET /api/v1/deliveries` → pricing-core `head_sha` == 上一幕 sha；`gh api repos/catbobyman/repomesh-e2e-pricing-core/branches` 无 `repomesh/*` 分支 | 14 |
| 19 | B | 观测 → 推理轨迹 | 只看 | 会话含 worker 与 Leader；事件数随施工增长 | `GET /api/v1/observe/summary` → sessions ≥ 4 | — |
| 20 | B | 观测 → 用量大盘 | 只看 | 「按 issue 汇总」非空（演示里是空的） | `GET /api/v1/observe/issues` → 含本 issue | — |
| 21 | B | 观测 → 日志 | 过滤 ERROR | **零条** `unhandled error on POST /api/v1/mcp/worker`（基线版会有，作对照） | `psql: select count(*) from observability.log_entries where message like '%mcp/worker%' and level='ERROR'` → 0 | — |
| 22 | B | 观测 → 告警 | 只看 | 默认三条规则；无假触发 | `GET /api/v1/observe/alerts` | — |
| 23 | — | 演练 A：停 verifier | `docker stop <verifier>` 后开一条新 issue 到开工 | 开工模态执行面块 verifier 红，409 点名 `services[verifier]` | `POST .../materialize` → 409 `execution_plane_not_ready`，`services[0].name == "verifier"`；再 `docker start` 后重查转绿 | 05 |
| 24 | — | 演练 B：停一个 worker 容器 | `docker stop agentteams-worker-agt-worker-<repo12>` | 409 点名该 worker，用真实 agent id 复测 | `members[].agentId` 含该 worker；`docker start` 后转绿 | 05 |
| 25 | — | 演练 C：Leader 对红候选 ACCEPT | 手工把 `candidate.bundle` 换成测试红的提交后让 Leader ACCEPT | issue 页任务 failed，证据 `testResults` 有非零退出码 | `psql: runner_events` 终态 `runner.failed`；任务 `status=failed`；**不是** succeeded | 03, 10 |
| 26 | — | 演练 D：重启 worker | 施工中 `docker restart <worker>` | 新代次目录 `tasks/<attempt2>`；旧目录后来交的结果被记 `fenced` | `psql: hosted_native_events where kind='fenced'` ≥ 1；`hosted_native_attempts` 活跃行 generation=2；重复 @ 前后 `md5(string_agg(md5(t.*::text),'' order by id))` 相同 | 09 |
| 27 | — | 演练 E：越界修改 | 让 worker 改 `.github/workflows/ci.yml` | 复验 failed，`blockers` 含 `changed_path_denied` | `runner_events` 终态 payload `blockers[0]` 以 `changed_path_denied:` 开头；同改动放白名单内的对照通过 | 10 |
| 28 | B | 演练 F：告警联动 | 建一条必触发规则 | 新建 issue 503 `intake_paused`（沿用演示 17） | `POST /api/v1/issues` → 503 `{"code":"intake_paused"}` | — |
| 29 | — | 本地 CLI 对照 | 把一支团队切成 `local_cli`，不起 Bridge | 开工 409 点名成员，文案「本地 CLI」 | `POST .../materialize` → 409 `members[].status == "offline"` | 12 |
| 30 | B | 终态账面 | 只查 | 一张表：任务、尝试、调度、事件、候选提交、共享盘对象各多少；任务目录 `mc ls -r` 无 `.git`、无 `node_modules` | `mc ls -r .../tasks/<attempt1>/` 只含契约允许的文件 | 08, 11 |

幕 11 补一条密钥探针：`mc cat` 任务目录全部文本文件后 `grep` 网关 key 与模型 key 的前 8 位，预期零命中；先用一个假 key 写进临时文件验证 grep 能抓到（正对照）。

## 4. 与 spec L2 AC 的对应

| AC | 幕 |
|---|---|
| 01 不起本地 CLI | 09（就绪租约表为空仍能开工）、29 |
| 02 容器内真施工；三条帮手命令无需人工审批跑完（D-23 自动审批，房间里不出现人工 `/approve`） | 12、13 |
| 03 Leader 只审不定 | 14、25 |
| 04 无无人领的 dispatch | 15、23 |
| 05 未就绪明确失败 | 02、09、10、23、24 |
| 06 平台能答领取与代次 | 11、12、15 |
| 07 一次性验证容器 | 16 |
| 08 共享盘干净 | 13、30 |
| 09 重复通知 / 重启 / 迟到 | 26 |
| 10 越不过判据与路径 | 25、27 |
| 11 无密钥泄漏 | 11 补充探针 |
| 12 本地 CLI 仍可用 | 29（缩减版） |
| 13 前端能选能看 | 04、09、15 |
| 14 到达候选提交 | 17、18 |

## 5. 验收报告：步步截图与写法

验收不是跑完就算，**每一幕一张截图、一条探针、一段记录**，最后合成一份报告。基线版与托管原生版各一份，从零验收（L3）同样按本节写。
PR 级（L1）不截图，以测试输出为证据。

### 5.1 截图怎么拍

1. 浏览器窗格设 1440×900、深色主题（与样张一致），侧栏在画面内；控制台页面每幕一张，动作前后状态不同的幕拍两张，命名加后缀 `_前` / `_后`。
2. 终端类证据（`docker ps`、`docker events`、`mc ls`、`psql`）也截图，画面里要有完整命令行与输出首尾；同时把原文存进 `output/hosted-native-e2e/<日期>/NN.txt`。
3. 命名 `NN_页面_状态.png`，`NN` 与 §3 幕号一致；演练幕加 `演练X` 前缀，例：`23_演练A_停verifier_开工409.png`。
4. 入镜前遮罩：token、密钥、`platform-runtime.env` 内容；agent id、房间 id、任务 id 不遮。
5. 每张截图对应一条探针输出，两者时间差不超过 1 分钟；探针时间戳写进记录。
6. 截图与 `NN.txt` 全部落地后再写正文；正文里用相对路径引用，不内嵌 base64。

### 5.2 报告文件骨架

文件：`docs/startup-records/<日期>-hosted-native-e2e-<baseline|live>.md`，截图目录同名。骨架固定为九节，顺序不变：

```text
# 托管原生模式端到端验收（<日期>，<基线版|托管原生版>）
> 被验对象：分支 / 头 commit / compose 镜像 tag；执行人；判定人；开始与结束时间
## 0. 结论先行          一句话：N 幕 PASS、M 幕 BLOCKED、K 项挂起裁决；能不能进下一波次
## 1. 环境与方法        容器清单、组织与计划 id、凭据位置（不写值）、谁操作了什么
## 2. AC 对照表         AC-01…14：判定 / 证据幕号 / 一句话理由（表）
## 3. 幕次记录          按 §3 幕号逐幕，格式见 5.3
## 4. 演练结果          幕 23–29 单列：触发动作 / 系统反应 / 是否与预期一致 / 恢复动作
## 5. 挂起裁决          判不了的项：材料、两种解读、建议口径（照 R6 判定文档 §3 写法）
## 6. 新发现            F-n：严重度 / 现象 / 位置 / 是否阻断
## 7. 终态账面          任务、尝试、调度、事件、候选提交、共享盘对象各多少；内容哈希
## 8. 环境现状与拆除    验收结束时活着什么、留了什么、怎么拆
```

### 5.3 每一幕的记录格式

```markdown
### 幕 11 · 团队房 pricing-core · 任务包通知

![幕 11](./2026-09-03-hosted-native-e2e-live/11_团队房_任务包通知.png)

- **操作**：用 @admin token 在团队房 @ worker（`scripts/...` 或 curl 原样粘贴）
- **预期**：一条派单含 `tasks/<attempt1>`，不含 `start_assigned_task`
- **实际**：房间 15:02:14 出现派单，正文首行「A RepoMesh task package is ready…」，无 MCP 指令
- **探针**：`mc ls agentteams/agentteams-storage/teams/<team>/shared/tasks/<attempt1>/` → 列出 `spec.md meta.json manifest.json base/`，退出码 0，15:02:40（原文 `output/.../11.txt`）
- **状态**：PASS
- **耗时**：40 s
```

规则：`实际` 只写观察到的事实，不写解释；`探针` 四元组齐全（命令、预期、实际摘录、退出码）；`状态` 只有四个值；
`BLOCKED` 与 `WORKAROUND` 必须多一行 `**原因**`，指向文件与行号或 F-n 编号。

### 5.4 演练幕多记两行

演练是反证，多写 `**触发**`（做了什么破坏）与 `**恢复**`（怎么恢复、恢复后哪条探针转绿）。
例：幕 23 `触发：docker stop goai-infra-repomesh-verifier-1`；`恢复：docker start …，45 s 内 setup/status.execution_plane 回到 ready`。

### 5.5 报告完成的定义

- 30 幕每幕有截图或 `SKIP` 理由，没有「补拍」；每个 PASS 都有探针原文文件。
- AC 对照表里每条 AC 至少指向一幕；幕 25、26、27 三条反证没做的报告不能写「通过」。
- 幂等类判定（幕 26）附前后两次内容哈希，不附计数。
- 「结论先行」由判定人（用户）改定；AI 只填证据与建议判定。
- 报告与截图一起提交，同一次 commit；`output/` 下原文不提交。

### 5.6 产出与去向

基线版 → `<日期>-hosted-native-e2e-baseline.md`，是改造前对照组，spec §7 波次 0 的输入。
托管原生版 → `<日期>-hosted-native-e2e-live.md`，就是 L2 判定文档，替代 spec §7 波次 1 的「活体实走一条新 issue」；结论先行那句决定能否进波次 2。
从零版 → `<日期>-from-zero-<os>.md`，格式相同，幕表换成 README 的启动步骤，是 L3 判定文档。
