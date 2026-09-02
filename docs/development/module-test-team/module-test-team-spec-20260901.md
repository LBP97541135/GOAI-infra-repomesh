# 测试团队 v1 施工规格（Spec，冻结稿）

日期：2026-09-01
上游：同目录设计稿（为什么与怎么建）、验收标准（什么算建成）。本文件回答**改什么、改成什么样**。
术语以根目录 `CONTEXT.md` 为准。与另一份已定稿路线 spec 的对读仍待做；本文件只承载盲写线的裁决。

**冻结纪律**（借 /module-test 规程自身的原则）：本 spec 冻结后，实现与测试分别服从它；
施工中发现 spec 错误时，先改 spec 留痕、再改代码，不得让当前实现反向污染判据。

---

## 0. 范围与非目标

**范围（v1）**：S-1 平台拓扑追加、S-2 控制台三处、S-3 测试资产仓、S-4 技能文档修订、
S-5 A 组自动化测试。对应验收标准 P/A/B/C/D 五组。

**非目标**（增量路线 v2+，见设计稿 §8）：第二 worker 与同队互通、平台独立重跑外环、
变异测试、规划器自动出联调任务、per-project 关闸、后端 team view 身份字段下沉。

---

## S-1 平台拓扑追加（唯一的平台代码改动）

### 接缝与接口

不新开接缝、不动任何接口。拓扑供给链（provision → create → project → reconcile）是
既有深模块，接口为「repository_ids 进，配好人、有房间、可派工的团队出」；本改动只让
调用方多递一个元素。变化点落在既有接缝上：catalog 行的 `capability_profile`。

### 位置

`src/repomesh/modules/repository_intelligence/application/discovery_materialization.py`
`DiscoveryMaterializationService._ensure_topology`（现 421 行起）。

### 行为规格（冻结）

现方法已持有 `profiles = await self._catalog.list()`。在其上：

1. **守卫先于追加**：`if not repository_ids: raise DiscoveryNotMaterialisable(...)` 的
   判定对象**仅为计划仓集合**，在追加测试仓之前求值。计划仓全部离册时照旧拒绝——
   只剩测试团队的项目没有可派的业务工作，是错误不是编制。
2. **追加规则**：
   ```python
   test_repository_ids = tuple(
       profile.id
       for profile in profiles
       if profile.capability_profile == CROSS_REPO_TEST_TEAM_PROFILE
       and profile.id not in repository_ids
   )
   ```
   合并后 `repository_ids + test_repository_ids` 交给 `self._provisioner.ensure(...)`，
   其余实参不变。
3. **去重语义**：计划本身包含测试仓时（某计划节点直指测试仓），`not in` 去重保证
   不出现双团队；拓扑不变式「一仓一团队」是第二道防线。
4. **多档案仓语义**：catalog 中每个贴 `cross-repo-test-team` 档案的仓**各得一支团队**。
   机制按仓泛化，不设数量上限；「保持恰好一个测试仓」是运维约定，不是代码约束。
5. **幂等与指纹**：`EnsureProjectAgentTopology.ensure` 内部 `sorted(set(...), key=str)`
   已保证追加顺序不影响命令指纹；已有拓扑的项目走 `existing is not None` 早退，
   本改动对其不可达。重放（同 key / 换 key）复用既有收敛路径，无新键。
6. **回执口径（接受，不改）**：`team_count` 含测试团队；`repositories` 仍只列计划仓名。
7. **import**：`CROSS_REPO_TEST_TEAM_PROFILE` 沿本模块 `application/registration.py`
   引入 `DEFAULT_TEAM_PROFILE` 的既有路径从 `capability_management` 引入——依赖方向
   已有先例，不新增模块依赖。

### 顺带自动发生（零改动，测试要覆盖但代码不动）

principal 铸造（leader 单例 + 1 worker）、Team 与双房 reconcile、技能覆盖
（`agentteams_skills(role, base, profile)`）、路由与派工——全部由既有链路完成。

### 顺序约束（运维面，UI 要提示）

档案必须**先设，后 materialize**：AgentTeams 技能列表在 worker 资源创建时固化，
事后改档只触达尚不存在的资源（PATCH 路由 docstring 已言明）。

---

## S-2 控制台三处

### S-2a 读模型补字段（设计稿的精度修正，spec 撰写时实核发现）

设计稿原句「前端 contract 补字段即可 join」不完整：控制台仓库列表由
`src/repomesh/api/read_models/service.py::list_repositories` 供给，**当前不含**
`capability_profile`。沿 verification 落地先例（`test_commands`/`test_paths` 当时
同样是为控制台补进读模型的）：

- `list_repositories` 的仓库映射增加 `capability_profile: str | None`；
- `frontend/src/api/contract.ts` 的 `ConsoleRepositoryView`（377 行）同步补
  `capability_profile: string | null`，注释写明「档案开关（供给侧），见 CONTEXT.md」。

徽标裁决的实质（身份不进 team view、前端 join）不变；此处只是让 join 的右表真的带列。

### S-2b 改档入口（RepositoriesPage）

- **形状**：抄 `frontend/src/components/RepositoryVerificationDialog.tsx` 的弹窗模式，
  新建 `RepositoryProfileDialog`；RepositoriesPage 仓库行上与 verification 入口并列。
- **选项**：`default` / `cross-repo-test-team` 二选一（合法档案集与后端
  `TEAM_CAPABILITY_PROFILES` 一致；前端写死这两个值，后端 422 是兜底）。
- **调用**：`PATCH /repositories/{repository_id}/capability-profile`
  （`repository_intelligence/api/router.py:383` 起；body `{capability_profile}`，
  `default` 传 null 语义由后端归一）。client 层新增函数，形状沿
  `client.ts:163` verification PATCH。
- **错误态**：422（未知档名）与 404（仓不存在）在弹窗内呈现 detail，不静默。
- **文案（冻结两句）**：成功回显处与弹窗内固定提示——
  「该档案只影响之后新建的团队编制，已建团队不受影响」（供给侧语义）、
  「请在建团（materialize）之前设置」（顺序约束，见 S-1）。

### S-2c 测试团队徽标（TeamsPage）

- **判定**：`ConsoleTeamView.repository_id` join 控制台仓库列表的
  `capability_profile === "cross-repo-test-team"` → 团队名旁渲染「测试团队」徽标。
- **只断正向**：贴档建团后有徽标、业务团队无。撕档后存量团队失徽标不失功能——
  已知局限，验收标准 §D 已显式接受，前端不做任何补偿逻辑。

### 验证（红线）

浏览器实走 + `tsc -b`（禁 `--noEmit`）+ oxlint 仅受影响文件。

---

## S-3 测试资产仓（改动 0，运维 + 配方）

### 建仓与注册

真实 git 仓（建议名 `repomesh-test-assets`），注册进 catalog；贴档动作即 AC-D1 的
执行步骤（走 UI）。

### 目录结构（冻结顶层，内层留给配方施工）

```text
environments/    # 参数化环境定义：compose/dev 入口、端口与 env 的注入约定
scenarios/       # 场景库：输入、断言、触点清单
evidence/        # 轮次证据：evidence/<run-id>/，含 BLOCKED 轮
```

### 参数化环境约定（冻结）

- compose project name 与全部资源（容器/network/volume/worktree 目录）统一前缀
  `itest-<run-id>`；
- 端口一律环境变量注入或随机分配后回读；场景断言禁止写死端口，入口从环境元数据取；
- 并发度设显式上限，默认值与依据写在 `environments/` 的定义里，「上限=没写」不合规。

### 证据目录 schema（冻结字段，格式留给配方）

`evidence/<run-id>/` 必含：组合清单（仓→钉死 commit）；逐场景最小证据集
（命令、退出码、输出摘录≤64KB 超出截断注明、request-id）；本轮实际端口/命名空间；
轮次结论（逐场景 PASS/FAIL/INCONCLUSIVE + 总评）。**禁止原始全量日志入仓**。
BLOCKED 轮同样提交（原因 + 已跑部分）；worker 暴毙轮以任务台账为准，新 run-id 重跑
不补交。

### 收尾顺序（冻结，不可逆）

跑完场景 → 提交轮次目录并推送 → 拆环境 → 提任务回执（结论 + artifacts 指针指向
该 commit 路径，contentHash 对账）。先证据后拆除。

### TTL 清扫（冻结）

每轮开工第一步：清除同 `itest-` 前缀且存活超 24h 的残留；新鲜残留不动。无守护进程。

---

## S-4 技能文档修订（7 条，随 v1 一并提交）

`capabilities/skills/integration-run/SKILL.md`：
1. 端口从「pinned to the scenario's ports」改为 S-3 参数化约定；
2. Workflow 头部加「开工前 TTL 清扫」；
3. Outputs/Workflow 加证据入仓与收尾顺序（S-3）；
4. Failure Handling 加 BLOCKED 轮提交证据目录、暴毙轮新 run-id 不补交；
5. Validation 加摘录 64KB 上限、禁全量日志入仓。

`capabilities/skills/cross-repo-test/SKILL.md`（队长）：
6. 组合判据来自 Manager 任务体、不得自行改钉；并发度上限意识；
7. 三轮不收敛 → `HumanReviewRequest` 升级纪律。

---

## S-5 A 组自动化测试规格

- **位置与双档**：in-memory 档进 `tests/test_api.py` 相邻的 materialize 用例族；
  postgres 档进 `tests/integration/test_postgres.py` 惯例轨。测试面 = 模块接口
  （materialize 端点 + 拓扑视图），不深入实现断言。
- **AC-A1**：造一个贴档仓 + 计划仓集 → materialize → 拓扑含测试仓团队、principal 在册、
  控制面资源技能等于覆盖表（经 fake control plane 断言投影入参）。
- **AC-A3**：断言两次所得测试团队 **id 与 team name 相等**——禁止计数式断言。
- **AC-A4**：两条断言分开写：撕档后新项目无测试团队；先前项目拓扑中的测试团队仍在。
- **AC-A5**：早退路径逐字段比对拓扑视图。
- **AC-A6**：对照组即现有 materialize 用例在无贴档仓夹具下全过（不新写，跑既有）。

---

## 映射与冻结区

| Spec 节 | 验收 |
|---|---|
| S-1 | AC-A1~A6（经 S-5 落地）、AC-A2 由 materialize 成功蕴含 |
| S-2 | AC-D1~D4 |
| S-3 | AC-B1/B2、AC-C1/C2 的被测物 |
| S-4 | B/C 组执行纪律的成文依据 |
| P-1 | 先于一切；失败触发 plan 的改道条款 |

**冻结**：S-1 行为规格全部、S-2b 文案两句与错误态、S-3 各「冻结」小节、S-5 断言纪律。
**不冻结**（施工自由度）：S-2 组件命名与样式、S-3 目录内层格式与文件名、测试夹具组织。

---

## 修订 A（2026-09-01）：P-1 FAIL，改道条款生效 —— 联调执行下 runner 轨

plan 的改道条款与设计稿 §5.2 预案在此生效。本节是留痕：**S-1/S-2/S-5 一字不改**
（A/D 组交付物原样有效）；S-3、S-4 中「worker 自建自拆」的执行者假设被下面的条文替换，
其余冻结条文（前缀、端口、并发上限、证据 schema、TTL、收尾顺序）**继续有效**——它们约束
的是环境定义与证据，不管由谁执行。

### A.0 实测事实（P-1 的现象，改道依据）

在运行中的 AgentTeams 控制器（`agentteams-embedded:v1.2.0-rm3`，宿主单例，任何新栈接的
都是它）上，用控制器**亲自建出的** worker 容器（`POST /api/v1/workers` → 
`agentteams-worker-p1-probe`）在容器内用它**自己的** `AGENTTEAMS_AUTH_TOKEN` 实测：

| 项 | 结果 |
|---|---|
| 容器 HostConfig | `Binds=null Privileged=false CapAdd=null`（controller `backend/docker.go` 只按 `req.Volumes` 挂卷，worker 创建路径不传） |
| 镜像内 `docker` / `docker compose` / `/var/run/docker.sock` | 全无（`agentteams-copaw-worker:v1.2.0`） |
| 控制器 `/docker/` 受限直通（embedded 模式，`server/http.go`） | worker 角色 **403 `cannot gateway gateway`**（`auth/authorizer.go`：worker 与 team-leader 对 `gateway` 资源均落 `default: deny`） |
| 同一直通用管理员 token | 建网络/建卷 **403**（`proxy.go` 白名单无 `/networks/create`、`/volumes/create`）；容器名强制 `agentteams-worker-` 前缀；bind mount 403；单容器 create/start/kill/delete 通 |

结论：worker 容器**不可能** `compose up`，也无法经控制器代建；`itest-<run-id>` 命名与代理的强制前缀
还直接冲突。P-1 = FAIL。runner 镜像（`components/repomesh-runner/Dockerfile`：`python:3.13-slim`
+ git）**同样没有 Docker**。

### A.1 执行者改为 runner 执行面（替换 S-3「worker 自建自拆」的隐含前提）

- 联调轮的执行体是**平台 Runner 执行面上的一次受治理运行**（governed run）：平台按既有
  `StartAssignedWorkerTask → DispatchWorkerTask` 链，在 `.repomesh-workspaces/w/<run>/<repo>`
  为**测试资产仓**准备 git worktree，投递 `RunnerTask`，runner 在该工作区执行
  `instruction` 与 `test_commands`。
- `RunnerTask` 是**单仓信封**（`repository` + `workspace` + `instruction` + `test_commands`），
  没有多仓组合的一等字段（`src/repomesh_runner/contracts.py:127`）。因此**组合由测试资产仓的
  配方脚本在工作区内自行检出**：`environments/<env>/run_round.py` 读组合文件、把各仓按钉死
  commit 检出到工作区内 `itest-<run-id>/`（`.gitignore` 排除）、跑场景、写证据、拆 `itest-` 根。
- worker 的 `integration-run` 技能从「自建自拆」改为「核对判据 → 经批准入口发起 governed run
  → 守候回执 → 上报」（S-4 修订见 A.3）。批准入口 = `POST /agent-actions/start-worker-task`
  （Bridge 成员）或平台派工时的自动投递（runner 运行时成员）；两者都走同一条
  `worker_execution.py` 链。

### A.2 证据与收尾的重新映射（S-3「证据目录 schema」与「收尾顺序」在 runner 形态下的落点）

- **摘录由配方脚本写**：runner 的 `_run_test_commands` 只回传 `command + exit_code`
  （stdout/stderr 均 DEVNULL，`executor.py:482-505`）。最小证据集四元组因此全部由
  `run_round.py` 写进工作区的 `evidence/<run-id>/`，64KB 上限与截断注明由脚本保证。
- **入仓由平台交付写**：脚本不 push。工作区里新增的 `evidence/<run-id>/` 经 runner 的
  `changed_files` 与既有交付链（`scm/plan_delivery.py`）推成测试资产仓的候选分支
  `repomesh/<plan8>/<repo8>`——即证据实体由平台侧写入，正是设计稿 §7 表中 v3 才想要的形态，
  改道把它提前了。回执的 artifacts 指针指向该分支上的 `evidence/<run-id>/` 路径。
- **收尾顺序保持不可逆**，只是主语换了：脚本写完证据 →（脚本）拆 `itest-` 根 → runner 回传
  退出码 → 平台交付推分支 → 任务回执。证据在拆除之前落盘这一条没有变。
- **退出码约定（冻结；2026-09-01 用户裁决「取 A」改版）**：配方脚本**只要这一轮跑完且
  `evidence/<run-id>/` 已写出就退出 0**，轮次结论（PASS / FAIL / BLOCKED）写在
  `steps.json.overall`、`round.md` §4 与任务回执摘要里；非零退出只表示配方自身没跑完
  （崩溃、证据未写出），那是任务失败，不是轮次结论。**理由**：runner 只在任务 SUCCEEDED 时
  提交变更（`executor.py:320`），test_command 非零即判任务 FAILED 且不提交——按原 0/1/2
  约定，红轮与阻塞轮的证据会滞留在工作区永远进不了仓，AC-B2/AC-C1「证据照样入仓」落空。
  被拒的替代：改 runner 让证据路径失败时也提交（动冻结的 runtime.v1 与交付链）、配方自推
  分支（runner 镜像按契约不带凭据）。语义上「任务成功 = 这一轮跑了」也更诚实：组合好不好
  是这轮的**产出**，不是任务的成败。BLOCKED 同样写出证据目录（含原因与已跑部分）。
  本机干跑仍可用 `--verdict-exit` 取 0/1/2 做脚本化断言。
- **TTL 清扫**：脚本开工第一步调用 `environments/sweep-itest.sh` 清同工作区根下的 `itest-*`
  残留；judge 仍是 24h 时间戳、新鲜不动。docker 类残留在 v1 不会产生（见 A.4）。
- **证据收集时点（冻结；2026-09-01 W4 活体后用户裁决「取 R1」）**：W4 核出 runner 执行器在
  agent 阶段之后、`test_commands` **之前**收集 `changed_files`（`executor.py::_collect_evidence`），
  而 Bridge 给受限 codex 的 PATH 按设计（J-12）不含 python，配方只能在 test 阶段跑——证据落盘
  晚于收集，runner 回「0 file(s) changed」，证据永远进不了候选分支（B1 第 3/4 轮实测）。裁决：
  执行器在 `test_commands` 全部退出 0 之后**再收集一次**，**仅当 agent 阶段变更集为空时生效**
  （agent 自己改了文件就只提交它自己的集合，不混入 test 阶段产物），第二次收集**同样过
  allowed/denied 路径校验**，违规照判 `changed_path_denied` 且不提交；test_command 非零仍是
  任务 FAILED 且不提交（与上条退出码约定一致：那是配方没跑完，不是轮次结论）。
  被拒的替代：R2 放宽 Bridge 受限 PATH 加 python/git（J-12 安全设计变更，不属本线）；
  R3 证据不入 git、回执只指工作区路径（违背 S-3 冻结）。落点：`src/repomesh_runner/executor.py`
  + `tests/runner/test_executor.py::TestExecutionEvidence` 四条（test 阶段证据被收集并提交 /
  第二次收集守路径规则 / agent 阶段有变更则不二次收集 / 非零退出不提交）。

### A.3 S-4 技能文档修订的对应调整

`integration-run` 的 Workflow 改为四步（核对判据含冻结组合 → 经批准入口发起 governed run →
守候回执与证据指针 → 上报原始证据不下结论）；S-4 条 1（端口参数化）、条 2（TTL）、条 3
（证据入仓与收尾）、条 4（BLOCKED/暴毙）、条 5（64KB/禁全量日志）**全部保留**，但主语从
「worker 亲手做」改为「配方脚本做、worker 核对回执里有没有」。`cross-repo-test` 条 6、条 7 不变。

### A.4 v1 已知局限（如实声明，不藏）

- **v1 只能执行源组装型环境**（python + git 即可组装的联调，如 `e2e-fixture-joint`）。
  基于 compose 的环境定义可以按 S-3 约定**写**进 `environments/`，但在两条执行面上都**跑不了**
  ——直到出现一个带 Docker 能力的执行面（AgentTeams 侧放开代理授权且允许建网络/卷，或
  runner 镜像挂 socket，二者都是平台外/上游决策，本线不做）。
- `agentteams_skills` 覆盖表、S-1 追加、S-2 三处对改道**零感知**：测试团队依旧是贴档仓上的
  仓库团队，只是它的 worker 干活的方式变了。

### A.5 W4 的执行形态：worker 以 Bridge 成员在位（2026-09-01 用户裁决）

runner 运行时的容器不是读技能的 agent，A.3 改写的四步在纯 runner 轨里没有执行者。W4 因此按
M7 已跑通的 **Bridge 轨**执行：测试团队的 worker 是经 `repomesh-agent-bridge` 接入的本地 CLI
成员（`containerManaged:false`），它读房间通知、按 `integration-run` 调批准入口
`POST /agent-actions/start-worker-task`，平台为测试资产仓准备工作区并投递 `RunnerTask`，
由该成员的 Bridge runner 执行 `test_commands`（即配方）。**A.1/A.2 的全部条文在此形态下不变**
（单仓信封、脚本检出组合、脚本写证据、平台交付推候选分支）；Bridge runner 跑在宿主机上恰好
有 Docker 只是巧合，**v1 不依赖它**，源组装型环境的局限（A.4）照旧。环境重建照
`docs/development/room-native-bridge-handoff-20260829-wave3-m7.md` §7.6。

**A.5 已知局限（2026-09-01 W4 活体 B/C 组后如实声明，未修）**：在此形态下 agent 阶段先于
test 阶段，而受限 codex 的 PATH（J-12）跑不了配方，所以 A.2「轮次结论写在 … 任务回执摘要里」
与 A.3 四步中的第 3/4 步（守候回执、上报指针）**没有执行者**：回执 `summary` 是 codex 的
「python 未找到」，团队房 `[done] … tests passed` 只表示 test_command 退出 0。轮次结论只在
`evidence/<run-id>/steps.json.overall`、`round.md` §4 与候选分支/PR 上（A.2 的入仓条文全部
成立：B1 PASS / B2 FAIL / C1 BLOCKED 三轮证据都经交付推成候选分支并开 PR）。v2 候选：执行器把
`test_commands` 输出尾行并入回执 `summary`，或平台叙事读取 `steps.json.overall`。验收标准
执行记录表 AC-B2/AC-C1 对该子句标为未达（发现 8）。
