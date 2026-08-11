import { useEffect, useState } from "react";
import type { ConsoleRepositoryView, TeamRuntimeStatus } from "../api/contract";
import { fetchConsoleRepositories, gridSourceMode } from "../api/grid";
import { dayLabel, shortId } from "../display";

/** 仓库网格页（CONS-44 / 契约 v0.2 §4.1）。
 *
 *  本端点**没有运行时代理**，故不走两段式取数（实测 0.3s 一次取全）。
 *
 *  诚实数据三处：
 *   - 团队芯片只写 `#issue 短版 · 建团结果`。原型写的「#42 结账价格 · 就绪 · 修复中」
 *     有两处无源——issue **标题**不在本端点的响应里（要 join /issues，而 /issues 是
 *     分页的，join 出来的会是部分结果冒充全量），「修复中」是任务态本端点也不给。
 *     芯片改为可点击跳转到该 issue 详情，比一个编出来的标题更有用；
 *   - `auto_card` 契约明写**不投影**（发现证据未按 project 存储），所以没有「证据」
 *     一栏可填——页脚一句话交代，而不是每张卡糊一个「未接入」占位；
 *   - `description` / `topics` / `languages` 是**有源字段**，空就是真的空，
 *     留白即可，不写「未接入」（那是无源字段的措辞，混用会让读者以为数据丢了）。 */

const TEAM_STATUS_LABEL: Record<TeamRuntimeStatus, string> = {
  ready: "建团就绪",
  pending: "建团中",
  failed: "建团失败",
};

const TEAM_STATUS_SKIN: Record<TeamRuntimeStatus, string> = {
  ready: "border-olive text-olive",
  pending: "border-line text-tx2",
  failed: "border-salmon text-salmon",
};

function RepositoryCard({
  repo,
  onOpenIssue,
}: {
  repo: ConsoleRepositoryView;
  onOpenIssue: (issueId: string) => void;
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
        <span className={`ml-auto text-[11.5px] ${idle ? "text-[#6b6046]" : "text-tx2"}`}>
          {repo.resident_team_count} 团队
        </span>
      </div>

      {repo.description && <p className="mt-1 text-[11.5px] text-tx2">{repo.description}</p>}

      <div className="mt-1.5 text-[11px] text-[#6b6046]">{facts}</div>

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
        <div className="mt-2 text-[11px] text-[#6b6046]">
          无驻扎团队 · 团队随 issue 范围确认自动组建
        </div>
      )}
    </div>
  );
}

export function RepositoriesPage({ onOpenIssue }: { onOpenIssue: (issueId: string) => void }) {
  const [repos, setRepos] = useState<ConsoleRepositoryView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setRepos(null);
    setError(null);
    fetchConsoleRepositories()
      .then((rows) => !cancelled && setRepos(rows))
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
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
      </div>

      {error ? (
        <div className="mt-4 rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">仓库网格加载失败</div>
          <p className="text-[12px] text-salmon">{error}</p>
          <button
            className="mt-2 rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={() => setReload((n) => n + 1)}
          >
            重试
          </button>
        </div>
      ) : repos === null ? (
        <p className="py-8 text-center text-[12.5px] text-tx2">加载中…</p>
      ) : repos.length === 0 ? (
        <div className="py-8 text-center text-[12.5px] text-[#6b6046]">
          catalog 里还没有仓库画像（repository_intelligence 未采集）
        </div>
      ) : (
        <div className="mt-4 grid gap-2">
          {repos.map((repo) => (
            <RepositoryCard key={repo.repository_id} repo={repo} onOpenIssue={onOpenIssue} />
          ))}
        </div>
      )}

      <p className="pt-4 text-[11px] text-[#6b6046]">
        团队按「issue × 仓库」自动组建（rm-team-*，teamRoom + leaderDM 双房间）。
        仓库的「发现证据」（auto_card）未按 project 存储，本页不投影 ——
        数据源：{gridSourceMode() === "live" ? "live · GET /console/repositories（契约 v0.2 §4.1）" : "replay 夹具"}
      </p>
    </div>
  );
}
