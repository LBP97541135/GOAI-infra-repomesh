import { useCallback, useEffect, useState } from "react";
import type { ConsoleAgentView, ConsoleRepositoryView } from "../api/contract";
import { fetchConsoleAgents, fetchConsoleRepositories, gridSourceMode } from "../api/grid";
import { TEAM_STATUS_LABEL, TEAM_STATUS_SKIN, dayLabel, errText, shortId } from "../display";
import { AddRepositoryCard } from "../components/AddRepositoryCard";
import { ProvisionTeamModal } from "../components/ProvisionTeamModal";
import { ErrorPanel, LoadingLine, ProbeNote } from "../components/StatusBlocks";

/** 仓库网格页（CONS-44 / 契约 v0.2 §4.1）。
 *
 *  本端点**没有运行时代理**，故不走两段式取数（实测 0.3s 一次取全）。
 *
 *  诚实数据三处：
 *   - 团队芯片只写 `#issue 短版 · 建团结果`。原型写的「#42 结账价格 · 就绪 · 修复中」
 *     有两处无源——issue **标题**不在本端点的响应里（要 join /issues，而 /issues 是
 *     分页的，join 出来的会是部分结果冒充全量），「修复中」是任务态本端点也不给。
 *     芯片改为可点击跳转到该 issue 详情，比一个编出来的标题更有用；
 *   - `auto_card`（发现证据）**按仓库已存、本版不渲染**（M-13 勘正：数据有源，
 *     端点暂不投影是版本取舍）——页脚一句话交代，而不是每张卡糊一个占位；
 *   - `description` / `topics` / `languages` 是**有源字段**，空就是真的空，
 *     留白即可，不写「未接入」（那是无源字段的措辞，混用会让读者以为数据丢了）。 */

// X3：建团三态措辞与皮肤用 display.ts 唯一表（「团队待建」为正）

function RepositoryCard({
  repo,
  staffed,
  canProvision,
  onOpenIssue,
  onProvision,
}: {
  repo: ConsoleRepositoryView;
  /** 该仓库在花名册里是否已有活跃 repository leader；null = 花名册还没取到 */
  staffed: boolean | null;
  /** 工作区已选定才给建团入口——组织是建团的必填参数，服务端不猜 */
  canProvision: boolean;
  onOpenIssue: (issueId: string) => void;
  onProvision: () => void;
}) {
  const idle = repo.resident_team_count === 0;

  const facts = [
    `${repo.open_issue_count} 个进行中 issue`,
    `${repo.active_task_count} 个在途任务`,
    // 没有交付过就没有这个时间戳——不回退到 profiled_at 冒充「最近交付」
    repo.last_delivery_at ? `最近交付 ${dayLabel(repo.last_delivery_at)}` : "尚无交付记录",
  ].join(" · ");

  return (
    <div className={`rounded-hard border border-line px-4 py-3 ${idle ? "bg-ink-deep" : "bg-panel"}`}>
      <div className="flex items-baseline gap-3">
        <span className={`font-mono text-[12.5px] ${idle ? "text-tx2" : "text-tx"}`}>{repo.name}</span>
        {repo.languages.length > 0 && (
          <span className="text-[11px] text-tx2">{repo.languages.join(" / ")}</span>
        )}
        <span className={`ml-auto text-[11.5px] ${idle ? "text-tx3" : "text-tx2"}`}>
          {repo.resident_team_count} 团队
        </span>
      </div>

      {repo.description && <p className="mt-1 text-[11.5px] text-tx2">{repo.description}</p>}

      <div className="mt-1.5 text-[11px] text-tx3">{facts}</div>

      {repo.teams.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {repo.teams.map((team) => (
            <button
              key={team.team_id}
              className={`rounded-hard border px-2 py-px text-[11px] hover:border-amber hover:text-amber-hi ${TEAM_STATUS_SKIN[team.runtime_status]}`}
              title={`issue ${team.issue_id} · 团队 ${team.team_id}`}
              onClick={() => onOpenIssue(team.issue_id)}
            >
              #{shortId(team.issue_id)} · {TEAM_STATUS_LABEL[team.runtime_status] ?? team.runtime_status}
            </button>
          ))}
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-tx3">
          {/* 「无驻扎团队」是拓扑事实（没有 issue 驻扎它），与「有没有建过人」
              是两件事——花名册里可能已有该仓库的 leader。两句话分开说，
              建团入口只按后者出现，避免给出一个注定撞单例 409 的按钮。 */}
          <span>无驻扎团队 · 团队随 issue 范围确认自动组建</span>
          {staffed === true && <span className="text-tx3">· 常驻 leader 已就位</span>}
          {staffed === false &&
            (canProvision ? (
              <button
                className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2 hover:border-amber hover:text-amber-hi"
                onClick={onProvision}
              >
                + 建团
              </button>
            ) : (
              <span className="text-tx3">· 先在左上角选定工作区即可建团</span>
            ))}
        </div>
      )}
    </div>
  );
}

export function RepositoriesPage({
  organizationId,
  onOpenIssue,
  onToast,
}: {
  /** 当前选定工作区；null = 未选定（全部工作区），此时不给建团入口 */
  organizationId: string | null;
  onOpenIssue: (issueId: string) => void;
  onToast: (text: string) => void;
}) {
  const [repos, setRepos] = useState<ConsoleRepositoryView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  /** 花名册：只为判断某仓库是否已有常驻 leader。取用失败不挡仓库网格——
   *  那时 staffed 恒为 null，建团入口整个不出现（宁可少一个按钮，
   *  也不给一个不知道会不会撞车的按钮）。 */
  const [agents, setAgents] = useState<ConsoleAgentView[] | null>(null);
  const [provisionFor, setProvisionFor] = useState<ConsoleRepositoryView | null>(null);

  /** 扫描走到终态后刷新列表。**引用必须稳定**：卡片把它作为轮询 effect 的依赖，
   *  每次 render 换一个新函数会让轮询不断重启。 */
  const refresh = useCallback(() => setReload((n) => n + 1), []);
  const openAdd = useCallback(() => setAddOpen(true), []);

  useEffect(() => {
    let cancelled = false;
    setRepos(null);
    setError(null);
    fetchConsoleRepositories()
      .then((rows) => !cancelled && setRepos(rows))
      .catch((err: unknown) => !cancelled && setError(errText(err)));
    // withRuntime=false：这里只要 role/repository_id 两个持久化字段，
    // 不值得为它等 Controller 探测（契约 §4.3，实测 0.10s vs 2.12s）。
    setAgents(null);
    fetchConsoleAgents(false)
      .then((rows) => !cancelled && setAgents(rows))
      .catch(() => !cancelled && setAgents(null));
    return () => {
      cancelled = true;
    };
  }, [reload]);

  const resident = repos?.filter((r) => r.resident_team_count > 0).length ?? 0;

  return (
    <div className="max-w-[860px]">
      <div className="flex items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-[16px] font-semibold text-cream">仓库</h1>
        {repos && (
          <span className="text-[11.5px] text-tx2">
            {repos.length} 个 · {resident} 个有驻扎团队
          </span>
        )}
        <button
          className="ml-auto rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
          onClick={() => setAddOpen((v) => !v)}
        >
          + 添加仓库
        </button>
      </div>

      {/* 卡片在收起时也保持挂载：轮询活在它内部，收起卡片不该中断一次在跑的扫描 */}
      <AddRepositoryCard
        open={addOpen}
        mode={gridSourceMode()}
        onClose={() => setAddOpen(false)}
        onRestored={openAdd}
        onScanSettled={refresh}
      />

      {error ? (
        <ErrorPanel title="仓库网格加载失败" message={error} onRetry={() => setReload((n) => n + 1)} />
      ) : repos === null ? (
        <LoadingLine />
      ) : repos.length === 0 ? (
        <div className="py-8 text-center text-[12.5px] text-tx3">
          catalog 里还没有仓库画像（repository_intelligence 未采集）
        </div>
      ) : (
        <div className="mt-4 grid gap-2">
          {repos.map((repo) => (
            <RepositoryCard
              key={repo.repository_id}
              repo={repo}
              staffed={
                agents === null
                  ? null
                  : agents.some(
                      (a) =>
                        a.repository_id === repo.repository_id &&
                        a.role === "repository_leader" &&
                        a.status === "active",
                    )
              }
              canProvision={organizationId !== null}
              onOpenIssue={onOpenIssue}
              onProvision={() => setProvisionFor(repo)}
            />
          ))}
        </div>
      )}

      {provisionFor !== null && organizationId !== null && (
        <ProvisionTeamModal
          open
          repositoryId={provisionFor.repository_id}
          repositoryName={provisionFor.name}
          organizationId={organizationId}
          onClose={() => setProvisionFor(null)}
          onProvisioned={refresh}
          onToast={onToast}
        />
      )}

      <p className="pt-4 text-[11px] text-tx3">
        团队按「issue × 仓库」自动组建（repomesh-team-*，teamRoom + leaderDM 双房间）；
        也可在接入后先建常驻团队，两条路径复用同一批人（仓库的 leader 是目录单例）。
        仓库的「发现证据」（auto_card）按仓库已存，本版不渲染 ——
        <ProbeNote sourceNote={gridSourceMode() === "live" ? "live · GET /console/repositories（契约 v0.2 §4.1）" : "replay 夹具"} />
      </p>
    </div>
  );
}
