# 交接文档：main 功能级合并 + web→frontend 迁移

- 日期：2026-08-14
- 分支：`feat/console-v2`，HEAD `204d530`（本文档提交本身）
- **未推送**：领先 `origin/main` **302** 个提交，全部只在本地
- 工作区干净（仅 `output/` 未跟踪，是截图与教程产物，本就不入库）

---

## 0. 一句话现状

`origin/main` 的五个提交已功能级合并进本分支（合并提交 `9780c75`），随后把 `web/`
的四项能力迁进 `frontend/` 控制台（`cebc8fe`→`9aaebb8`）。**下一步是推 GitHub**，
以及三件明确的收尾（见 §5）。

---

## 1. 提交链（本次会话产出，从旧到新）

| 提交 | 内容 |
| --- | --- |
| `cc92676` | 合并前分析报告与裁决建议 |
| `9780c75` | **merge origin/main**，13 个冲突全解 + 迁移链收敛 |
| `59a33bf` | 报告补记执行结果（§7 实际怎么落的） |
| `cebc8fe` | **登录门恢复 + 双凭据**（迁移 0，其余两项的前置） |
| `7f3b0cd` | **建团**进仓库页（迁移 1）+ 后端 409 缺陷修复 |
| `8ba51f6` | **平台就绪 + 适配器探测**进设置页（迁移 3） |
| `13ed971` | **人工审核台**（迁移 2，新导航 `#/reviews`） |
| `9aaebb8` | **DAG 边语义**（迁移 4） |

---

## 2. 必读材料（按接手顺序）

### 2.1 先读这两份文档

1. **`docs/development/main-functional-merge-analysis-20260814.md`** —— 合并的全部
   依据。§1 两边功能面对照、§2 四个硬冲突与裁决、§4 迁移链方案、§6 裁决表、
   §7 实际执行结果与踩到的坑。**接手前必读**，它解释了为什么某些地方看起来「本可以
   更简单」。
2. **`docs/development/web-to-frontend-batch5-20260814.md`** —— 第五批迁移清单。
   它推翻了本文 §5.2 原来那句「`web/` 整个目录可以删」，并说明了为什么不补齐它，
   上一批迁进来的人工审核台就永远收不到东西。**动 `web/` 之前必读。**
3. `docs/contracts/delivery-read-model-v0.1.md` ~ `v0.4.md` —— 前端消费的唯一事实。
   v0.4 是发现链（增量叠加，前三版继续有效）。**禁止前端猜字段**是这一系列的红线。

### 2.2 合并留下的关键代码接缝

| 文件 | 看什么 |
| --- | --- |
| `src/repomesh/modules/change_orchestration/application.py` | `materialize()` 第 5 步：**两条路径（填草稿 / 开新版本）都要写对齐版本后的 `graph_edges` + `integration_method`**。改这里前先读 §7 的融合说明 |
| `src/repomesh/modules/repository_intelligence/contracts/__init__.py` | 跨模块唯一边界。main 的 R100 包化把本分支的发现链导出漏了，现已补齐（14 个名）。**加新导出必须同步这里**，否则应用起不来 |
| `src/repomesh/modules/project/domain.py` | `repository_agentteams_team_name` 委托 `RepositoryTeam.canonical_agentteams_team_name`（裁决 D-1）。**不要让两个铸名模板再并存** |
| `migrations/versions/20260814_0028*` `0029*` | main 两个迁移的重铸版本。**main 原版 0019/0020 已删** |

### 2.3 前端双凭据（改任何请求前必读）

**`frontend/src/api/auth.ts` 文件头** —— 解释了为什么控制台有两套凭据、为什么不能混用。

| 模块 | 凭据 | 覆盖端点 |
| --- | --- | --- |
| `api/client.ts` | 动作 token（vite env） | 读模型、发现链、`/console/*` 网格、`/setup/*`、`/plans/*` |
| `api/auth.ts` + `api/humanControl.ts` + `api/reviewDesk.ts` | cookie 会话（登录门发） | `human_control` 面：建团、审核台、检查点决策、项目控制 |

**混用会静默失败**：后端 `_bearer()` 先读 `Authorization` 头、取不到才回落 cookie。
给 human_control 端点带动作 token → 拿动作 token 去验会话 → 401，cookie 有效也进不来。
这是分两个模块而不是给 `client.ts` 加 `auth` 参数的原因。

---

## 3. 任务状态

### 3.1 已完成并验证

| 项 | 验证方式 |
| --- | --- |
| main 合并（13 冲突 + 迁移链） | 322 个定向测试全绿；一次性 postgres 从空库跑通全链并抽查落点 |
| 登录门 + 双凭据 | 浏览器实走：未登录被拦、错误密码回显后端 401、登录成功进控制台、读模型面独立 401（证明两套凭据隔离） |
| 平台就绪 + 适配器探测 | 浏览器实走：九项检查真实渲染、本机适配器真实探测、徽标配色语义经 DOM 断言核对 |
| 人工审核台 | 端到端实走：插入 review request 后 SSE 4 秒内自动出现（未刷新）、侧栏徽标跟随、决策写路径到达业务层并原文回显后端拒绝理由 |
| 前端类型与静态检查 | 全程 `tsc -b` + `oxlint` 干净（注意：`tsc --noEmit` 在本项目是空转桩，**永远退出 0，不可用**） |

### 3.2 已完成但**验证未走完**（重要）

| 项 | 已验 | 未验 | 为什么 |
| --- | --- | --- | --- |
| 建团（迁移 1） | 类型、lint、入口出现条件的逻辑 | **未实走** | ~~会真在 AgentTeams 上创建 Team，不宜在他人联调环境做~~ **理由已撤销**，见下 |
| DAG 边语义（迁移 4） | 后端端点返回的边形状与 TS 类型逐字段一致；replay 降级路径实走 | **带语义的渲染未实走** | 需要在合并后的代码上跑一次 materialize 才有带边的快照 |

两条都在各自提交信息里写明了。**接手时不要当作已验证**。

**建团的顾虑不成立（08-14 复核）**：查 `repomesh-agentteams-forwarder` 的实际去向，
它是 `socat TCP-LISTEN:8090 → agentteams-controller:8090`（docker 网络 `agentteams-net`），
转发的是**本机 `agentteams-controller` 容器**，且只绑 127.0.0.1；后端默认
`agentteams_controller_url=http://localhost:8090`（`settings.py:20`）正落在这条链上。
所谓「他人联调环境」全在本机，建团实走随时可做。

**DAG 边语义的缺数据已实证**：5533 库 26 份快照逐条查，`graph_edges` **全为空数组**，
而 `integration_method` 有值——都是合并前旧路径写的行。不是「大概没验」，是没有可渲染
的数据。

**08-14 已补跑 materialize，后端半场通过。** 做法与结论：

- 目标 `98b01b24-8d2e-5b98-b2ff-701e6e91a23f`（issue「空文本新建 txt文本」，有草稿快照、
  0 轮次、`required_checkpoints=[]`），走 `POST /bridge/materialize`。
- **刻意不触发派工**：该项目拓扑只给 `api`+`client` 建了团，提交的计划却点名
  `pricing-core`/`checkout`/`billing`——`_plan_batches` 会把「catalog 有但本拓扑无团」
  的仓库计入 `skipped`，batches 空 → 不起执行计划。DB 复核 `plans=0 tasks=0`，
  没有 coding agent 被唤起、没有 GitHub 写入。这一步只验落库与渲染，不验派工
  （派工在此前的验收轮里已实走过）。
- 结果：`graph_edges` 落 **2 条 confirmed 边**（带 `interface` + `agreement`），
  `plan_version` 仍为 **1**——说明合并后**草稿消费路径**（`set_integration` 带
  `graph_edges`）成立，没有多开一版。另建 1 份 Engineering Spec + 2 份 Contract Spec。
- 读模型对得上：`/issues/{id}/repositories/{pricing-core}/plan` 给 3 节点 2 边、
  `plan_version=1`，`/plans/{id}/versions/1` 200。边按**仓库名**配对，两侧名字一致。

**仍未走完的只剩浏览器渲染那一下**：控制台有登录门，需本机账号 `jack1` 登录后打开该
issue，确认连线 hover 出 `interface`/`agreement`。数据与端点都已就位。

改动前已备份 `plan_snapshots` 全表到 scratchpad（`plan_snapshots-before-materialize.sql`）。

---

## 4. 环境说明（踩过的坑）

> ⚠ **本节原先写反了，08-14 已订正。** 原文说「5280/8100/5533 不是主工作树在跑，
> 而是 `GOAI-infra-repomesh-live`（`run/console-v2-live`）」——那是本次会话**重启前**
> 的状态。重启后这三个端口已换成主工作树，见下表。**判断归属永远查进程命令行，
> 不要信任何文档（包括本文）里记的端口归属**，它是会漂的。

当前（08-14 重启后）**5280 / 8100 都是主工作树**，实测进程命令行：

```text
PID 25476 :: ...\GOAI-infra-repomesh\.venv\Scripts\uvicorn.exe repomesh.main:app --host 127.0.0.1 --port 8100
PID 31112 :: node ...\GOAI-infra-repomesh\frontend\node_modules\...\vite.js --port 5280
```

```bash
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5280,8100 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { (Get-CimInstance Win32_Process -Filter \"ProcessId=\$_\").CommandLine }"
```

| 端口 | 服务 | 归属 |
| --- | --- | --- |
| 5280 | vite dev server | 主工作树，`VITE_API_TOKEN=console-dev-token`，proxy → 8100 |
| 8100 | uvicorn `repomesh.main:app` | 主工作树，连 5533 |
| 5533 | `cons-live-pg` 容器 | 联调库，schema `20260814_0029`，账号 `jack1`(admin) |
| 8090 / 6167 | `repomesh-agentteams-forwarder` | socat → 本机 `agentteams-controller` 容器 |
| 18001/18080/18088 | `agentteams-controller` | 本机 |
| 9000 | `repomesh-minio-forwarder` | 本机 |

5533 库里有什么（08-14 实测，**两个 0 都不是缺陷**）：

| 表 | 行数 | 说明 |
| --- | --- | --- |
| `repository_intelligence.plan_snapshots` | 26 | `graph_edges` 全空（合并前旧路径写的） |
| `repository_intelligence.repositories` | 5 | 全是 `repomesh-e2e-*` 夹具仓库 |
| `project.repository_agent_teams` | 22 | |
| `project.agent_topologies` | 14 | |
| `project.human_review_requests` | **0** | 没有受控项目产生检查点，审核台空是对的 |
| `delivery.delivery_policies` | **0** | 空表 = 三级回退落在 env 默认层，正是 main 的设计语义 |

要开干净的自验环境仍可另起：

```bash
docker run -d --name verify-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=repomesh -p 127.0.0.1:5601:5432 postgres:16-alpine
```

后端与前端（前端 proxy 目标本次已做成可覆盖）：

```bash
REPOMESH_DATABASE_URL='postgresql+asyncpg://postgres:pw@127.0.0.1:5601/repomesh' uv run alembic upgrade head
```

```bash
REPOMESH_DATABASE_URL='postgresql+asyncpg://postgres:pw@127.0.0.1:5601/repomesh' REPOMESH_AGENT_ACTION_TOKEN='verify-token' REPOMESH_RUNNER_CONTROL_TOKEN='verify-runner' uv run uvicorn repomesh.main:app --host 127.0.0.1 --port 8101
```

```bash
cd frontend && REPOMESH_API_TARGET=http://127.0.0.1:8101 npx vite --port 5281
```

注意事项：
- ASGI 入口是 **`repomesh.main:app`**，不是 `repomesh.bootstrap.app:app`（后者无 `app` 属性）
- 空库要先 bootstrap 管理员才能过登录门（`POST /api/v1/auth/bootstrap`，用 `--data-binary @file` 传 JSON，直接 `-d` 带中文会 400）
- **5432 活体库谱系与本分支不符，别对它跑本分支迁移**（既有纪律）
- 一次性容器用完即删
- **Docker Desktop 起不来、报 `removing stale socket: ...userAnalyticsOtlpHttp.sock`**：
  那个 socket 是损坏的文件系统条目（`ls` 看得见但 `stat` 失败、权限位全 `?`），
  `rm -f` / `cmd del` / .NET `File.Delete` 全部删不掉。解法是把整个
  `%LOCALAPPDATA%\Docker\run` 目录 `Rename-Item` 掉让 Docker 重建，引擎 20 秒就绪。
  已发生两次（旁边留着 `run.stale-20260812`、`run.broken-20260814`）。
  ⚠ **绝不要点 "Reset to factory defaults"**——它会清掉全部镜像与容器，`cons-live-pg` 的
  联调数据和验收快照一起没。

---

## 5. 待完成任务

### 5.1 立即可做

1. **推 GitHub**（302 个提交未推）。推之前要知会协作者一件事：main 原版迁移
   `20260812_0019/0020` 已被删除重铸为 `20260814_0028/0029`，**任何已按 main 原序
   执行过这两个迁移的数据库需要人工对账**（分析文档 §4 有说明）。
2. **补走两条未验证路径**（§3.2）：在合并后的代码上跑一次完整 materialize，既能拿到
   带 `graph_edges` 的快照验证 DAG 语义渲染，也能顺带验证建团。

### 5.2 明确立项、尚未动手

3. **`web/` 的去留 —— 原判「整个目录可以删」已撤销，见
   [`web-to-frontend-batch5-20260814.md`](web-to-frontend-batch5-20260814.md)。**

   > ~~四项迁移后 `web/` 只剩 `PrdPlanner` 与 `ProjectSetup` 两样没有对应物，两者都
   > 已被涵盖，所以 `web/` 整个目录可以删。~~
   >
   > **这段是错的（08-14 复核）**。`PrdPlanner` 那半成立，`ProjectSetup` 那半不成立：
   > `EnsureProjectAgentTopology` 的类文档原话是「`execution_mode` 和
   > `required_checkpoints` **刻意**留在默认值……管理面（`POST /projects/topologies`）
   > 仍然拥有那件事」（`modules/project/application.py:190`）。物化路径**不**涵盖监管
   > 策略。活体库 14 个拓扑里 12 个是 `auto` + 空检查点，仅有的 2 个带检查点与授权的
   > 都出自 `web/`。
   >
   > **后果**：现在删 `web/`，**上一批刚迁进来的人工审核台会永远为空**——审核单由
   > `required_checkpoints` 触发，而控制台没有任何地方能设置它。这就是
   > `human_review_requests = 0` 的原因。
   >
   > 另外清点出**两个漏网的**：`AccountPanel`（`POST /auth/accounts` 建本地账号）与
   > `TeamSetup`（`POST /agent-teams` 手工组队），`frontend/` 全目录搜这三个端点
   > 字符串**一次都没出现**。没有账号就加不了审核人。

   所以 `web/` 剩的不是两样是四样，其中三样卡在同一条链上（配检查点 → 建审核人账号
   → 审核台才有东西）。已列为**第五批迁移**，清单、端点、守卫、时序死线与验收标准
   全在上面那份文档里。**做完第五批才可以删 `web/`**。

   顺带线头（这几条原判仍成立）：`web/DagGraph.tsx` 是孤儿组件（全仓库无 import）、
   `web/src/api.ts` 的 `planDiff` 已封装无调用方、`web/vite.config.ts` 代理写 8000
   而其文档写 8001。

4. **仓库接入入口收敛**。`/setup/repositories/onboard`（main，要管理员，扫描+建团一把梭）
   与 `frontend` 的 `scan-org`/`scan-repo`（动作 token，任务式，只注册不建团）是同一件事
   的两个入口。本次迁移绕开了前者（直接调无复合语义的 `/repositories/{id}/agent-team`），
   两者仍并存。

5. **统一到单一主体化凭据**。两套鉴权并存是本次的既成事实，设置页的缺口清单已如实列出。
   长期应收敛，但那是独立立项。

### 5.3 更早的遗留（未受本次影响）

6. `docs/development/team-handoff.md` 记的后端线遗留：**M-12**（`029e3e1` 未合并）与
   **M-7 契约提案**（未裁决，底稿在该文 §7）。
7. 图形化 DAG 的**版本对比**（`GET /plans/{id}/diff` 目前无调用方）——迁移 4 只做了
   边语义，diff 叠加层未做。

---

## 6. 不要重开的裁决

| # | 裁决 | 日期 | 出处 |
| --- | --- | --- | --- |
| D-1 | `repository_agent_teams` 取复合唯一 `(project_id, agentteams_team_name)`；铸名统一 `rm-team-{repository_id.hex}` | 08-14 | 分析文档 §2 硬点 1 |
| D-2 | 本分支迁移链不动，main 两个迁移重排链尾 | 08-14 | 分析文档 §4 |
| D-4 | 前端以 `frontend/` 为准，`web/` 短期并存 | 08-14 | 分析文档 §6 |
| —— | **前端加登录门**（推翻 08-12「不设登录门」） | 08-14 | 用户裁决；理由见 `ConsoleShell.tsx` 文件头 |
| —— | 控制台不设第二套状态映射：`state`/`phase`/`display_status` 唯一实现在读模型 | 既有 | 契约 v0.1 §5.1 |
| —— | 诚实数据：无源字段显「未接入」，不编造；服务端 `detail` 原文上抛，不归并 | 既有 | 契约体例 |

---

## 7. 验证纪律（本项目既有）

- **不跑全量测试套件**，只跑受影响面的定向测试。
- 前端 = **浏览器实走** + **`tsc -b`** + `oxlint` 受影响文件。
  ⚠ `tsc --noEmit` 在本项目是空转桩，**永远退出 0**，用它等于没测。
- 计数不证明幂等；信号对不等于原因对；性能结论要先有对照组。
