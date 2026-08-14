import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError, authApi, type Account } from "./api/auth";
import { LoginPage } from "./components/LoginPage";
import { NewIssueModal } from "./components/NewIssueModal";
import { SidebarV2, type NavKey } from "./components/SidebarV2";
import type { IssueListResponse, OrganizationView } from "./api/contract";
import { createIssue, fetchIssues, issuesSourceMode } from "./api/issues";
import { createWorkspace, fetchWorkspaces } from "./api/workspaces";
import { errText, shortId } from "./display";
import type { HumanReviewRequestView } from "./api/reviewDesk";
import { fetchReviewRequests, subscribeReviewRequests } from "./api/reviewDesk";
import { AgentsPage } from "./pages/AgentsPage";
import { IssueDetailContainer } from "./pages/IssueDetailContainer";
import { IssueListPage } from "./pages/IssueListPage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { ReviewDeskPage } from "./pages/ReviewDeskPage";
import { RoomViewContainer } from "./pages/RoomViewContainer";
import { SettingsPage } from "./pages/SettingsPage";
import { TeamsPage } from "./pages/TeamsPage";
import { NAV_HASH, readRoute, type Route } from "./routes";

/** v2 控制台外壳：身份门 → 侧栏导航 → 主区页面。
 *  路由用 hash（#/issues 等），不引入路由库。
 *
 *  **登录门于 2026-08-14 恢复**（裁决推翻 08-12 的「无登录门」）。当初拆门的理由
 *  在当时成立：数据面全走动作 token，那道 `/auth/me` 门只是 UX 层的。这次把它装
 *  回来是因为理由不再成立——main 合并后进来的**建团**与**人工审核台**落在
 *  `human_control` 面上，那一面认的是本地账号会话（建团还要 `is_admin`），共享
 *  动作 token 换不到。控制台从此持两套凭据：数据面仍走动作 token，human_control
 *  面走这道门发的 cookie 会话（见 `api/auth.ts` 顶部为什么不能混用）。
 *
 *  四态与拆门前一致：checking（不闪主界面）/ unreachable（身份服务打不通，可见失败
 *  态而非静默）/ anonymous（登录页）/ authenticated。 */

export default function ConsoleShell() {
  const [account, setAccount] = useState<Account | null>(null);
  const [authState, setAuthState] = useState<"checking" | "anonymous" | "authenticated" | "unreachable">(
    "checking",
  );
  const [authNote, setAuthNote] = useState<string | null>(null);
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

  // 审核待办（迁移 2）。SSE 推 pending 列表，侧栏徽标与页面共用这一份——
  // 两处各取一次会让徽标和列表在刷新间隙互相矛盾。
  const [reviews, setReviews] = useState<HumanReviewRequestView[] | null>(null);
  const [reviewsError, setReviewsError] = useState<string | null>(null);
  const [reviewsStreaming, setReviewsStreaming] = useState(true);
  const [reviewsReload, setReviewsReload] = useState(0);
  /** 列表代际号（A3）：主取数 effect 每次执行 +1，「加载更多」按代际丢弃过期响应 */
  const issuesEpoch = useRef(0);

  const showToast = useCallback((text: string) => {
    setToast(text);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2800);
  }, []);

  useEffect(() => {
    let cancelled = false;
    authApi
      .me()
      .then((acc) => {
        if (cancelled) return;
        setAccount(acc);
        setAuthState("authenticated");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 401 = 未登录（正常）；0/5xx = 身份服务不可达（可见失败态，不静默）
        if (err instanceof AuthError && err.status === 401) setAuthState("anonymous");
        else {
          setAuthNote(errText(err));
          setAuthState("unreachable");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (authState !== "authenticated") return;
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
  }, [authState, workspacesReload]);

  useEffect(() => {
    if (authState !== "authenticated") return;
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
  }, [authState, issueTab, issuesReload, workspaceId]);

  useEffect(() => {
    if (authState !== "authenticated") return;
    let cancelled = false;
    setReviewsError(null);
    // 先一次性取一份垫底：SSE 的首帧要等到 store 有变化或首轮循环，
    // 空手等它会让首屏在两秒里说不清是「没有待办」还是「还没取到」。
    fetchReviewRequests("pending")
      .then((rows) => !cancelled && setReviews(rows))
      .catch((err: unknown) => !cancelled && setReviewsError(errText(err)));
    const unsubscribe = subscribeReviewRequests(
      (rows) => {
        if (cancelled) return;
        setReviews(rows);
        setReviewsStreaming(true);
        setReviewsError(null);
      },
      () => {
        // 流断不清空列表：最后一份结果仍然有用，页面会说明它不再更新。
        if (!cancelled) setReviewsStreaming(false);
      },
    );
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [authState, reviewsReload]);

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

  const handleLogout = () => {
    authApi
      .logout()
      .catch(() => undefined)
      .finally(() => {
        setAccount(null);
        setAuthState("anonymous");
      });
  };

  if (authState === "checking") {
    return (
      <div className="grid h-screen place-items-center bg-ink">
        <p className="microlabel">校验会话…</p>
      </div>
    );
  }

  if (authState === "unreachable") {
    return (
      <div className="grid h-screen place-items-center bg-ink px-6">
        <div className="max-w-[520px] rounded-hard border border-salmon/60 bg-salmon/10 px-5 py-4">
          <div className="eyebrow mb-1.5 text-salmon">身份服务不可达</div>
          <p className="text-[12.5px] text-salmon">{authNote}</p>
          <p className="mt-2 text-[12px] text-tx2">
            控制平面需要本地身份服务（/api/v1/auth）。确认后端已启动后刷新页面。
          </p>
        </div>
      </div>
    );
  }

  if (authState === "anonymous" || !account) {
    return (
      <LoginPage
        onAuthenticated={(acc) => {
          setAccount(acc);
          setAuthState("authenticated");
        }}
      />
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-ink text-tx">
      <SidebarV2
        account={account}
        nav={route.nav}
        issueCount={issues?.open_count ?? null}
        reviewCount={reviews?.length ?? null}
        workspaces={workspaces}
        workspaceNote={workspaceNote}
        selectedWorkspaceId={workspaceId}
        onSelectWorkspace={setWorkspaceId}
        onCreateWorkspace={handleCreateWorkspace}
        onNavigate={navigate}
        onNewIssue={() => setNewIssueOpen(true)}
        onLogout={handleLogout}
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
        {route.nav === "reviews" && (
          <ReviewDeskPage
            rows={reviews}
            error={reviewsError}
            streaming={reviewsStreaming}
            onRefresh={() => setReviewsReload((n) => n + 1)}
            onToast={showToast}
          />
        )}
        {route.nav === "repositories" && (
          <RepositoriesPage
            organizationId={workspaceId}
            onOpenIssue={openIssue}
            onToast={showToast}
          />
        )}
        {route.nav === "teams" && <TeamsPage onOpenIssue={openIssue} onOpenRoom={openRoom} />}
        {route.nav === "agents" && <AgentsPage onOpenIssue={openIssue} />}
        {route.nav === "settings" && <SettingsPage account={account} />}
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
