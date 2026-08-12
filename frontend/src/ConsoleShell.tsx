import { useCallback, useEffect, useRef, useState } from "react";
import { NewIssueModal } from "./components/NewIssueModal";
import { SidebarV2, type NavKey } from "./components/SidebarV2";
import type { IssueListResponse, OrganizationView } from "./api/contract";
import { createIssue, fetchIssues, issuesSourceMode } from "./api/issues";
import { createWorkspace, fetchWorkspaces } from "./api/workspaces";
import { errText, shortId } from "./display";
import { AgentsPage } from "./pages/AgentsPage";
import { IssueDetailContainer } from "./pages/IssueDetailContainer";
import { IssueListPage } from "./pages/IssueListPage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { RoomViewContainer } from "./pages/RoomViewContainer";
import { SettingsPage } from "./pages/SettingsPage";
import { TeamsPage } from "./pages/TeamsPage";
import { NAV_HASH, readRoute, type Route } from "./routes";

/** v2 控制台外壳：侧栏导航 → 主区页面。
 *  路由用 hash（#/issues 等），不引入路由库。
 *
 *  **无登录门**（裁决 2026-08-12）：打开即进控制台，身份恒为默认管理员。
 *  数据面本来就不靠登录会话——读模型与写端点全走 `Authorization: Bearer`
 *  动作 token（vite env 注入），原先那道 /auth/me 四态门只是 UX 层的门。
 *  后端 /auth/* 端点仍在（脚本与后续多用户立项可用），前端不再依赖。 */

export default function ConsoleShell() {
  const [route, setRoute] = useState<Route>(readRoute);
  const [newIssueOpen, setNewIssueOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  // issue 列表：state 由服务端筛选（?state=），不做本地分 tab——分页下本地过滤
  // 等于拿部分结果冒充全量。工作区（organization_id）由前端持有，当前无组织读模型
  // （CONS-32）故不传 = 全部工作区。
  const [issueTab, setIssueTab] = useState<"open" | "closed">("open");
  const [issues, setIssues] = useState<IssueListResponse | null>(null);
  const [issuesLoading, setIssuesLoading] = useState(true);
  const [issuesMore, setIssuesMore] = useState(false);
  const [issuesError, setIssuesError] = useState<string | null>(null);
  const [issuesReload, setIssuesReload] = useState(0);

  // 工作区（B-2）：null = 不适用（replay）/取用失败；选中 id 传给 /issues
  // （契约 §2.5：计数受 organization_id 影响，列表与计数必须同一隔离域）。
  const [workspaces, setWorkspaces] = useState<OrganizationView[] | null>(null);
  const [workspaceNote, setWorkspaceNote] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [workspacesReload, setWorkspacesReload] = useState(0);
  /** 列表代际号（A3）：主取数 effect 每次执行 +1，「加载更多」按代际丢弃过期响应 */
  const issuesEpoch = useRef(0);

  const showToast = useCallback((text: string) => {
    setToast(text);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2800);
  }, []);

  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchWorkspaces()
      .then((list) => {
        if (cancelled) return;
        setWorkspaces(list);
        setWorkspaceNote(list === null ? "回放模式 · 工作区不适用" : null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setWorkspaces(null);
        setWorkspaceNote(`工作区取用失败：${errText(err)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [workspacesReload]);

  useEffect(() => {
    let cancelled = false;
    // A3：换 tab/工作区即换代——在途「加载更多」响应按代际丢弃，不污染新列表
    issuesEpoch.current += 1;
    setIssuesLoading(true);
    setIssuesError(null);
    fetchIssues({ state: issueTab, organizationId: workspaceId ?? undefined })
      .then((page) => {
        if (cancelled) return;
        setIssues(page);
        setIssuesLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setIssuesError(errText(err));
        setIssuesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [issueTab, issuesReload, workspaceId]);

  const loadMoreIssues = () => {
    const cursor = issues?.next_cursor;
    if (!cursor || issuesMore) return;
    const epoch = issuesEpoch.current;
    setIssuesMore(true);
    fetchIssues({ state: issueTab, cursor, organizationId: workspaceId ?? undefined })
      .then((page) => {
        if (epoch !== issuesEpoch.current) return; // A3：已切 tab/工作区，丢弃
        // 续读只追加条目；计数是全量值，以最新一页为准即可
        setIssues((prev) => (prev ? { ...page, issues: [...prev.issues, ...page.issues] } : page));
      })
      .catch((err: unknown) => showToast(`加载更多失败：${errText(err)}`))
      .finally(() => setIssuesMore(false));
  };

  const navigate = (nav: NavKey) => {
    window.location.hash = NAV_HASH[nav];
    setRoute({ nav, issueId: null, roomId: null });
  };

  const openIssue = (issueId: string) => {
    window.location.hash = `#/issues/${issueId}`;
    setRoute({ nav: "issues", issueId, roomId: null });
  };

  const openRoom = (issueId: string, roomId: string) => {
    window.location.hash = `#/issues/${issueId}/rooms/${encodeURIComponent(roomId)}`;
    setRoute({ nav: "issues", issueId, roomId });
  };

  /** B-1 创建回路：POST /issues（v0.3 §1）→ 刷新列表 → 跳新 issue 详情。
   *  处理者按当前工作区派生（选「全部」时 null = 花名册唯一活跃 Org Leader）；
   *  幂等键由弹窗持有（A2）。 */
  const handleCreateIssue = async (text: string, idempotencyKey: string) => {
    const issue = await createIssue(text, workspaceId, idempotencyKey);
    setNewIssueOpen(false);
    showToast(`issue 已创建：#${shortId(issue.issue_id)}（虚拟草稿，等待规划）`);
    setIssuesReload((n) => n + 1);
    openIssue(issue.issue_id);
  };

  /** B-2 创建回路：建组织 + 登记 Org Leader → 刷新列表 → 选中新工作区。 */
  const handleCreateWorkspace = async (name: string, idempotencyKey: string) => {
    const created = await createWorkspace(name, idempotencyKey);
    showToast(`工作区已创建：${created.name}（Org Leader 已登记，运行时未拉起）`);
    setWorkspacesReload((n) => n + 1);
    setWorkspaceId(created.organization_id);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-ink text-tx">
      <SidebarV2
        nav={route.nav}
        issueCount={issues?.open_count ?? null}
        workspaces={workspaces}
        workspaceNote={workspaceNote}
        selectedWorkspaceId={workspaceId}
        onSelectWorkspace={setWorkspaceId}
        onCreateWorkspace={handleCreateWorkspace}
        onNavigate={navigate}
        onNewIssue={() => setNewIssueOpen(true)}
        onToast={showToast}
      />

      <main className="min-w-0 flex-1 overflow-y-auto px-8 pt-5 pb-10">
        {route.nav === "issues" &&
          (route.issueId === null ? (
            <IssueListPage
              data={issues}
              tab={issueTab}
              loading={issuesLoading}
              loadingMore={issuesMore}
              error={issuesError}
              sourceNote={
                issuesSourceMode() === "live"
                  ? "数据源：live · GET /issues（契约 v0.2 §2）"
                  : "数据源：replay 夹具 · 加 ?source=live 打真实读模型"
              }
              onTab={setIssueTab}
              onLoadMore={loadMoreIssues}
              onRetry={() => setIssuesReload((n) => n + 1)}
              onOpenIssue={(item) => openIssue(item.issue_id)}
            />
          ) : route.roomId !== null ? (
            <RoomViewContainer
              issueId={route.issueId}
              roomId={route.roomId}
              onBack={() => openIssue(route.issueId!)}
              onToast={showToast}
            />
          ) : (
            <IssueDetailContainer
              issueId={route.issueId}
              onBack={() => navigate("issues")}
              onOpenRoom={(room) => openRoom(route.issueId!, room.room_id)}
              onToast={showToast}
            />
          ))}
        {route.nav === "repositories" && <RepositoriesPage onOpenIssue={openIssue} />}
        {route.nav === "teams" && <TeamsPage onOpenIssue={openIssue} onOpenRoom={openRoom} />}
        {route.nav === "agents" && <AgentsPage onOpenIssue={openIssue} />}
        {route.nav === "settings" && <SettingsPage />}
      </main>

      <NewIssueModal
        open={newIssueOpen}
        workspaceLabel={
          workspaces?.find((w) => w.organization_id === workspaceId)?.name ?? "全部工作区"
        }
        organizationId={workspaceId}
        mode={issuesSourceMode()}
        onClose={() => setNewIssueOpen(false)}
        onToast={showToast}
        onCreate={handleCreateIssue}
      />

      {toast && (
        <div className="fixed bottom-[28px] left-1/2 z-[999] -translate-x-1/2 rounded-hard bg-kraft px-4 py-2 text-[12.5px] text-paper-ink">
          {toast}
        </div>
      )}
    </div>
  );
}
