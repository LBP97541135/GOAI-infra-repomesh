import type {
  DeliveryTaskView,
  IssueDetailView,
  IssueRoundView,
  RollbackScopeView,
  RoomListItemView,
} from "../api/contract";
import type {
  CodeAccessLevel,
  HumanProjectRole,
  ProjectAgentTopologyView,
  ProjectExecutionMode,
} from "../api/humanControl";
import type { DagExecutionView, Decision, PlanAnchor } from "../types";
import { DecisionDeck } from "../components/DecisionDeck";
import { DiscoveryPanel, type MaterializeContext, type PolicyGate } from "../components/DiscoveryPanel";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { PlanDagPanel, type PlanDagState } from "../components/PlanDagPanel";
import { RoundsPanel, type RoundHistoryState } from "../components/RoundsPanel";
import { ErrorPanel, LoadingLine } from "../components/StatusBlocks";
import { CODE_ACCESS_LABEL, CONTROL_ACTION_ORDER, PHASE_SKIN, PHASE_SKIN_FALLBACK, ROLE_LABEL, TEAM_STATUS_LABEL, TEAM_STATUS_SKIN, checkpointLabel, controlActionLabel, dayLabel, eventTime, openedBy, orderByFixed, orderCheckpoints, shortId } from "../display";

/** issue 详情页（CONS-42）。版式按原型 redesign-issue-centric.html 的 `#v-detail`：
 *  标题+元数据+徽标 → 原始需求卡 → 关联仓库·团队芯片 → 房间区（每仓 teamRoom + leaderDM）。
 *
 *  红线：state / phase / phase_note / runtime_status / live 全部由读模型派生，本页只渲染。
 *  下列配色是展示皮肤，取自 Variant D 既有令牌，不是状态映射。
 *
 *  与原型的三处诚实偏差（原型是假数据，live 无源）：
 *   1. 原型的 `#42` 编号 → issue_id 短版（`issue_key` 恒 null，§0/§6.1）；
 *   2. 原型的「王倩 发起于」→ agent 短版（只有 `opened_by_agent_id`，无人名）；
 *   3. 原型的「契约 c3f1a29e」→ 契约投影无 hash 字段，改显 specification 短版 + 版本。
 *
 *  **区块划分即错误边界的粒度（A-4 兜底修）**：下面每个区块都是一个独立组件，
 *  外面各包一层 `<ErrorBoundary>`——一块抛错只塌那一块，其余照常。
 *  这些块此前是本文件里的内联 JSX，**内联 JSX 在父组件的 render 里求值，边界接不住**，
 *  所以必须先拆成组件，边界才真的成立。拆分只挪位置，不动任何渲染逻辑。 */

// X2/X3：八相皮肤与建团三态措辞均用 display.ts 唯一表

function RoomRow({ room, onOpen }: { room: RoomListItemView; onOpen: (room: RoomListItemView) => void }) {
  const empty = room.last_message === null;
  const tag = room.kind === "team_room" ? "TR" : "DM";

  return (
    <button
      className={`flex w-full items-center gap-2.5 border-b border-panel px-3 py-2.5 text-left last:border-b-0 hover:bg-[#241e13] ${
        room.live ? "bg-[#1a160e]" : ""
      }`}
      onClick={() => onOpen(room)}
    >
      <span
        className={`grid size-8 flex-none place-items-center rounded-hard font-mono text-[11px] ${
          empty ? "bg-[#1d1810] text-tx3" : "bg-line text-kraft"
        }`}
      >
        {tag}
      </span>

      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className={`text-[12.5px] ${empty ? "text-tx2" : "text-tx"}`}>
            {room.kind === "team_room" ? "teamRoom" : "leaderDM"}
          </span>
          <span className="text-[10.5px] text-tx2">
            {room.members.length} 成员
            {!room.live && !empty ? " · 静默" : ""}
          </span>
          {room.live && (
            <span
              className="rounded-hard border border-salmon px-1.5 font-mono text-[10px] tracking-[0.1em] text-salmon"
              title="LIVE 由该仓在途任务派生，不是 Matrix presence（契约 v0.2 §5.3）"
            >
              <i className="blink mr-1 inline-block size-[5px] rounded-full bg-salmon align-middle not-italic" />
              LIVE
            </span>
          )}
        </span>
        {/* 空房间不装填占位消息（§5.1） */}
        <span className={`mt-0.5 block truncate text-[11.5px] ${empty ? "text-tx3" : "text-tx2"}`}>
          {empty ? "暂无消息" : room.last_message?.subject}
        </span>
      </span>

      {room.last_message && (
        <span className="flex-none font-mono text-[10.5px] text-tx3">
          {eventTime(room.last_message.at).slice(0, 5)}
        </span>
      )}
    </button>
  );
}

/** 区块①：标题 · 元数据 · 状态徽标。 */
function HeaderBlock({ detail }: { detail: IssueDetailView }) {
  const meta = [
    `#${shortId(detail.issue_id)}`,
    `${openedBy(detail)} 发起于 ${dayLabel(detail.opened_at)}`,
    `第 ${detail.round_count} 轮交付`,
    `${detail.repository_count} 仓`,
  ].join(" · ");

  return (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="text-[17px] leading-[1.4] font-semibold text-tx">{detail.title}</h1>
        <div className="mt-1.5 font-mono text-[11.5px] text-tx2">{meta}</div>
      </div>

      <div className="mt-1 flex flex-none flex-wrap justify-end gap-1.5">
        {/* state 与 phase_note 均由读模型派生（§2.1/§2.2） */}
        <span className="rounded-hard bg-amber px-2 py-px text-[11px] font-semibold text-[#191308]">
          {detail.state === "open" ? "Open" : "Closed"} · {detail.phase_note}
        </span>
        {/* §2.3：需求已落库、尚未物化——与列表行同一徽标，详情页同样提示
            Org Leader 下一步是驱动发现链并物化 */}
        {detail.pending_planning && (
          <span className="rounded-hard border border-amber px-2 py-px text-[11px] text-amber">
            待处理
          </span>
        )}
        {/* §2.1：paused 不影响 state，必须独立徽标呈现 */}
        {detail.operational_status === "paused" && (
          <span className="rounded-hard border border-salmon px-2 py-px text-[11px] text-salmon">已暂停</span>
        )}
        {detail.operational_status === "cancelled" && (
          <span className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">已取消</span>
        )}
        <span className={`rounded-hard border px-2 py-px text-[11px] ${PHASE_SKIN[detail.phase]?.badge ?? PHASE_SKIN_FALLBACK.badge}`}>
          {detail.phase}
        </span>
      </div>
    </div>
  );
}

/** 区块②：原始需求卡。
 *
 *  此处原本还挂着一行「本 issue 设有人工检查点：…」的提示，迁移 5-1a 把它撤了，
 *  由下方「监管策略」段统一承担。撤掉的两个理由都不是版面偏好：
 *   1. 那一行直接 `join` 读模型给的数组，而那份是**字母序**（交付排在执行前面）；
 *      监管策略段按流程先后排。同一页两处同一份卡点排出两种次序，读者只能怀疑其一有误。
 *   2. 它只在卡点非空时才出现——而活体库 14 个项目里 12 个卡点为空，于是这一行
 *      恒不渲染，「这个项目没有任何人工把关」这条最该说的事实反而一个字都没有。 */
function RequirementBlock({ detail }: { detail: IssueDetailView }) {
  return (
    <div className="mt-3.5 rounded-hard border border-line bg-panel px-3.5 py-3">
      <div className="microlabel pb-2">
        原始需求
        {detail.contract
          ? ` · 工程契约 ${shortId(detail.contract.specification_id)} · v${detail.contract.version} · ${detail.contract.status}`
          : " · 工程契约未接入"}
      </div>
      <p className="text-[12.5px] leading-[1.7] text-kraft">
        {detail.requirement_text ?? "需求文本未接入（无 Project 注册表，取自最早 PlanSnapshot）"}
      </p>
    </div>
  );
}

/** 区块④：关联仓库 · 团队芯片。 */
function RepositoryChips({ detail }: { detail: IssueDetailView }) {
  const teamOf = (repositoryId: string) => detail.teams.find((t) => t.repository_id === repositoryId) ?? null;

  return (
    <>
      <div className="microlabel pt-4 pb-2">关联仓库 · 团队</div>
      {/* 空要说出来，不能让区块凭空消失——草稿 issue 尚未确定范围就是这个形态 */}
      {detail.repositories.length === 0 && (
        <p className="text-[12px] text-tx3">尚未确定交付范围（范围由 Org Leader 提议、各仓 Leader 评审后冻结）。</p>
      )}
      <div className="flex flex-wrap gap-2">
        {detail.repositories.map((repo) => {
          const team = teamOf(repo.repository_id);
          const skin = team ? TEAM_STATUS_SKIN[team.runtime_status] : "border-line text-tx2";
          return (
            <span key={repo.repository_id} className={`rounded-hard border px-2 py-px font-mono text-[11px] ${skin}`}>
              {repo.name} · {team ? TEAM_STATUS_LABEL[team.runtime_status] : "无团队"}
              {/* role_in_issue 恒 null：拓扑不记录仓库在 issue 中的角色语义（§3） */}
              {repo.role_in_issue ? ` · ${repo.role_in_issue}` : ""}
            </span>
          );
        })}
      </div>
    </>
  );
}

/** 监管策略段的取数四态 + 就绪态（迁移 5-1a）。
 *
 *  **三个非成功态刻意不合并**，因为它们的下一步完全不同：
 *   - `absent`（服务端 404）= 该 issue 还没有拓扑，**这不是错误**，是「尚未设定」，
 *     同时也是 5-1b 配置入口该出现的条件；
 *   - `forbidden`（403）= 策略存在，只是当前账号读不到。**看不到不等于没有**；
 *   - `error` = 真的取数失败，重试有意义。
 *  糊成一个「加载失败」会把「还没设」说成「坏了」，把「你没权限」说成「不存在」。 */
export type SupervisionState =
  | { status: "loading" }
  | { status: "ready"; topology: ProjectAgentTopologyView }
  | { status: "absent" }
  | { status: "forbidden"; detail: string }
  /** 401：会话过期。**不能混进 `error`**——那一态给的是「重试」按钮，而重试一个
   *  过期会话永远不会成功，按到天亮也没用。会话是有 TTL 的（后端
   *  `session_ttl_seconds`），过期是常态不是故障。 */
  | { status: "unauthenticated"; detail: string }
  | { status: "error"; message: string }
  /** 回放模式：human_control 面没有夹具，这一段改由读模型（此时即夹具）作答。 */
  | { status: "replay" };

/** 拓扑取数四态 → 草稿卡片的门（迁移 5-1b · F4，设计文档 §3.4）。
 *
 *  **这一判只做一次，在这里**：草稿卡片要问的「有没有真档案了」与本段问的是同一个
 *  问题，发现面板自己再取一遍拓扑，就会在物化那一瞬间出现两个互相矛盾的答案。
 *
 *  `forbidden` 归 `sealed` 不是保守处理，是后端语义：`get_project_topology` 的 404
 *  判定在权限判定之前，所以能答 403 就意味着**档案确实存在**（见 `humanControl.ts`
 *  那段三码分述）。`unauthenticated` / `error` / `replay` 一律 `unknown`——不知道就
 *  不出卡片，而不是赌一把「多半还没物化」。 */
function policyGateOf(state: SupervisionState): PolicyGate {
  switch (state.status) {
    case "absent":
      return "open";
    case "ready":
    case "forbidden":
      return "sealed";
    case "loading":
      return "resolving";
    default:
      return "unknown";
  }
}

const EXECUTION_MODE_LABEL: Record<ProjectExecutionMode, string> = {
  auto: "全自动",
  supervised: "人工监督",
  manual_controlled: "全程人工把关",
};

/** 展示皮肤，不是状态映射。全自动走中性色——它是默认值，不是告警；
 *  另两档用琥珀，与页面上其他「有人要动手」的标记同色。 */
const EXECUTION_MODE_SKIN: Record<ProjectExecutionMode, string> = {
  auto: "border-line text-tx2",
  supervised: "border-amber text-amber",
  manual_controlled: "border-amber text-amber",
};

/** 三档的释义**照后端域不变量写**（`modules/project/domain.py` 的 `__post_init__`
 *  与 `human_control.py` 的 `requires_human_checkpoint`），不是界面自己的解读：
 *   - auto 带任何卡点会被域直接拒，所以「全自动」必然等于零卡点；
 *   - 非 auto 时 `exception_escalation` 恒需人工，哪怕它不在 required_checkpoints 里；
 *   - manual_controlled 必须集齐六个卡点，否则后端不收。 */
const EXECUTION_MODE_MEANING: Record<ProjectExecutionMode, string> = {
  auto: "Agent 从规格一路跑到交付，中途不会为任何一步停下来等人——这个项目不会产生任何人工审核待办。",
  supervised: "只在下列检查点上停下来等人审，其余步骤自动。异常升级即使不在列表里也照样停。",
  manual_controlled: "六个检查点全部生效（后端强制集齐，少一个都不收），每一步都要人点头。",
};

// 身份 / 代码权限 / 控制动作三张措辞表已移入 `display.ts`（5-1b）：配置弹窗要用
// 同一批词，抄第二份就会漂移。

/** 授权行 + **它来自哪个来源**。
 *
 *  `partial` 不是修饰词，是行的一半含义：读模型 §3 的 human_grants 只有
 *  principal / role / code_access 三列，`control_actions`、`repository_id`、
 *  `path_patterns` 三列**根本不在那份投影里**。
 *
 *  **为什么不用「字段填 null」表示缺列**：初版这么做过，`control_actions` 那一列
 *  处理对了（`null` 显示「本来源不带这一列」），另两列却漏了——`repository_id === null`
 *  在拓扑来源下的真实含义是「授权覆盖整个项目」，于是缺列被渲染成了「不限范围」。
 *  一个被限死在单仓单目录的仓库监督人，会被显示成全项目监督人。`null` 一个值背两种
 *  意思，漏一处就说反一次，所以来源改由行自己带着，缺列由 `partial` 一处判定。 */
interface PolicyGrant {
  human_principal_id: string;
  role: string;
  code_access: string;
  /** 仅当 `partial` 为 false 时有意义。 */
  control_actions: string[];
  repository_id: string | null;
  path_patterns: string[];
  /** true = 本行来自读模型投影，上面三列它不带，**不得据以判断范围与权限**。 */
  partial: boolean;
}

/** 拓扑端点的授权行 → 共用形状。全列齐备。 */
function topologyGrants(topology: ProjectAgentTopologyView): PolicyGrant[] {
  return topology.human_grants.map((grant) => ({
    human_principal_id: grant.human_principal_id,
    role: grant.role,
    code_access: grant.code_access,
    control_actions: grant.control_actions,
    repository_id: grant.repository_id,
    path_patterns: grant.path_patterns,
    partial: false,
  }));
}

/** 读模型 §3 的授权行 → 共用形状，标记为缺列。 */
function readModelGrants(detail: IssueDetailView): PolicyGrant[] {
  return detail.human_grants.map((grant) => ({
    human_principal_id: grant.human_principal_id,
    role: grant.role,
    code_access: grant.code_access,
    control_actions: [],
    repository_id: null,
    path_patterns: [],
    partial: true,
  }));
}

function GrantRow({ grant }: { grant: PolicyGrant }) {
  return (
    <div className="flex flex-wrap items-baseline gap-2 border-b border-panel py-1.5 last:border-b-0">
      {/* 只有 UUID，没有人名——短版够认，完整 id 挂在 title 上供复制核对 */}
      <span className="font-mono text-[11.5px] text-tx" title={`账号 ${grant.human_principal_id}`}>
        {shortId(grant.human_principal_id)}
      </span>
      <span className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">
        {ROLE_LABEL[grant.role as HumanProjectRole] ?? grant.role}
      </span>
      <span className="text-[11px] text-tx3">
        {CODE_ACCESS_LABEL[grant.code_access as CodeAccessLevel] ?? grant.code_access}
      </span>

      {grant.partial ? (
        /* 缺列一次说清，别让读者从「什么都没显示」里去猜是没有还是不知道 */
        <span className="text-[11px] text-tx3">
          可执行动作与授权范围：本来源不带这三列
        </span>
      ) : (
        <>
          {grant.control_actions.length === 0 ? (
            <span className="text-[11px] text-salmon">未授予任何控制动作——这个人什么也批不了</span>
          ) : (
            orderByFixed(grant.control_actions, CONTROL_ACTION_ORDER).map((action) => (
              <span
                key={action}
                className="rounded-hard border border-line px-1.5 py-px text-[10.5px] text-tx2"
              >
                {controlActionLabel(action)}
              </span>
            ))
          )}

          {/* 授权范围：两者都缺省即「整个项目、不限路径」，为真时才出，免得每行都拖一截。
              这个「缺省即不限」的读法只在全列来源下成立，故整段锁在 !partial 里。 */}
          {grant.repository_id !== null && (
            <span className="font-mono text-[10.5px] text-tx3">限仓库 {shortId(grant.repository_id)}</span>
          )}
          {grant.path_patterns.length > 0 && (
            <span className="font-mono text-[10.5px] text-tx3">限路径 {grant.path_patterns.join("、")}</span>
          )}
        </>
      )}
    </div>
  );
}

/** 策略正文：执行方式 + 人工检查点 + 授权人。两个来源（拓扑端点 / 读模型退化）
 *  共用这一份渲染，免得同一份策略在两条路径下长得不一样。 */
function PolicyBody({
  mode,
  modeAbsentNote,
  checkpoints,
  grants,
  grantsWithheldNote,
}: {
  /** null = 连执行方式都没取到。**不要当成 auto**：默认值是后端的，不是界面的。 */
  mode: ProjectExecutionMode | null;
  /** `mode` 为 null 时说什么。**由调用方给**，因为「为什么没有」按来源不同：
   *  ready = 服务端就是 null；forbidden = 拓扑端点拒答后退化来源这一项为空；
   *  replay = 夹具这一项写的是 null。写死一句「两个来源都没给出」会在后两种下说假话
   *  ——回放只有一个来源，而 403 不是「没给出」是「拒答」。 */
  modeAbsentNote: string;
  checkpoints: string[];
  grants: PolicyGrant[];
  /** 非空 = 授权人名单**刻意不展示**，显示这句话取而代之。 */
  grantsWithheldNote?: string;
}) {
  const ordered = orderCheckpoints(checkpoints);
  // 一个人可以有多条授权（域按 (账号, 仓库) 去重），所以人数与条数不是一回事
  const people = new Set(grants.map((grant) => grant.human_principal_id)).size;

  return (
    <>
      <div className="flex flex-wrap items-baseline gap-2">
        <span
          className={`rounded-hard border px-2 py-px text-[11px] ${
            // 未知档也要有边框色，否则 className 里拼进字面 undefined，chip 变成裸文字，
            // 而旁边的说明正说着「界面还不认识的执行方式」——看起来倒像是渲染坏了
            (mode ? EXECUTION_MODE_SKIN[mode] : null) ?? "border-line text-tx3"
          }`}
        >
          {mode ? EXECUTION_MODE_LABEL[mode] ?? mode : "执行方式未取到"}
        </span>
        <span className="text-[12px] leading-[1.7] text-tx2">
          {mode
            ? EXECUTION_MODE_MEANING[mode] ??
              "服务端给出的是一个界面还不认识的执行方式；含义以后端为准。"
            : modeAbsentNote}
        </span>
      </div>

      <div className="mt-2.5 flex flex-wrap items-baseline gap-x-2 gap-y-1.5">
        <span className="text-[11.5px] text-tx2">人工检查点</span>
        {ordered.length === 0 ? (
          // 空要说出来：一片空白会被读成「没取到」，而这里的空是一条硬事实
          <span className="text-[12px] text-tx3">
            一个都没有——流程上没有任何一步会停下来等人批准。
          </span>
        ) : (
          ordered.map((checkpoint) => (
            <span
              key={checkpoint}
              className="rounded-hard border border-amber px-2 py-px text-[11px] text-amber"
              title={checkpoint}
            >
              {checkpointLabel(checkpoint)}
            </span>
          ))
        )}
      </div>

      <div className="mt-2.5">
        <span className="text-[11.5px] text-tx2">
          授权人
          {!grantsWithheldNote && grants.length > 0 && (
            <span className="ml-2 text-[11px] text-tx3">
              {people} 人{grants.length !== people ? ` · ${grants.length} 条授权` : ""}
            </span>
          )}
        </span>
        {grantsWithheldNote ? (
          <p className="mt-1 text-[12px] leading-[1.7] text-tx3">{grantsWithheldNote}</p>
        ) : grants.length === 0 ? (
          <p className="mt-1 text-[12px] text-tx3">
            没有任何账号被授权在这个项目上做人工决策。
          </p>
        ) : (
          <div className="mt-1">
            {/* key 用下标而非 (账号, 仓库)：域确实按这两者去重（domain.py），但退化来源
                不带 repository_id，同一人的项目级与单仓级两条会撞成同一个 key。 */}
            {grants.map((grant, index) => (
              <GrantRow key={`${grant.human_principal_id}:${index}`} grant={grant} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/** 区块⑤：监管策略（迁移 5-1a，**只读**）。
 *
 *  **为什么有这一段**：控制台此前完全不显示一个 issue 是全自动跑的、还是设了人工卡点。
 *  活体库 14 个项目里 12 个是「全自动 + 零检查点」（物化路径顺手建的拓扑刻意留在默认值，
 *  见 `EnsureProjectAgentTopology` 的类文档），而用户对此一无所知——人工审核台之所以
 *  永远空着，根子就在这里。这一段只负责把这个事实说出来。
 *
 *  **`auto` + 零卡点是一条事实，不是缺口**，所以必须写成一句话而不是渲染成空白：
 *  它的意思是「Agent 不会为任何一步停下来等人」。渲染成空白会被读成「没取到」，
 *  于是这段存在的唯一理由就没了。
 *
 *  **403 时退回读模型，但只退执行方式与卡点两项**：`GET /projects/{id}/topology`
 *  要求管理员或本项目被授权人，而那 12 个全自动项目一条 human_grant 都没有——非管理员
 *  打它必 403。若就此把整段收成一句「你没权限」，最该看见这条事实的那批账号恰好一个字
 *  也看不到。读模型 §3 的 issue 投影与拓扑**同源**（两处都是
 *  `container.topology_reader().get_view(project_id)`，见 `bootstrap/container.py`
 *  的 `_Topology` 与 `api/human_control.py` 的 `get_project_topology`），
 *  且不受本项目授权限制（读模型面的守卫只有全局共享的动作 token），故它补得上。
 *
 *  **但授权人名单不补**。后端那个 403 存在的意义就是不让非成员看见谁被授权；读模型
 *  面的动作 token 是**全控制台共享**的，拿它把名单原样印出来，等于让界面先说
 *  「你读不到完整策略」再把名单摆上桌，对用户自相矛盾，对后端则把 403 变成装饰。
 *  （这份数据本来就随 `GET /issues/{id}` 落到了客户端——见 `contract.ts` 的
 *  `IssueDetailView.human_grants`——所以这不是本段引入的泄露，但也不该由本段扩大。
 *  「动作 token 是否构成可见性边界」是一条独立裁决，未定之前本段按后端的意思办。）
 *
 *  **404 不退回**：404 的含义就是「没有拓扑」，而读模型在同一情形下给出的正是
 *  `execution_mode: null` 与空卡点——同一事实再印一遍只是复述。
 *  ⚠ 初版这里还有一段「两来源不一致，值得排查」的红字警告，**已删**：两处既然同源，
 *  就不存在可排查的后端不一致。唯一能点亮它的是一次读竞态（拓扑先答 404，随后有人
 *  完成物化建出拓扑，issue 详情后到），而 `GET /issues/{id}` 是重聚合、几乎总是后到，
 *  所以那段红字只会把一次一秒内自愈的时序，报成一个查不到的后端故障。取而代之的是
 *  重试入口——那才是这种情形下真正有用的东西。
 *
 *  **人名：不查**。把 `human_principal_id` 显示成人名要调 `GET /auth/accounts`，
 *  而那个端点只有管理员能调。管理员才补名字意味着同一段策略对两个人显示成两个样子，
 *  而看不到名字的恰恰是被授权的**非管理员本人**；更要紧的是，这一段的可见性本来就由
 *  human_grants 决定，再套一层对同一批人注定 403 的请求，只会多一个永远报错的区块。
 *  所以统一显示短 id + 完整 id 挂 title。人名属于 5-1b 的授权人选择器——那里天然是
 *  管理员入口（`POST /projects/topologies` 要 `is_admin`），账号表在那里查才不会白查。 */
function SupervisionBlock({
  detail,
  state,
  onRetry,
}: {
  detail: IssueDetailView;
  state: SupervisionState;
  onRetry: () => void;
}) {
  return (
    <>
      <div className="microlabel pt-5 pb-2">监管策略</div>

      {state.status === "loading" && <LoadingLine text="监管策略读取中…" />}

      {state.status === "unauthenticated" && (
        <>
          <p className="text-[12px] leading-[1.7] text-tx2">{state.detail}</p>
          <p className="mt-1.5 text-[11.5px] leading-[1.7] text-tx3">
            本地登录会话已失效或过期。这一段认的是登录会话，不是页面其余部分用的动作
            token，所以别处还读得到、只有这里读不到。重新登录后即可——
            <b className="text-tx">这里不给重试按钮</b>，重试一个过期的会话不会有别的结果。
          </p>
        </>
      )}

      {state.status === "error" && (
        <ErrorPanel className="mt-0" title="监管策略加载失败" message={state.message} onRetry={onRetry} />
      )}

      {state.status === "absent" && (
        <>
          <p className="text-[12px] leading-[1.7] text-tx2">
            <b className="text-tx">监管策略尚未设定</b>：本 issue 还没有项目拓扑。这不是加载失败——
            「尚未设定」与「设成了全自动」是两回事，前者还改得动。
          </p>
          <p className="mt-1.5 text-[11.5px] leading-[1.7] text-tx3">
            {/* 入口**只有一处**（迁移 5-1b · F6）。这里刻意不再放第二个配置按钮：
                「同一件事两个入口」正是这批迁移一直在还的那笔债，而策略必须挨着
                物化按钮设——它要回答的是「这一次开工，谁来盯着」。 */}
            配置入口在上方「发现」区块的底部：发现链走完后，物化按钮的正上方会出现一张
            「监管策略」卡片，从那里设。⚠ 设定是有时限的：首次物化时后端会按草稿建出档案
            （没设过就是「全自动 + 零卡点」），而全仓没有任何更新拓扑的端点——过了首次物化，
            这个项目的监管策略就定死了。
            <button className="ml-2 text-amber hover:text-amber-hi" onClick={onRetry}>
              重新取一次
            </button>
          </p>
        </>
      )}

      {state.status === "forbidden" && (
        <>
          {/* 服务端 detail 原文在前，它比任何改写都准 */}
          <p className="text-[12px] leading-[1.7] text-tx2">{state.detail}</p>
          <p className="mt-1.5 text-[11.5px] leading-[1.7] text-tx3">
            当前账号既不是管理员，也不在本项目的 human_grants 里，读不到完整策略。
            <b className="text-tx">看不到不等于没有</b>——策略是存在的，只是这个账号无权读它。
            下面执行方式与检查点两项取自读模型的 issue 投影（与拓扑同源）；
            授权人名单不在此展示，那正是这个 403 要挡的东西。
          </p>
          <div className="mt-2">
            <PolicyBody
              mode={detail.execution_mode}
              modeAbsentNote="拓扑端点拒答，退化来源这一项也为空，含义无从判断。"
              checkpoints={detail.required_checkpoints}
              grants={[]}
              grantsWithheldNote="无权查看：授权人名单要管理员或本项目被授权人才读得到。"
            />
          </div>
        </>
      )}

      {state.status === "replay" && (
        <>
          <p className="text-[12px] leading-[1.7] text-tx3">
            回放夹具：human_control 面没有回放数据，下面取自读模型投影（此时即夹具本身）。
            授权明细少 control_actions / repository_id / path_patterns 三列。
          </p>
          <div className="mt-2">
            <PolicyBody
              mode={detail.execution_mode}
              modeAbsentNote="夹具这一项为 null，含义无从判断。"
              checkpoints={detail.required_checkpoints}
              grants={readModelGrants(detail)}
            />
          </div>
        </>
      )}

      {state.status === "ready" && (
        <PolicyBody
          mode={state.topology.execution_mode}
          modeAbsentNote="服务端这一项为空，含义无从判断。"
          checkpoints={state.topology.required_checkpoints}
          grants={topologyGrants(state.topology)}
        />
      )}

      {/* 数据源要报**这一次实际用的那个**。写死成拓扑端点会在 403 与回放两条分支上
          说谎——那两处渲染的是读模型投影，少三列，页脚却宣称来自那个带全列的端点。 */}
      <p className="pt-2.5 text-[11px] leading-[1.7] text-tx3">
        数据源：
        {state.status === "ready" ? (
          <>
            live · GET /projects/{"{issue_id}"}/topology（human_control 面，认本地登录会话，
            不带 Authorization 头；issue_id 即 project_id，契约 §0 语义等式）
          </>
        ) : state.status === "forbidden" ? (
          <>
            live · GET /issues/{"{issue_id}"} 的 §3 投影（拓扑端点答 403，已退化到此来源；
            与拓扑同源但少 control_actions / repository_id / path_patterns 三列，
            授权人名单按后端本意不展示）
          </>
        ) : state.status === "replay" ? (
          <>replay 夹具 · 读模型 §3 投影（human_control 面无回放数据）</>
        ) : state.status === "loading" ? (
          /* 请求还没回来就不下结论。原先 loading 与 absent/error 共用 else 分支，
             首屏会出现「读取中…」和「本次未取到」并排，等于替一个在途请求判了死刑。 */
          <>live · GET /projects/{"{issue_id}"}/topology（读取中）</>
        ) : (
          <>
            live · GET /projects/{"{issue_id}"}/topology（本次未取到，见上方说明）
          </>
        )}
        。<b className="text-tx2">本段只读</b>
        ——改执行方式、加人工检查点、授权审核人在上方「发现」区块底部的「监管策略」卡片，
        且只在<b className="text-tx2">首次物化之前</b>可设（全仓没有更新拓扑的端点）。
      </p>
    </>
  );
}

/** 区块⑥：决策夹（当前轮）。 */
function DeckBlock({
  deck,
  deckHidden,
  deckNote,
  onToggleDeck,
  onBringToFront,
  onDecisionAction,
}: {
  deck: Decision[];
  deckHidden: boolean;
  deckNote: string | null;
  onToggleDeck: () => void;
  onBringToFront: (id: string) => void;
  onDecisionAction: (decision: Decision, actionIdx: number) => void;
}) {
  if (deck.length === 0 && !deckNote) return null;
  return (
    <>
      <div className="microlabel flex items-baseline gap-2 pt-5 pb-2">
        决策夹
        {deckNote && <span className="text-[10px] tracking-normal text-tx3">{deckNote}</span>}
      </div>
      {deck.length > 0 ? (
        <DecisionDeck
          deck={deck}
          hidden={deckHidden}
          onToggleHidden={onToggleDeck}
          onBringToFront={onBringToFront}
          onAction={onDecisionAction}
        />
      ) : (
        <p className="text-[12px] text-tx3">本轮无待决策事项。</p>
      )}
    </>
  );
}

/** 区块⑧：房间区（每仓 teamRoom + leaderDM）。 */
function RoomsBlock({
  detail,
  rooms,
  onOpenRoom,
}: {
  detail: IssueDetailView;
  rooms: RoomListItemView[];
  onOpenRoom: (room: RoomListItemView) => void;
}) {
  const teamOf = (repositoryId: string) => detail.teams.find((t) => t.repository_id === repositoryId) ?? null;

  return (
    <>
      <div className="microlabel pt-5 pb-2">房间</div>
      {rooms.length === 0 && (
        <p className="text-[12px] text-tx3">
          {detail.repositories.length === 0
            ? "尚未建团，暂无房间。团队在范围确认时按「issue × 仓库」自动组建（每团队 teamRoom + leaderDM 双房间）。"
            : "本 issue 的仓库均尚未建团，暂无房间。"}
        </p>
      )}
      {detail.repositories.map((repo) => {
        const team = teamOf(repo.repository_id);
        const group = rooms.filter((r) => r.repository_id === repo.repository_id);

        if (group.length === 0) {
          return (
            <div
              key={repo.repository_id}
              className="mb-2 flex items-baseline gap-2 rounded-hard border border-line px-3 py-2"
            >
              <span className="font-mono text-[11.5px] text-tx2">{repo.name}</span>
              <span className="text-[11px] text-tx3">
                无房间 · {team ? TEAM_STATUS_LABEL[team.runtime_status] : "无团队"}
              </span>
            </div>
          );
        }

        return (
          <div key={repo.repository_id} className="mb-2.5 rounded-hard border border-line">
            <div className="flex items-baseline gap-2 border-b border-line bg-panel px-3 py-2 font-mono text-[11.5px] text-tx">
              {repo.name}
              <span className="text-[10.5px] text-tx3">{team?.agentteams_team_name ?? "团队未接入"}</span>
            </div>
            {group.map((room) => (
              <RoomRow key={room.room_id} room={room} onOpen={onOpenRoom} />
            ))}
          </div>
        );
      })}
    </>
  );
}

export function IssueDetailPage({
  detail,
  rooms,
  deck,
  deckHidden,
  deckNote,
  onToggleDeck,
  onBringToFront,
  onDecisionAction,
  planState,
  planExecution,
  onRetryPlan,
  onPlanGenerated,
  onCandidateAnchor,
  supervision,
  onRetrySupervision,
  materialize,
  onMaterialized,
  onBack,
  onOpenRoom,
  onToast,
  roundsExpanded,
  roundsHistory,
  onToggleRound,
  archiveConfirmId,
  archivingId,
  onArchiveRound,
  onRollbackRound,
  onRedispatchRound,
}: {
  detail: IssueDetailView;
  rooms: RoomListItemView[];
  deck: Decision[];
  deckHidden: boolean;
  /** 决策夹的轮次与数据源说明；null = 该 issue 无轮次，整块不渲染 */
  deckNote: string | null;
  onToggleDeck: () => void;
  onBringToFront: (id: string) => void;
  onDecisionAction: (decision: Decision, actionIdx: number) => void;
  /** 计划 DAG 面板（C-2）的取数四态：加载 / 无快照 / 失败 / 就绪，由容器持有 */
  planState: PlanDagState;
  /** DAG 执行态着色（C-4）：本轮任务展示态按仓一份，null = 无执行事实可着色 */
  planExecution: DagExecutionView | null;
  onRetryPlan: () => void;
  /** 发现面板（B-1/B-2）生成计划成功后刷新计划纸面。与 onRetryPlan 是同一个动作
   *  （重取该 issue 的计划快照），但两个入口的语义不同，故分成两个 prop 各自命名。 */
  onPlanGenerated: () => void;
  /** 锚点回退：发现面板把候选块里的仓库报给容器，容器据此给计划 DAG 面板兜底取数。
   *  本页只做管道——两个面板分属两个取数容器，不许互相 import。 */
  onCandidateAnchor: (anchor: PlanAnchor | null) => void;
  /** 监管策略（迁移 5-1a）：拓扑端点的取数四态，由容器持有。
   *  与其它面板同款单区块降级——它取不到不该影响这一页的任何其它内容。 */
  supervision: SupervisionState;
  onRetrySupervision: () => void;
  /** 物化开工（C-3）：轮次数与计划里的仓库数由容器派生（见 MaterializeContext） */
  materialize: MaterializeContext;
  onMaterialized: () => void;
  onBack: () => void;
  onOpenRoom: (room: RoomListItemView) => void;
  onToast: (text: string) => void;
  /** 轮次索引与跨轮决策（B-6）：展开态与取数结果由容器持有 */
  roundsExpanded: Record<string, boolean>;
  roundsHistory: Record<string, RoundHistoryState>;
  onToggleRound: (round: IssueRoundView) => void;
  /** 轮次归档（B-4）：两步确认态与在途态由容器持有 */
  archiveConfirmId: string | null;
  archivingId: string | null;
  onArchiveRound: (round: IssueRoundView) => void;
  /** 回滚（E-1）：交付卡上的动作，弹窗与提交态由容器持有 */
  onRollbackRound: (round: IssueRoundView, scope: RollbackScopeView) => void;
  /** 重新派工（§8.7.4）：派工卡上的动作，弹窗与提交态由容器持有 */
  onRedispatchRound: (round: IssueRoundView, tasks: DeliveryTaskView[]) => void;
}) {
  // 边界的复位键：容器不随 issue 切换重挂载，不复位的话 A 单的区块错误会挂在 B 单头上
  const key = detail.issue_id;

  return (
    <div className="max-w-[860px]">
      <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
        ‹ issue
      </button>

      <ErrorBoundary block="标题与元数据" resetKey={key}>
        <HeaderBlock detail={detail} />
      </ErrorBoundary>

      <ErrorBoundary block="原始需求" resetKey={key}>
        <RequirementBlock detail={detail} />
      </ErrorBoundary>

      {/* 发现面板（B-1/B-2）：位置按任务书——原始需求卡之后、计划 DAG 之前。
          发现在计划之前发生（需求 → 发现 → 计划），版面顺序照这条时间线。
          面板自持取数与轮询（同 AddRepositoryCard 的取舍），容器只给 issue 与工作区。 */}
      <ErrorBoundary block="发现" resetKey={key}>
        <DiscoveryPanel
          issueId={detail.issue_id}
          issueTitle={detail.title}
          organizationId={detail.organization_id}
          onToast={onToast}
          onPlanGenerated={onPlanGenerated}
          onCandidateAnchor={onCandidateAnchor}
          materialize={materialize}
          policyGate={policyGateOf(supervision)}
          onMaterialized={onMaterialized}
        />
      </ErrorBoundary>

      <ErrorBoundary block="关联仓库 · 团队" resetKey={key}>
        <RepositoryChips detail={detail} />
      </ErrorBoundary>

      {/* 监管策略（5-1a）：紧挨着关联仓库·团队——监管策略和团队编制是同一个项目拓扑
          上的两面（`ProjectAgentTopologyView` 一个对象同时装着 repository_teams 与
          execution_mode/required_checkpoints/human_grants），分开摆会让人以为它们
          来自两处不同的设置。 */}
      <ErrorBoundary block="监管策略" resetKey={key}>
        <SupervisionBlock detail={detail} state={supervision} onRetry={onRetrySupervision} />
      </ErrorBoundary>

      {/* 计划 DAG（C-2 + C-4）：位置按 IA 定稿——issue 详情页新区块，关联仓库芯片
          之后、决策夹之前（先看计划长什么样，再看这一轮要决什么）。物化后节点按
          本轮任务的 display_status 着色（C-4），未物化时维持结构三视觉。 */}
      <ErrorBoundary block="计划 DAG" resetKey={key}>
        <PlanDagPanel state={planState} execution={planExecution} onRetry={onRetryPlan} />
      </ErrorBoundary>

      {/* 决策夹：位置按设计定稿——关联仓库芯片之后、房间区之前。
          决策是轮次粒度，deckNote 说明取的是哪一轮，避免与 issue 级
          pending_decision_count（跨轮求和）被读成同一个数。 */}
      <ErrorBoundary block="决策夹" resetKey={key}>
        <DeckBlock
          deck={deck}
          deckHidden={deckHidden}
          deckNote={deckNote}
          onToggleDeck={onToggleDeck}
          onBringToFront={onBringToFront}
          onDecisionAction={onDecisionAction}
        />
      </ErrorBoundary>

      {/* 轮次索引 + 跨轮决策（B-6）：位置在决策夹（当前轮）之后、房间区之前 */}
      <ErrorBoundary block="轮次" resetKey={key}>
        <RoundsPanel
          detail={detail}
          currentRoundId={detail.active_round_id ?? detail.latest_round_id}
          expanded={roundsExpanded}
          history={roundsHistory}
          archiveConfirmId={archiveConfirmId}
          archivingId={archivingId}
          onToggleRound={onToggleRound}
          onArchiveRound={onArchiveRound}
          onRollbackRound={onRollbackRound}
          onRedispatchRound={onRedispatchRound}
        />
      </ErrorBoundary>

      <ErrorBoundary block="房间" resetKey={key}>
        <RoomsBlock detail={detail} rooms={rooms} onOpenRoom={onOpenRoom} />
      </ErrorBoundary>

      {/* pending_decision_count 是跨轮求和（§2），决策夹只呈现当前一轮：两个数不等时
          说清楚差在哪，别让人以为决策夹漏了事项。查看入口 = 上方轮次区逐轮展开（B-6）。 */}
      {detail.pending_decision_count > deck.length && (
        <div className="pt-4 text-[11.5px] text-tx3">
          该 issue 跨全部 {detail.round_count} 轮共 {detail.pending_decision_count} 项待决策，决策夹只显示当前一轮的{" "}
          {deck.length} 项——其余轮次在上方「轮次」区逐轮展开查看（批准动作仍只在当前轮的决策夹）。
        </div>
      )}
    </div>
  );
}
