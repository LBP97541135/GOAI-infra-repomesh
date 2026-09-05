# 交接：下一会话做「基线版验收 + 波次 0 实证」（2026-09-02）

写于 2026-09-02 晚。本会话没有改任何源码，只做了讨论、拍板和写文档。下一会话仍不改源码。

## 给下一会话的 prompt（原样复制）

```text
上一个会话（2026-09-02）把「issue→提交链没人干活」的解法拍板成两种施工模式并列：托管原生（默认，AgentTeams copaw worker
在自己容器里施工，不用 runner / coding CLI）与本地 CLI（Bridge，原样保留）。产出三份文档：目的文档（用户写）、施工 spec
（D-1…D-22 决策、M1…M8 模块、前端/契约/后端与数据库/环境四方面改动、影响范围、波次 0–4）、端到端验收剧本（30 幕，一页一图配一条探针）。
本会话没改一行源码，环境还活着。

这个会话的任务有两件，都不改源码：
1. 跑验收剧本的「基线版」：只跑标 B 的 13 幕，在活体上留下改造前的对照组。登录由我在浏览器做；其余你用 action token、
   docker exec、mc、psql 驱动并截图。产出 docs/startup-records/2026-09-03-hosted-native-e2e-baseline.md 与同名截图目录，
   截图拍法、每幕记录格式、九节报告骨架、完成定义全部按验收剧本 §5 执行；探针原文落 output/hosted-native-e2e/<日期>/NN.txt。
2. 波次 0 实证：手工按 spec §4.2 M3 的 v2 布局打一个尝试包给活着的 pricing-core worker（base.bundle 从公开夹具仓
   在 base sha 882231dd 打、rm-work.sh 帮手脚本、spec.md、带尝试 id 的 meta.json、manifest v2），先把包内容贴给我过目，
   我点头后再写进 MinIO 该团队的 shared/tasks/<尝试id>/ 并用 @admin 在团队房 @ worker。之后再手工给 Leader 一个审阅包，
   施工中 docker restart 一次 worker。回答三个问题：copaw 加 DeepSeek 能不能独立做完多币种任务；帮手脚本三条命令它照不照做；
   重启后会不会往旧目录交结果。产出 docs/startup-records/2026-09-03-hosted-native-spike.md。
3. 用实证答案更新 spec §8 开放项；有推翻 §3 决策的发现单独列出来，不要直接改决策。

先按顺序读这几个文件，读完再开口：
1. CONTEXT.md（术语表）
2. docs/development/agentteams-native-execution-mode-purpose-20260902.md（目的与 11 条不变量，§6 执行链，§7 工作区边界）
3. docs/development/agentteams-native-execution-mode-spec-20260902.md（§3 决策记录；§4.2 M3 任务包 v2 布局与 M1 状态机；
   §4.3 时序；§7 波次；§8 开放项）
4. docs/development/hosted-native-e2e-acceptance-script-20260902.md（§1 规则，§3 幕表里标 B 的幕）
5. docs/startup-records/2026-09-02-handoff-solution-design.md §2（环境实况、id 表、§2.5 查法）与 §4（坑）
6. docs/startup-records/2026-09-02-issue-to-commit-chain.md §4–§6 与 .rooms.md（房间 id、任务 id、worker 在房里怎么推理）
7. components/agentteams/manager/agent/copaw-worker-agent/skills/task-management/SKILL.md 与 file-sharing/SKILL.md
   （worker 侧原生协议：目录布局、ack_task / submit_task、四种状态、base/ 与 workspace/ 的归属）
8. components/agentteams/copaw/src/copaw_worker/task.py:553-577、hooks/tools/taskflow.py:299-340、sync.py:1-20,258-266
   （ack 幂等、submit 覆盖、身份只比 assigned_to、后台同步根）
9. src/repomesh/integrations/agentteams/task_publishing.py（现有 meta.json / manifest 的确切字段，手工包要和它对得上）
10. docs/startup-records/2026-09-02-console-demo-screenshots/README.md（截图样张与失败面对照）
11. docs/startup-records/README.md（记录格式）

约束：
- 环境还活着（15 个容器、组织 repomesh-e2e、计划 6f438ac3、pricing-core worker 容器 agentteams-worker-agt-worker-dfb8a4cda6f7），
  取证直接查，别重建也别整拆；MinIO 与 Matrix 的凭据位置在交接 §2.3，只读位置不打印值。
- 不要碰我的密码、不要在页面里填 API key；需要管理员会话的操作告诉我，我自己点。
- 四个 GitHub 夹具仓是干净的，不要往上推任何东西；候选结果只停在共享盘。
- 手工包不要走 RepoMesh 的任务表，尝试 id 用新的 uuid，别复用 b6e0bc59 那个目录。
- 回复用中文，代码和英文文档保持英文。
```

## 本会话的裁决速览（细节全在 spec §3）

Leader 只审不定 · 独立复验器起一次性容器 · 工作区在 `/work/<attempt>` · 第一阶段观察原生 ack/submit ·
验证调度复用 `runner_dispatches`（`adapterId=repomesh-verifier`）· api 在 ACCEPT 时物化候选工作树 · 第一阶段保留 MCP 投影 ·
不要求组织 Manager 容器运行 · `/health/ready` 不改 503 · 波次 4 把启动逻辑归 bootstrap 容器。

## 环境实况

与 `2026-09-02-handoff-solution-design.md` §2 相同，本会话没有动它。新增未提交文件：本文件、spec、验收剧本、截图目录及其 README、
`docs/development/agentteams-native-execution-mode-purpose-20260902.md` 的更新版。
