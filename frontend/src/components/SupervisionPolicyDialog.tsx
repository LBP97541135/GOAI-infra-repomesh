import { useCallback, useEffect, useMemo, useState } from "react";
import { AuthError, authApi, type Account } from "../api/auth";
import type { ConsoleRepositoryView, DiscoveryEffectiveTier } from "../api/contract";
import { fetchConsoleRepositories } from "../api/grid";
import {
  deletePolicyDraft,
  fetchPolicyDraft,
  putPolicyDraft,
  type CodeAccessLevel,
  type HumanControlAction,
  type HumanProjectRole,
  type PolicyDraftGrantInput,
  type ProjectExecutionMode,
  type TopologyPolicyDraftView,
} from "../api/humanControl";
import type { ProjectCheckpoint } from "../api/reviewDesk";
import {
  CHECKPOINT_LABEL,
  CHECKPOINT_ORDER,
  CODE_ACCESS_LABEL,
  CONTROL_ACTION_ORDER,
  POLICY_TIER_TITLE,
  ROLE_LABEL,
  controlActionLabel,
  errText,
  type PolicyTier,
} from "../display";
import { Modal } from "./Modal";

/** 「配置监管策略」弹窗（迁移 5-1b，设计文档 §4）。
 *
 *  **它补的是哪一段**：控制台的流程是「提需求 → 自动分析 → 建团+建档案 → 干活」，
 *  建团与建档案在物化那一瞬间同时发生，没有一条缝能让人把监管策略插进去。于是
 *  `EnsureProjectAgentTopology` 只能把三个策略字段留在默认值——活体库 14 个项目里
 *  12 个是「全自动 + 零卡点」，人工审核台恒空（实测 0 行），而**没有任何一个用户
 *  被问过**。本弹窗把那一问补上：它写的是一张独立的草稿表，物化时去读。
 *
 *  **两套凭据在这个文件里同时出现，这是正确的，不是笔误**（设计文档 §4.7）：
 *
 *  | 调用 | 凭据 | 出处 |
 *  | --- | --- | --- |
 *  | 列仓库目录 `GET /console/repositories` | **动作 token** | `api/grid.ts` → `api/client.ts` |
 *  | 列账号、读写草稿 | **管理员会话 cookie** | `api/auth.ts` 的 `sessionRequest` |
 *
 *  ⚠ **不要把其中一处「统一」成另一套**。后端 `_bearer()` 先读 `Authorization` 头、
 *  取不到才回落 cookie：给 human_control 面带上动作 token，会让它拿动作 token 去验
 *  会话，**cookie 明明有效也 401，而且这个失败是静默的**——统一完只会得到一个不报错
 *  的空白面板。反过来给 `/console/repositories` 去掉 Authorization 头则直接 401。
 *
 *  **界面的职责是让 422 不可能发生。** 后端的域不变量有四条（`auto` 必须零卡点、
 *  非 `auto` 至少一个卡点、非 `auto` 至少一条授权、`manual_controlled` 必须六个全），
 *  外加单条授权四条与「人+仓库」判重。这里的三档 + 联动禁用把它们全部排除在可表达
 *  范围之外（映射见 §4.8）。**看见后端 422 就是界面漏了一条**，那时该修界面，
 *  不是把那句话翻译得好看一点。 */

/* ─────────────────────────── 界面三档 ─────────────────────────── */

/** 界面档位。**这是界面概念，不是后端取值**——后端只认
 *  `auto` / `supervised` / `manual_controlled`，而那三个词对刚走完发现链的人毫无意义。
 *  档位由「选了哪些卡点」**推导**（见 `modeOf` / `tierOf`），不单独存一份状态：
 *  存两份就会出现「档位说第二档、卡点却是六个全」这种自相矛盾的中间态。 */
/** ⚠ 档位与它的三个标题**已移入 `display.ts`**（迁移 5-1b · F4）：策略卡片要把
 *  「已设」显示成用户当初在这里选的那一档，两处各写一张表就会漂移。搬走的只是措辞，
 *  本文件的逻辑一个字没动。 */

const TIER_BLURB: Record<PolicyTier, string> = {
  unattended: "从规格一路跑到交付，中途不会为任何一步停下来。",
  key_points: "在确定仓库范围、提交交付这两处停下来等你。",
  every_step: "六个检查点全部生效，每个任务开工前都要你确认。",
};

/** 第二档的预设卡点。
 *
 *  ⚠ **是「仓库范围 + 交付」，不是「规格 + 交付」**——设计文档初稿写错过，§4.2 有
 *  修正框，那是实测出来的：`supervised` + `["specification","delivery"]` 的项目走完
 *  发现链、物化返回 200，`human_review_requests` **仍然是 0 行**；换成含
 *  `repository_scope` 的组合，物化答 409 `human_checkpoint_pending`，审核单从 0 变 1。
 *  原因是「规格」当前根本没有可达的触发点（见 `UNREACHABLE_CHECKPOINTS`）。
 *  **`repository_scope` 是物化路径上唯一可达的卡点**，去掉它这一档就等于第一档。
 *
 *  `exception_escalation` 也在预设里，但它不是一个选择——非 `auto` 时后端恒需人工
 *  （见 `FORCED_CHECKPOINTS`），列进来只是让界面说的和后端做的一致。 */
const TIER_CHECKPOINTS: Record<PolicyTier, readonly ProjectCheckpoint[]> = {
  unattended: [],
  key_points: ["repository_scope", "delivery", "exception_escalation"],
  every_step: CHECKPOINT_ORDER,
};

/** 非 `auto` 时**恒需人工、勾不勾都一样**的卡点。
 *
 *  `requires_human_checkpoint`（后端 `modules/project/human_control.py`）整个函数三行：
 *  `auto` 直接 false；`exception_escalation` 直接 true（**不看 required_checkpoints**）；
 *  其余才查集合。所以这一项在界面上固定勾选并置灰——不这么做，用户会以为不勾就不会
 *  被打断，而他会被打断。 */
const FORCED_CHECKPOINTS: readonly ProjectCheckpoint[] = ["exception_escalation"];

/** 当前**没有可达触发点**的卡点：勾了也不会停（设计文档 §4.3.1 三环查证）。
 *
 *  `ProjectCheckpoint.SPECIFICATION` 的唯一 evaluate 被 `kind is not TASK` 守着，
 *  而全仓唯一的 `publish_to_context` 调用方发布的规格恒为 `kind=TASK`，守卫恒假。
 *
 *  **处置：列出来但标注，不从界面删掉。** 它是合法枚举值、后端照收，删了会让
 *  「界面能表达的」窄于「契约允许的」；而且这是个待修的缺陷（D-8），不是永久语义。
 *  代价预告里按 0 计（见 `CHECKPOINT_STOPS`）。 */
const UNREACHABLE_CHECKPOINTS: readonly ProjectCheckpoint[] = ["specification"];

/** 每个卡点会开出多少张审核单（设计文档 §4.3 逐个 evaluate 调用点实测）。
 *
 *  `"per_task"` 是**唯一随规模线性增长的那个**——所以第二档的预设里不含「执行」，
 *  也所以第三档必须把这件事说出来而不是给一个静态数字。 */
const CHECKPOINT_STOPS: Record<ProjectCheckpoint, number | "per_task"> = {
  repository_scope: 1, // 每项目一次（change_orchestration/application.py）
  specification: 0, // 无可达触发点，见 UNREACHABLE_CHECKPOINTS
  execution: "per_task", // 每个任务一次（task_orchestration/application.py）
  validation: 1, // 每轮一次（scm/plan_delivery.py）
  delivery: 1, // 每轮一次（同上）
  exception_escalation: 0, // 出异常才开，正常路径上不产生待办
};

/** 卡点集合 → 后端执行方式（设计文档 §4.8）。
 *
 *  **升格与降级是这一行推导出来的，不是两条 if**：第二档展开勾满六个自动成为
 *  `manual_controlled`，第三档取消任一项自动回到 `supervised`——用户不需要知道
 *  这两个词的存在，而域不变量「manual_controlled 必须六个全」自动满足。 */
function modeOf(unattended: boolean, checkpoints: readonly ProjectCheckpoint[]): ProjectExecutionMode {
  if (unattended) return "auto";
  return checkpoints.length === CHECKPOINT_ORDER.length ? "manual_controlled" : "supervised";
}

function tierOf(unattended: boolean, checkpoints: readonly ProjectCheckpoint[]): PolicyTier {
  if (unattended) return "unattended";
  return checkpoints.length === CHECKPOINT_ORDER.length ? "every_step" : "key_points";
}

/** 卡点集合永远按 `CHECKPOINT_ORDER` 归一：状态里存一份乱序的集合，界面上就会
 *  出现「同一份选择两次渲染次序不同」。同时这也保证了 `length === 6` 等价于「六个全」。 */
function normalizeCheckpoints(values: Iterable<ProjectCheckpoint>): ProjectCheckpoint[] {
  const chosen = new Set(values);
  return CHECKPOINT_ORDER.filter((item) => chosen.has(item));
}

/** 「估计要点头几次」。
 *
 *  ⚠ **必须标成估计，两处不精确都要说出来**（设计文档 §4.2）：
 *   1. 证据版本变化会**重新开单**（`checkpoint_control.py` 的第二个 `_reviews.ensure`），
 *      所以返工会让实际次数**高于**估算；
 *   2. `T` 取自计划生成那一刻的任务节点数，返工重排会改变它。
 *  不说这两条就是在编造精确度。
 *
 *  `taskCount` 为 null = 计划还没生成，`T` 无从得知。此时**不给一个假的合计数**，
 *  由 `stopsLabel` 如实说「+ 每个任务各 1 次」。 */
function stopsLabel(checkpoints: readonly ProjectCheckpoint[], taskCount: number | null): string {
  let fixed = 0;
  let perTask = false;
  for (const checkpoint of checkpoints) {
    const stops = CHECKPOINT_STOPS[checkpoint];
    if (stops === "per_task") perTask = true;
    else fixed += stops;
  }
  if (!perTask) return `${fixed} 次`;
  if (taskCount === null) return `${fixed} 次 + 每个任务各 1 次（任务数尚未确定）`;
  return `${fixed + taskCount} 次`;
}

/* ─────────────────────────── 三个预设角色 ─────────────────────────── */

/** 后端给的是七个原子动作，直接摆出来让人勾是没法用的——没人知道「恢复项目」和
 *  「暂停项目」该不该一起勾。三个预设角色是这七个的打包（设计文档 §4.5）。
 *
 *  ⚠ **「全权」是六个，不含 `edit_specification`**（§4.5.1）：那个动作声明了权限，
 *  但被它守卫的功能根本不存在——`specification` 模块没有 api 目录、全仓没有规格的
 *  写端点，`authorize_human` 的两个调用点也都不查它。**一个角色不该默认包含一个
 *  不存在的能力**，那是在界面上撒谎。 */
const ROLE_PRESET_ACTIONS: Record<"viewer" | "approver" | "full", readonly HumanControlAction[]> = {
  viewer: ["view_decisions"],
  approver: ["view_decisions", "approve_checkpoint", "request_changes"],
  full: [
    "view_decisions",
    "approve_checkpoint",
    "request_changes",
    "pause_project",
    "resume_project",
    "cancel_project",
  ],
};

const ROLE_PRESET_LABEL: Record<"viewer" | "approver" | "full", string> = {
  viewer: "只看",
  approver: "能批能退",
  full: "全权",
};

/** 全仓**只在枚举定义那一行出现过**的两个动作（§4.5.1 查证：`authorize_human` 的
 *  调用点只有两处，都不查它们）。逐项配置里保留但标注，不隐藏——它们是后端的合法值，
 *  隐藏会让界面能表达的比契约允许的窄。 */
const INERT_ACTIONS: readonly HumanControlAction[] = ["view_decisions", "edit_specification"];

function sameActionSet(a: readonly HumanControlAction[], b: readonly HumanControlAction[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(a);
  return b.every((item) => set.has(item));
}

function presetOf(actions: readonly HumanControlAction[]): "viewer" | "approver" | "full" | null {
  const found = (Object.keys(ROLE_PRESET_ACTIONS) as Array<"viewer" | "approver" | "full">).find(
    (key) => sameActionSet(ROLE_PRESET_ACTIONS[key], actions),
  );
  return found ?? null;
}

/* ─────────────────────────── 授权行 ─────────────────────────── */

interface GrantRow {
  /** 本地行号。**不能用 `human_principal_id` 当 key**——同一个人可以有多条授权
   *  （域按「人 + 仓库」判重，不是按人），撞 key 会让两行合成一行。 */
  key: string;
  /** "" = 还没选人。**不是「不限」**，是一条填不完整的授权，保存前必须拦下。 */
  accountId: string;
  role: HumanProjectRole;
  codeAccess: CodeAccessLevel;
  actions: HumanControlAction[];
  /** "" = 覆盖整个项目（提交时转 null）。 */
  repositoryId: string;
  /** 原始输入，提交时才切分——边输边切会把还没打完的那一段吃掉。 */
  pathText: string;
  expanded: boolean;
}

function newGrantRow(): GrantRow {
  return {
    key: `grant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    accountId: "",
    // 默认「项目监督人」：默认管整个需求，而「仓库监督人」必须指定仓库
    // （`domain.py`），默认成它会让新加的一行天生非法。
    role: "project_supervisor",
    // 默认 `read`：一个只需点头的人不需要改代码的权限。`web/` 的 ProjectSetup
    // 写死 `write` 是过度授权，这批迁移刻意不照抄。
    codeAccess: "read",
    actions: [...ROLE_PRESET_ACTIONS.approver],
    repositoryId: "",
    pathText: "",
    expanded: false,
  };
}

function parsePaths(text: string): string[] {
  return text
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/** 一条授权 → 请求体。**范围两个字段在这里收口**：`repository_id` 空串转 null，
 *  路径在没有仓库时一律空——后端 `HumanProjectGrant.__post_init__` 的第二、三条
 *  （非仓库监督人不许带仓库、有路径必须先有仓库）就是这两处对上的。 */
function toGrantInput(row: GrantRow): PolicyDraftGrantInput {
  const repositoryId = row.role === "repository_supervisor" ? row.repositoryId || null : null;
  return {
    human_principal_id: row.accountId,
    role: row.role,
    code_access: row.codeAccess,
    control_actions: [...row.actions],
    repository_id: repositoryId,
    path_patterns: repositoryId === null ? [] : parsePaths(row.pathText),
  };
}

/* ─────────────────────────── 取数状态 ─────────────────────────── */

type LoadState =
  | { kind: "loading" }
  /** 401：会话过期。**不能混进 `error`**——那一态给「重试」按钮，而重试一个过期会话
   *  永远不会成功，按到天亮也没用。会话有 TTL，过期是常态不是故障。 */
  | { kind: "unauthenticated"; detail: string }
  /** 403：不是管理员。写草稿要 `is_admin`，所以这不是「先看看」的问题，整个弹窗不可用。 */
  | { kind: "forbidden"; detail: string }
  | { kind: "error"; message: string }
  | { kind: "ready" };

export function SupervisionPolicyDialog({
  open,
  projectId,
  issueTitle,
  effectiveTiers,
  taskCount,
  onClose,
  onSaved,
}: {
  open: boolean;
  /** **就是 `issue_id`**（契约 §0 语义等式）——控制台里没有独立于 issue 的项目。 */
  projectId: string;
  issueTitle: string;
  /** 发现链的生效分档（`GET …/discovery` 的 `effective_tiers`）。**只有仓库名字，
   *  没有编号**——仓库下拉的选项由它与仓库目录按名字取交集得来（§4.7）。 */
  effectiveTiers: readonly DiscoveryEffectiveTier[];
  /** `integration.task_dag_count`。计划还没生成时为 null——那时 `T` 未知，
   *  代价预告如实说「每个任务各 1 次」，不拿 0 冒充。 */
  taskCount: number | null;
  onClose: () => void;
  /** 保存或撤回成功后回调，让调用方重取草稿卡片。传 null = 已撤回。 */
  onSaved: (draft: TopologyPolicyDraftView | null) => void;
}) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [repositories, setRepositories] = useState<ConsoleRepositoryView[] | null>(null);
  const [repoError, setRepoError] = useState<string | null>(null);
  /** 服务端已存的那份。非 null → 底部出现「撤回策略」。 */
  const [stored, setStored] = useState<TopologyPolicyDraftView | null>(null);

  const [unattended, setUnattended] = useState(false);
  const [checkpoints, setCheckpoints] = useState<ProjectCheckpoint[]>([
    ...TIER_CHECKPOINTS.key_points,
  ]);
  const [showCheckpoints, setShowCheckpoints] = useState(false);
  const [grants, setGrants] = useState<GrantRow[]>([newGrantRow()]);

  const [busy, setBusy] = useState(false);
  /** 提交失败。`expired` 单独一位，**因为它决定按钮还能不能按**：401 之后再点
   *  「保存策略」永远不会成功（重试一个过期会话不会有别的结果），把它和可重试的
   *  失败混在一起，就是给用户一个按到天亮也不会成功的按钮。 */
  const [submitError, setSubmitError] = useState<{ detail: string; expired: boolean } | null>(null);
  const [confirmWithdraw, setConfirmWithdraw] = useState(false);

  /** **真正会被提交的那份卡点集合。**
   *
   *  强制项在这里补进来，而不是散在每个 setState 里：只要有一条路径忘了补，界面就会
   *  把「异常升级」显示成勾上的、提交出去的清单里却没有它——同一份策略在屏幕上和
   *  库里长得不一样，而这种不一致要等到出异常那天才会被发现。
   *
   *  它同时也是「非 `auto` 至少一个卡点」这条域不变量恒成立的原因：非全自动时这份
   *  集合至少含 `exception_escalation`，永远不可能为空。 */
  const active = useMemo(
    () => (unattended ? [] : normalizeCheckpoints([...checkpoints, ...FORCED_CHECKPOINTS])),
    [unattended, checkpoints],
  );
  const tier = tierOf(unattended, active);
  const mode = modeOf(unattended, active);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoad({ kind: "loading" });
    setSubmitError(null);
    setConfirmWithdraw(false);

    void (async () => {
      // 仓库目录走**动作 token**，与下面两个走会话的请求是两套凭据（见文件头）。
      // 它单独 catch：目录取不到只是「限不了仓库范围」，不该让整个弹窗打不开。
      const repoPromise = fetchConsoleRepositories().then(
        (rows) => ({ rows, error: null as string | null }),
        (err: unknown) => ({ rows: null, error: errText(err) }),
      );
      // 草稿 404 = 还没设过，是正常起点不是错误。其余原样上抛。
      const draftPromise = fetchPolicyDraft(projectId).then(
        (draft) => ({ draft, error: null as unknown }),
        (err: unknown) =>
          err instanceof AuthError && err.status === 404
            ? { draft: null, error: null as unknown }
            : { draft: null, error: err },
      );

      try {
        const [accountRows, repoResult, draftResult] = await Promise.all([
          authApi.accounts(),
          repoPromise,
          draftPromise,
        ]);
        if (cancelled) return;
        if (draftResult.error) throw draftResult.error;

        setAccounts(accountRows.filter((item) => item.active));
        setRepositories(repoResult.rows);
        setRepoError(repoResult.error);
        setStored(draftResult.draft);

        if (draftResult.draft) {
          const draft = draftResult.draft;
          setUnattended(draft.execution_mode === "auto");
          // ⚠ `required_checkpoints` 回来的是 **frozenset 的迭代顺序**（进程内随机），
          // 当集合处理：只做 `has`，顺序一律由 `normalizeCheckpoints` 重排。
          setCheckpoints(normalizeCheckpoints(draft.required_checkpoints));
          setGrants(
            draft.human_grants.length === 0
              ? [newGrantRow()]
              : draft.human_grants.map((grant, index) => ({
                  key: `stored-${index}`,
                  accountId: grant.human_principal_id,
                  role: grant.role,
                  codeAccess: grant.code_access,
                  // control_actions 同样是 frozenset，按展示次序归一
                  actions: CONTROL_ACTION_ORDER.filter((item) =>
                    grant.control_actions.includes(item),
                  ),
                  repositoryId: grant.repository_id ?? "",
                  pathText: grant.path_patterns.join("、"),
                  expanded: false,
                })),
          );
        }
        setLoad({ kind: "ready" });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof AuthError && err.status === 401) {
          setLoad({ kind: "unauthenticated", detail: err.message });
          return;
        }
        if (err instanceof AuthError && err.status === 403) {
          setLoad({ kind: "forbidden", detail: err.message });
          return;
        }
        setLoad({ kind: "error", message: errText(err) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, projectId, reload]);

  /* ── 仓库下拉的选项：仓库目录 ∩ 发现链分档结果，按名字取交集（§4.7） ── */
  const { repoOptions, unresolvedNames } = useMemo(() => {
    // 排除档的仓库不进计划，也就不会进拓扑的仓库集——把它们摆出来只会让用户配出
    // 一条物化时必被拒的授权（后端 `human grant references an unknown project
    // repository`）。
    const planned = effectiveTiers.filter((item) => item.tier !== "excluded").map((item) => item.repository);
    if (repositories === null) return { repoOptions: [], unresolvedNames: [] as string[] };
    const byName = new Map(repositories.map((item) => [item.name, item]));
    return {
      repoOptions: planned.flatMap((name) => {
        const hit = byName.get(name);
        return hit ? [hit] : [];
      }),
      // 解析不到的名字**列出来说明，不静默跳过**。后端 `_ensure_topology` 对解析
      // 不到的名字是静默跳过的——那是它的语境（它别无选择），前端不照抄：这里
      // 静默的后果是用户找不到某个仓库，却看不出为什么。
      unresolvedNames: planned.filter((name) => !byName.has(name)),
    };
  }, [effectiveTiers, repositories]);

  /* ── 校验：这里拦下的每一条，都对应后端的一个 422 ── */
  const rowProblems = useMemo(() => {
    const seen = new Map<string, number>();
    return grants.map((row, index) => {
      if (!row.accountId) return "还没选人——一条授权必须指向一个账号。";
      if (row.role === "repository_supervisor" && !row.repositoryId) {
        return "仓库监督人必须指定一个仓库（后端：repository supervisor requires repository scope）。";
      }
      if (row.actions.length === 0) {
        return "至少要勾一项能干的事，否则这个人什么也批不了（后端：human grant requires control actions）。";
      }
      const scope = `${row.accountId}::${row.role === "repository_supervisor" ? row.repositoryId : ""}`;
      const first = seen.get(scope);
      seen.set(scope, first ?? index);
      if (first !== undefined) {
        return `与第 ${first + 1} 条重复：同一个人在同一范围上只能有一条授权（后端：duplicate human grant scope）。`;
      }
      return null;
    });
  }, [grants]);

  const formProblem = useMemo(() => {
    if (unattended) return null; // 全自动不需要卡点也不需要授权，域不变量对它只有「卡点必须空」
    // 强制项让这一条永远为假；留着是因为「不可能发生」和「没查」在别人读代码时长得一样
    if (active.length === 0) return "非全自动至少要停在一处（后端：human-controlled projects require checkpoints）。";
    if (grants.length === 0) return "非全自动至少要有一位审核人（后端：human-controlled projects require a human grant）。";
    if (rowProblems.some((item) => item !== null)) return "下面有授权还没填完。";
    return null;
  }, [unattended, active, grants, rowProblems]);

  /* ── 档位切换 ── */
  const selectTier = useCallback(
    (next: PolicyTier) => {
      // 点当前这一档不做事：第二档允许自定义卡点，重置会把用户刚勾的东西冲掉
      if (next === tier) return;
      setUnattended(next === "unattended");
      setCheckpoints(normalizeCheckpoints(TIER_CHECKPOINTS[next]));
    },
    [tier],
  );

  const toggleCheckpoint = useCallback((checkpoint: ProjectCheckpoint) => {
    // 强制项不可取消：`exception_escalation` 非 auto 时后端恒需人工，让它看起来
    // 能关掉就是骗人。它同时也是「非 auto 至少一个卡点」永远成立的原因。
    if (FORCED_CHECKPOINTS.includes(checkpoint)) return;
    setCheckpoints((prev) =>
      normalizeCheckpoints(
        prev.includes(checkpoint) ? prev.filter((item) => item !== checkpoint) : [...prev, checkpoint],
      ),
    );
  }, []);

  /* ── 授权行编辑 ── */
  const patchGrant = useCallback((key: string, patch: Partial<GrantRow>) => {
    setGrants((prev) => prev.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }, []);

  const setRole = useCallback(
    (key: string, role: HumanProjectRole) => {
      // 身份↔范围绑死（`domain.py`）：选另两种身份时仓库**禁用并清空**，
      // 只禁不清会留下一个看不见的值，提交时被后端拒——那正是「界面能表达契约
      // 不允许的东西」的样子。路径同理，它依附于仓库。
      setGrants((prev) =>
        prev.map((row) =>
          row.key === key
            ? role === "repository_supervisor"
              ? { ...row, role }
              : { ...row, role, repositoryId: "", pathText: "" }
            : row,
        ),
      );
    },
    [],
  );

  const setRepository = useCallback((key: string, repositoryId: string) => {
    setGrants((prev) =>
      prev.map((row) =>
        row.key === key ? { ...row, repositoryId, pathText: repositoryId ? row.pathText : "" } : row,
      ),
    );
  }, []);

  const toggleAction = useCallback((key: string, action: HumanControlAction) => {
    setGrants((prev) =>
      prev.map((row) =>
        row.key === key
          ? {
              ...row,
              actions: CONTROL_ACTION_ORDER.filter((item) =>
                item === action ? !row.actions.includes(item) : row.actions.includes(item),
              ),
            }
          : row,
      ),
    );
  }, []);

  /* ── 提交 ── */
  const submit = async () => {
    setBusy(true);
    setSubmitError(null);
    try {
      const draft = await putPolicyDraft(projectId, {
        execution_mode: mode,
        // `active` 在 auto 时恒为空——`auto` 带任何卡点会被域直接拒。
        required_checkpoints: active,
        // 全自动不带授权（§4.8）。域并不禁止 auto 带授权，但一个不会停下来的项目
        // 里「审核人」没有任何含义，存下去只会让人以为有人在盯。
        human_grants: unattended ? [] : grants.map(toGrantInput),
      });
      setStored(draft);
      onSaved(draft);
      onClose();
    } catch (err) {
      // detail 原文上抛不归并。⚠ 这里出现域不变量原文就是界面漏了一条约束
      // （见文件头），那是一个待修缺陷，不是给用户看的正常路径。
      setSubmitError({ detail: errText(err), expired: err instanceof AuthError && err.status === 401 });
    } finally {
      setBusy(false);
    }
  };

  const withdraw = async () => {
    if (!confirmWithdraw) {
      setConfirmWithdraw(true);
      return;
    }
    setBusy(true);
    setSubmitError(null);
    try {
      await deletePolicyDraft(projectId);
      onSaved(null);
      onClose();
    } catch (err) {
      setSubmitError({ detail: errText(err), expired: err instanceof AuthError && err.status === 401 });
    } finally {
      setBusy(false);
      setConfirmWithdraw(false);
    }
  };

  const currentStops = stopsLabel(active, taskCount);

  return (
    <Modal
      open={open}
      onClose={onClose}
      className="m-auto w-[min(680px,94vw)] rounded-[3px] border border-[#4a4128] bg-panel p-0 text-tx shadow-[0_24px_70px_rgba(0,0,0,0.7)]"
    >
      <div className="flex items-start justify-between border-b border-line px-[22px] pt-5 pb-3.5">
        <div className="pr-4">
          <span className="eyebrow">SUPERVISION POLICY</span>
          <h2 className="mt-1 text-[16px] font-semibold text-cream">配置监管策略</h2>
          <p className="mt-1 text-[11.5px] text-tx3">需求：{issueTitle}</p>
        </div>
        <button className="text-[18px] text-tx2" aria-label="关闭" onClick={onClose}>
          ×
        </button>
      </div>

      <div className="max-h-[68vh] overflow-y-auto px-[22px] py-4">
        {load.kind === "loading" && <p className="text-[12.5px] text-tx2">读取账号与仓库目录…</p>}

        {load.kind === "unauthenticated" && (
          <>
            <p className="text-[12.5px] leading-[1.75] text-tx2">{load.detail}</p>
            <p className="mt-1.5 text-[11.5px] leading-[1.7] text-tx3">
              本地登录会话已失效或过期。监管策略认的是登录会话，不是页面其余部分用的动作
              token，所以别处还读得到、只有这里读不到。重新登录后再来——
              <b className="text-tx">这里不给重试按钮</b>，重试一个过期的会话不会有别的结果。
            </p>
          </>
        )}

        {load.kind === "forbidden" && (
          <>
            <p className="text-[12.5px] leading-[1.75] text-tx2">{load.detail}</p>
            <p className="mt-1.5 text-[11.5px] leading-[1.7] text-tx3">
              设定监管策略要本地管理员（后端 <code>is_admin</code>）。这不是填写问题，
              换个填法重试没有用——请用管理员账号登录，或让管理员来设。
            </p>
          </>
        )}

        {load.kind === "error" && (
          <>
            <p className="text-[12.5px] leading-[1.75] text-salmon">{load.message}</p>
            <button
              className="mt-2 rounded-hard border border-line px-3 py-1 text-[12px] text-tx2 hover:border-amber hover:text-amber-hi"
              onClick={() => setReload((n) => n + 1)}
            >
              重试
            </button>
          </>
        )}

        {load.kind === "ready" && (
          <>
            {/* ───── 第一问：三档 ───── */}
            <div className="microlabel pb-2">这活谁说了算？</div>

            <div className="flex flex-col gap-2">
              {(["unattended", "key_points", "every_step"] as PolicyTier[]).map((item) => {
                const selected = tier === item;
                const preview = stopsLabel(TIER_CHECKPOINTS[item], taskCount);
                return (
                  <button
                    key={item}
                    type="button"
                    onClick={() => selectTier(item)}
                    className={`rounded-hard border px-3.5 py-3 text-left transition-colors ${
                      selected ? "border-amber bg-amber/10" : "border-line hover:border-tx3"
                    }`}
                  >
                    <div className="flex items-baseline gap-2">
                      <span
                        className={`inline-block h-[9px] w-[9px] shrink-0 rounded-full border ${
                          selected ? "border-amber bg-amber" : "border-tx3"
                        }`}
                      />
                      <span className={`text-[13px] font-semibold ${selected ? "text-cream" : "text-tx"}`}>
                        {POLICY_TIER_TITLE[item]}
                      </span>
                      {item === "key_points" && (
                        <span className="rounded-hard border border-olive px-1.5 py-px text-[10.5px] text-olive">
                          推荐
                        </span>
                      )}
                    </div>
                    <p className="mt-1 pl-[17px] text-[12px] leading-[1.7] text-tx2">{TIER_BLURB[item]}</p>
                    <p className="mt-1 pl-[17px] text-[11.5px] text-tx3">
                      {item === "unattended" ? (
                        // 0 是精确的，不是估计：auto 时 requires_human_checkpoint 恒 false
                        <>▸ 需要你点头 <b className="text-tx2">0 次</b>，这个需求不会产生任何审核待办</>
                      ) : (
                        <>▸ 估计需要你点头 <b className={item === "every_step" ? "text-amber" : "text-tx2"}>{preview}</b>{item === "every_step" ? "，且随任务数增长" : "，不随规模增长"}</>
                      )}
                    </p>
                    {item === "key_points" && (
                      <p className="mt-0.5 pl-[17px] text-[11px] text-tx3">
                        ▸ 异常升级始终启用（非全自动时后端强制，关不掉）
                      </p>
                    )}
                  </button>
                );
              })}
            </div>

            {!unattended && (
              <div className="mt-2.5">
                <button
                  type="button"
                  className="text-[11.5px] text-amber hover:text-amber-hi"
                  onClick={() => setShowCheckpoints((v) => !v)}
                >
                  {showCheckpoints ? "⌃ 收起" : "⌄ 改要停哪几处"}
                  <span className="ml-2 text-tx3">
                    当前选择：估计需要你点头 {currentStops}
                  </span>
                </button>

                {/* 展开面板放在三张卡**下面**而不是第二张卡里（线框是画在卡里的）：
                    档位由卡点集合推导，勾满第六个的瞬间选中的卡会从第二张跳到第三张，
                    面板若在卡里就会跟着整块移位——刚点的那个复选框从鼠标底下跑掉。 */}
                {showCheckpoints && (
                  <div className="mt-2 rounded-hard border border-line px-3.5 py-3">
                    {CHECKPOINT_ORDER.map((checkpoint) => {
                      const forced = FORCED_CHECKPOINTS.includes(checkpoint);
                      const unreachable = UNREACHABLE_CHECKPOINTS.includes(checkpoint);
                      const stops = CHECKPOINT_STOPS[checkpoint];
                      return (
                        <label
                          key={checkpoint}
                          className={`flex items-start gap-2 py-1 ${forced ? "opacity-70" : "cursor-pointer"}`}
                        >
                          <input
                            type="checkbox"
                            className="mt-[3px] accent-amber"
                            checked={active.includes(checkpoint)}
                            disabled={forced}
                            onChange={() => toggleCheckpoint(checkpoint)}
                          />
                          <span className="text-[12px] leading-[1.6]">
                            <span className="text-tx">{CHECKPOINT_LABEL[checkpoint]}</span>
                            <span className="ml-2 text-[11px] text-tx3">
                              {stops === "per_task"
                                ? "每个任务开一张单——这是唯一随规模增长的一项"
                                : stops === 1
                                  ? "每轮/每项目开一张单"
                                  : forced
                                    ? "出异常才开单"
                                    : "不开单"}
                            </span>
                            {forced && (
                              <span className="mt-0.5 block text-[11px] text-tx3">
                                非全自动时<b className="text-tx2">始终启用</b>：后端{" "}
                                <code>requires_human_checkpoint</code>{" "}
                                对这一项直接返回 true，不看这份清单。勾不勾都一样，所以不给关。
                              </span>
                            )}
                            {unreachable && (
                              <span className="mt-0.5 block text-[11px] text-salmon">
                                当前无可达触发点，勾选不会产生审核单——它的 evaluate 被
                                「非 TASK 规格」守着，而全仓唯一的发布方发的恒是 TASK 规格。
                                保留在这里是因为它是后端的合法值，且这是个待修缺陷、不是永久语义。
                              </span>
                            )}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}

                {/* 两处不精确必须说出来，否则就是在编造精确度 */}
                <p className="mt-2 text-[11px] leading-[1.7] text-tx3">
                  ⚠ 这是<b className="text-tx2">估计</b>，实际次数可能更多：证据版本一变会
                  <b className="text-tx2">重新开单</b>，所以返工会让次数高于估算；「每个任务一次」
                  的任务数取自计划生成那一刻，返工重排会改变它。
                </p>
              </div>
            )}

            {/* ───── 第二问：谁来看 ───── */}
            {unattended ? (
              <p className="mt-4 rounded-hard border border-line px-3.5 py-3 text-[12px] leading-[1.75] text-tx3">
                全自动不需要审核人——没有任何一步会停下来等人，「谁来看」这一问对它没有意义。
                {grants.some((row) => row.accountId) && (
                  <b className="mt-1 block text-amber">
                    已填的授权不会被保存：选定「AI 自己干完」时提交的授权清单是空的。
                  </b>
                )}
              </p>
            ) : (
              <>
                <div className="microlabel pt-5 pb-2">谁来看？</div>

                {accounts.length === 0 && (
                  <p className="mb-2 text-[11.5px] leading-[1.7] text-salmon">
                    没有可用的本地账号。先在「设置 → 本地账号」建一个审核人账号，
                    否则这个需求没有人能批。
                  </p>
                )}

                {grants.map((row, index) => {
                  const preset = presetOf(row.actions);
                  const problem = rowProblems[index];
                  const repoLocked = row.role !== "repository_supervisor";
                  return (
                    <div
                      key={row.key}
                      className={`mb-2 rounded-hard border px-3.5 py-3 ${problem ? "border-salmon/60" : "border-line"}`}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="w-[52px] shrink-0 text-[11.5px] text-tx2">人</span>
                        <select
                          className="min-w-[190px] flex-1 rounded-hard border border-line bg-ink px-2 py-1 text-[12.5px] text-tx focus:border-amber focus:outline-none"
                          value={row.accountId}
                          onChange={(e) => patchGrant(row.key, { accountId: e.target.value })}
                        >
                          <option value="">— 选一个账号 —</option>
                          {accounts.map((account) => (
                            <option key={account.id} value={account.id}>
                              {account.display_name}（{account.username}）
                              {account.is_admin ? " · 管理员" : ""}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          className="text-[13px] text-tx3 hover:text-salmon"
                          aria-label="删除这条授权"
                          onClick={() => setGrants((prev) => prev.filter((item) => item.key !== row.key))}
                        >
                          ✕
                        </button>
                      </div>

                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span className="w-[52px] shrink-0 text-[11.5px] text-tx2">能干啥</span>
                        {(["viewer", "approver", "full"] as const).map((key) => (
                          <label key={key} className="flex cursor-pointer items-center gap-1 text-[12px] text-tx">
                            <input
                              type="radio"
                              className="accent-amber"
                              name={`preset-${row.key}`}
                              checked={preset === key}
                              onChange={() => patchGrant(row.key, { actions: [...ROLE_PRESET_ACTIONS[key]] })}
                            />
                            {ROLE_PRESET_LABEL[key]}
                          </label>
                        ))}
                        {preset === null && (
                          <span className="text-[11px] text-tx3">自定义（见逐项配置）</span>
                        )}
                        <button
                          type="button"
                          className="ml-auto text-[11.5px] text-amber hover:text-amber-hi"
                          onClick={() => patchGrant(row.key, { expanded: !row.expanded })}
                        >
                          {row.expanded ? "⌃ 收起逐项配置" : "⌄ 逐项配置"}
                        </button>
                      </div>

                      {row.expanded && (
                        <div className="mt-2.5 border-t border-panel pt-2.5">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="w-[52px] shrink-0 text-[11.5px] text-tx2">身份</span>
                            {(Object.keys(ROLE_LABEL) as HumanProjectRole[]).map((role) => (
                              <label key={role} className="flex cursor-pointer items-center gap-1 text-[12px] text-tx">
                                <input
                                  type="radio"
                                  className="accent-amber"
                                  name={`role-${row.key}`}
                                  checked={row.role === role}
                                  onChange={() => setRole(row.key, role)}
                                />
                                {ROLE_LABEL[role]}
                              </label>
                            ))}
                          </div>

                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <span className="w-[52px] shrink-0 text-[11.5px] text-tx2">管哪个仓库</span>
                            <select
                              className="min-w-[190px] rounded-hard border border-line bg-ink px-2 py-1 text-[12.5px] text-tx focus:border-amber focus:outline-none disabled:opacity-50"
                              value={row.repositoryId}
                              disabled={repoLocked}
                              onChange={(e) => setRepository(row.key, e.target.value)}
                            >
                              <option value="">
                                {repoLocked ? "——（整个项目）" : "— 必选：选一个仓库 —"}
                              </option>
                              {repoOptions.map((repo) => (
                                <option key={repo.repository_id} value={repo.repository_id}>
                                  {repo.name}
                                </option>
                              ))}
                            </select>
                            <span className="text-[11px] text-tx3">
                              {repoLocked
                                ? "只有「仓库监督人」能限定仓库；另两种身份带上仓库会被后端拒，所以这里禁用并清空。"
                                : "选项＝仓库目录 ∩ 本次计划的仓库（按名字取交集）。"}
                            </span>
                          </div>

                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <span className="w-[52px] shrink-0 text-[11.5px] text-tx2">管哪些路径</span>
                            <input
                              className="min-w-[240px] flex-1 rounded-hard border border-line bg-ink px-2 py-1 font-mono text-[12px] text-tx focus:border-amber focus:outline-none disabled:opacity-50"
                              placeholder="src/**、docs/**（留空＝该仓库全部）"
                              value={row.pathText}
                              disabled={!row.repositoryId}
                              onChange={(e) => patchGrant(row.key, { pathText: e.target.value })}
                            />
                            {!row.repositoryId && (
                              <span className="text-[11px] text-tx3">路径范围要先有仓库范围。</span>
                            )}
                          </div>

                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <span className="w-[52px] shrink-0 text-[11.5px] text-tx2">代码权限</span>
                            {(Object.keys(CODE_ACCESS_LABEL) as CodeAccessLevel[]).map((level) => (
                              <label key={level} className="flex cursor-pointer items-center gap-1 text-[12px] text-tx">
                                <input
                                  type="radio"
                                  className="accent-amber"
                                  name={`access-${row.key}`}
                                  checked={row.codeAccess === level}
                                  onChange={() => patchGrant(row.key, { codeAccess: level })}
                                />
                                {CODE_ACCESS_LABEL[level]}
                              </label>
                            ))}
                            <span className="text-[11px] text-tx3">
                              默认「可读代码」：只需点头的人不需要改代码的权限。
                            </span>
                          </div>

                          <div className="mt-2 flex flex-wrap items-start gap-2">
                            <span className="w-[52px] shrink-0 pt-px text-[11.5px] text-tx2">能干哪些事</span>
                            <div className="flex-1">
                              <div className="flex flex-wrap gap-x-4 gap-y-1">
                                {CONTROL_ACTION_ORDER.map((action) => (
                                  <label
                                    key={action}
                                    className="flex cursor-pointer items-center gap-1 text-[12px] text-tx"
                                  >
                                    <input
                                      type="checkbox"
                                      className="accent-amber"
                                      checked={row.actions.includes(action)}
                                      onChange={() => toggleAction(row.key, action)}
                                    />
                                    {controlActionLabel(action)}
                                    {INERT_ACTIONS.includes(action) && (
                                      <span className="text-[10.5px] text-tx3">（无效果）</span>
                                    )}
                                  </label>
                                ))}
                              </div>
                              <p className="mt-1 text-[11px] leading-[1.7] text-tx3">
                                标「无效果」的两项（{controlActionLabel("view_decisions")}、
                                {controlActionLabel("edit_specification")}）
                                <b className="text-tx2">该动作暂无对应功能，勾选不产生效果</b>
                                ——它们在后端枚举里合法，但全仓只在枚举定义那一行出现过：
                                审核台的可见性是按人查的，规格模块也没有任何写端点。
                                所以「全权」预设里不含「{controlActionLabel("edit_specification")}」。
                              </p>
                            </div>
                          </div>
                        </div>
                      )}

                      {problem && <p className="mt-2 text-[11.5px] leading-[1.7] text-salmon">{problem}</p>}
                    </div>
                  );
                })}

                <button
                  type="button"
                  className="rounded-hard border border-line px-3 py-1 text-[12px] text-tx2 hover:border-amber hover:text-amber-hi"
                  onClick={() => setGrants((prev) => [...prev, newGrantRow()])}
                >
                  + 再加一个人
                </button>
                <span className="ml-2 text-[11px] text-tx3">
                  同一个人可以有多条授权，只要「人 + 仓库」这一对不重复。
                </span>

                {repoError && (
                  <p className="mt-2 text-[11.5px] leading-[1.7] text-salmon">
                    仓库目录取不到（{repoError}）——这一段走的是动作 token 那条通道，与账号
                    会话彼此独立。此时<b className="text-tx2">限不了仓库范围</b>，只能配项目级授权。
                  </p>
                )}
                {unresolvedNames.length > 0 && (
                  <p className="mt-2 text-[11.5px] leading-[1.7] text-tx3">
                    本次计划里有 {unresolvedNames.length} 个仓库不在仓库目录中，
                    <b className="text-tx2">无法限定范围</b>：{unresolvedNames.join("、")}。
                    要盯它们只能配项目级授权（或先把它们接入目录）。
                  </p>
                )}
              </>
            )}

            {submitError && (
              <div className="mt-4 rounded-hard border border-salmon/60 bg-salmon/10 px-3 py-2 text-[12px] leading-[1.7] text-salmon">
                {submitError.detail}
                {submitError.expired && (
                  <b className="mt-1 block">
                    登录会话已过期，<b className="underline">再点「保存策略」也不会成功</b>——所以它已被禁用。
                    请重新登录后再打开本弹窗；这里填的内容不会自动保留。
                  </b>
                )}
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-line px-[22px] py-3">
        {load.kind === "ready" && stored !== null && (
          <button
            className="rounded-hard border border-line px-3 py-1.5 text-[12.5px] text-tx3 hover:border-salmon hover:text-salmon disabled:opacity-60"
            onClick={withdraw}
            disabled={busy || (submitError !== null && submitError.expired)}
            title="撤回后这个需求回到「未设定」：首次物化时后端会补一个全自动 + 零卡点的档案"
          >
            {confirmWithdraw ? "再点一次确认撤回" : "撤回策略"}
          </button>
        )}
        <span className="flex-1 text-[11px] text-tx3">
          {load.kind === "ready" && formProblem}
        </span>
        <button
          className="rounded-hard border border-line px-3 py-1.5 text-[12.5px] text-tx2 hover:border-amber hover:text-amber-hi disabled:opacity-60"
          onClick={onClose}
          disabled={busy}
        >
          取消
        </button>
        <button
          className="rounded-hard bg-amber px-3.5 py-1.5 text-[12.5px] font-bold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
          onClick={submit}
          disabled={
            busy ||
            load.kind !== "ready" ||
            formProblem !== null ||
            (submitError !== null && submitError.expired)
          }
        >
          {busy ? "正在保存…" : "保存策略"}
        </button>
      </div>
    </Modal>
  );
}
