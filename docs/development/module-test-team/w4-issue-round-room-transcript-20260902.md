# W4 补充轮 r2：从 issue 起跑一轮，全程盯三间房（2026-09-02）

目的：B/C 组四轮都是直接 `POST /bridge/materialize` 派的，项目沿用上一会话经发现链建出的 `21899c3e`。
本轮补一条**从 issue 起**的完整链，并在派工前就开始盯测试团队的三间房（团队房、队长房、worker DM）留档，
同时在控制台 RoomView 里实看。原始档案（gitignored）：`output/bridge-team/w4-live/logs/rooms/r2/`
（`<room>.matrix.jsonl` = Matrix 原始事件、`<room>.platform.jsonl` = 控制台房间流、`timeline.md` = 合并时间线）；
盯房脚本 `scripts/module-test-team/room_watch.py`。

## 1. 链条（时间为 UTC）

| 时刻 | 步骤 | 结果 |
|---|---|---|
| 05:40:4x | `POST /issues`（`w4_chain.py`，`W4_RUN=r2`） | issue `8782a8db-98e4-5e6a-b6ac-10ec1d73a972` |
| 05:41:0x | analysis → candidates → classification → approval → plan（真 LLM，各 3–6 s） | sufficient=true；候选唯一 `pricing-fixture`（score 1.0）；classification required=[pricing-fixture]；approval 无调整 |
| 05:41:15 | `POST /issues/{id}/discovery/materialize` | plan `d037728b`，`team_count=2`，`repositories=["pricing-fixture"]`（S-1 追加再次活体）；新项目的两支团队复用同名 Team 与**同三间房**（Team 按仓在控制器唯一，`repository_agent_teams` 按 `(project_id, team_name)` 各一行） |
| 05:41:5x | `POST /bridge/materialize`（`payloads/r2_issue_green.json`，`project_id=8782a8db`，前缀 `w4-r2-issue-green`） | plan `be0f747e`，队长任务 + worker 任务 `d8c6051c` |
| 05:43:33 | worker 任务 `succeeded`（79 s） | 证据 `evidence/itest-td8c6051ceec4/` `overall=PASS`，提交 `91c3c43e`，`itest-` 根拆净 |
| 05:43:4x | 交付 | 分支 `repomesh/be0f747e/55555555`=`91c3c43e` → PR #5；poller 在 05:45 前观测到 `pr:5` |

## 2. 三间房的现场（Matrix 原始事件，盯房脚本 3 s 一拉）

| 时刻 | 房间 | 发信者 | 内容 |
|---|---|---|---|
| 05:41:58.700 | 队长房 `!n53K…` | `@admin`（服务端发信身份）→ `@repomesh-test-leader` | 判据（Manager 冻结，不得改钉）：组合文件 green.json … 执行方式 … `repomesh.agent-report.v1` 回执格式 |
| 05:41:59.061 | 团队房 `!sY1l…` | `@admin` → `@repomesh-test-worker` | A verified RepoMesh task package is ready … `start_assigned_task {"task_id":"d8c6051c…"}` … Content hash … |
| 05:42:03.002 | 团队房 | `@repomesh-test-worker` | `[accepted] Task accepted; governed run is queued. (run 8391de65…)` |
| 05:42:33.035 | 团队房 | `@repomesh-test-worker` | `[started] The governed run is executing on this machine.` |
| 05:43:33.093 | 团队房 | `@repomesh-test-worker` | `[tests] The task's test commands finished. (… run_round.py, exit 0)` |
| 05:43:33.107 | 团队房 | `@repomesh-test-worker` | `[done] The governed run finished. 2 file(s) changed, commit 91c3c43e5d32, tests passed, 3 tool action(s), 0 denied.` |
| 05:30–05:45 每 5 min | 团队房 / 队长房 | `@admin` / 队长 | `room.meta` 心跳（`lifecycle=persistent`，`roomKind=team_room/direct_room`），不是消息 |
| （无） | worker DM `!iBuL…` | — | 全程无新事件；该房只有建房时的 name/topic/invite/join 与 `@manager` 的 leave（16 条，均为 03:16 建房当时） |

控制台房间流（读模型）与 Matrix 一一对应：队长房 `message: Implement changes for repomesh-test-assets`（05:41:58.66），
团队房同名 `message`（05:41:59.04）+ 四条 `matrix` 叙事；DM 房不在读模型里（404）。

**观察**：本轮队长（容器 copaw）在队长房**没有回复**（前几轮它会在队长房推理并申请工具审批）；server 拆解不依赖它回复。
worker 的最终消息仍是「python 未找到」，轮次结论只在证据与 PR 上（spec A.5 已知局限）。

## 3. 前端 RoomView 实看

- 登录 5281 → issue 列表看到新 issue `#8782a8db`（「第 1/1 批执行中」）→ issue 页「房间」区列出四间房：
  `pricing-fixture` teamRoom/leaderDM、`repomesh-test-assets` teamRoom（LIVE）/leaderDM（LIVE），末条消息 `Implement changes for repomesh-test-assets` 05:41。
- 打开 `repomesh-test-assets · teamRoom`：LIVE、轮询 5 s、右侧「事件对照线 · 本轮」正确显示本轮 05:41:59 `task_assignment` / `计划 v2 已生成` / 05:42:10 `runner.accepted` / 05:43:16 `runner.completed`；
  环境栏显示 changeset `evidence/itest-td8c6051ceec4/…`、commit `91c3c43e`、PR `catbobyman/repomesh-test-assets#5`、门禁「门禁运行中」。
- **缺陷（发现 12）**：消息列只显示房间**最早的 50 条**（03:24–04:58，即前几轮），本轮消息不出现。网络面板：每 5 s 的轮询与「↓ 加载后续」按钮都只发
  `GET /rooms/{id}/stream?limit=50`，**不带 `cursor`**；读模型是 offset 分页（`next_cursor`），前端从不传。超过 50 条的房间在 RoomView 里永远看不到新消息。
  上一会话 AC-D3 PASS 时房间不足 50 条，所以没暴露。属前端线缺陷，本线不修。
- `leaderDM` 视图同样只显示前 50 条（03:24 的判据与队长推理）。

## 4. 结论

从 issue 到 PR 的整条链在一次会话里连续跑通：发现链四步 + 审批 + materialize（30 s）→ 联调轮（79 s）→ 交付（PR #5）。
三间房的实时档案与控制台读模型一致；worker DM 房在联调轮里没有流量。新增发现：RoomView 分页不带 cursor（发现 12）。
