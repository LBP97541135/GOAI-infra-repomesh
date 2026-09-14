import { useCallback, useEffect, useRef, useState } from "react";
import { Toast } from "./components/Toast";
import { AuthError, authApi, type Account } from "./api/auth";
import { LoginPage } from "./components/LoginPage";
import { SidebarV2, type NavKey } from "./components/SidebarV2";
import { CommandPalette } from "./components/CommandPalette";
import type { IssueListItemView, IssueListResponse, OrganizationView } from "./api/contract";
import { archiveIssue, createIssue, fetchIssues, issuesSourceMode, purgeIssue } from "./api/issues";
import { createWorkspace, fetchWorkspaces } from "./api/workspaces";
import { errText, shortId } from "./display";
import type { HumanReviewRequestView } from "./api/reviewDesk";
import { fetchReviewRequests, subscribeReviewRequests } from "./api/reviewDesk";
import { AgentsPage } from "./pages/AgentsPage";
import { DecisionChainPage } from "./pages/DecisionChainPage";
import { IssueListPage } from "./pages/IssueListPage";
import { ObserveAlerts } from "./pages/observe/ObserveAlerts";
import { ObserveHome } from "./pages/observe/ObserveHome";
import { ObserveLogs } from "./pages/observe/ObserveLogs";
import { ObserveTrace } from "./pages/observe/ObserveTrace";
import { ObserveUsage } from "./pages/observe/ObserveUsage";
import { RepositoriesPage } from "./pages/RepositoriesPage";
import { ReviewDeskPage } from "./pages/ReviewDeskPage";
import { RoomViewContainer } from "./pages/RoomViewContainer";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupWizardPage } from "./pages/SetupWizardPage";
import { fetchSetupStatus } from "./api/platformSetup";
import { TeamsPage } from "./pages/TeamsPage";
import { WorkbenchPage } from "./pages/workbench/WorkbenchPage";
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
  const [setupReady, setSetupReady] = useState<boolean | null>(null);
  const [setupRequested, setSetupRequested] = useState(false);
  const [route, setRoute] = useState<Route>(readRoute);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  // ⌘K/Ctrl+K 命令面板：状态与全局快捷键在外壳（数据源同侧栏的 issues 轮询）。
  const [paletteOpen, setPaletteOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // issue 列表：state 由服务端筛选（?state=），不做本地分 tab——分页下本地过滤
  // 等于拿部分结果冒充全量。工作区（organization_id）由前端持有，当前无组织读模型
  // （CONS-32）故不传 = 全部工作区。
  const [issueTab, setIssueTab] = useState<"open" | "closed">("open");
  // v0.5：已归档开关走服务端过滤（include_archived），不做本地过滤——分页下本地
  // 过滤等于拿部分结果冒充全量，与 tab 的裁决同一条。
  const [showArchived, setShowArchived] = useState(false);
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
    if (authState !== "authenticated") return;
    let cancelled = false;
    fetchSetupStatus()
      .then((status) => !cancelled && setSetupReady(status.ready_for_project_creation))
      .catch(() => !cancelled && setSetupReady(false));
    return () => {
      cancelled = true;
    };
  }, [authState]);

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
    fetchIssues({
      state: issueTab,
      organizationId: workspaceId ?? undefined,
      includeArchived: showArchived,
    })
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
  }, [authState, issueTab, issuesReload, workspaceId, showArchived]);

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
    fetchIssues({
      state: issueTab,
      cursor,
      organizationId: workspaceId ?? undefined,
      includeArchived: showArchived,
    })
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
    setRoute({ nav, issueId: null, roomId: null, observeSection: null, settingsSection: null });
  };

  const openIssue = (issueId: string) => {
    window.location.hash = `#/issues/${issueId}`;
    setRoute({ nav: "issues", issueId, roomId: null, observeSection: null, settingsSection: null });
  };

  /** 新建 issue = 主页对话框：#/issues/new 就是「空流 + 可用输入框」的新会话态，
   *  发送即建 issue 并进入其对话视图（原 NewIssueModal 弹窗已按用户裁决退役）。 */
  const openNewSession = () => {
    window.location.hash = "#/issues/new";
    setRoute({ nav: "issues", issueId: "new", roomId: null, observeSection: null, settingsSection: null });
  };

  const openRoom = (issueId: string, roomId: string) => {
    window.location.hash = `#/issues/${issueId}/rooms/${encodeURIComponent(roomId)}`;
    setRoute({ nav: "issues", issueId, roomId, observeSection: null, settingsSection: null });
  };

  /** B-1 创建回路：POST /issues（v0.3 §1）→ 刷新列表 → 跳新 issue 详情。
   *  处理者按当前工作区派生（选「全部」时 null = 花名册唯一活跃 Org Leader）；
   *  幂等键由弹窗/主页聊天框持有（A2：每次逻辑创建换键，重试沿用同键）。 */
  const handleCreateIssue = async (
    text: string,
    idempotencyKey: string,
    documentFilename: string | null,
  ) => {
    const issue = await createIssue(text, workspaceId, idempotencyKey, documentFilename);
    showToast(`issue 已创建：#${shortId(issue.issue_id)}（虚拟草稿，等待规划）`);
    setIssuesReload((n) => n + 1);
    openIssue(issue.issue_id);
    return issue;
  };

  /** v0.5 归档回路：POST /issues/{id}/archive（墓碑语义，不是删除）→ 刷新列表。
   *  replay 夹具不可篡改（createIssue 同一条红线），入口在页面层已藏、这里兜底。
   *  409（进行中）/其余失败：detail 原文上抛进 toast，不归并措辞。 */
  const handleArchiveIssue = async (item: IssueListItemView) => {
    if (issuesSourceMode() === "replay") {
      showToast("回放模式 · 归档不适用于夹具数据");
      return;
    }
    try {
      await archiveIssue(item.issue_id);
      showToast(`issue 已归档：#${shortId(item.issue_id)}（数据全部保留）`);
      setIssuesReload((n) => n + 1);
    } catch (err) {
      showToast(`归档失败：${errText(err)}`);
    }
  };

  /** 彻底清除回路（2026-09-08 用户裁决）：POST /issues/{id}/purge——不可逆的
   *  硬删除（快照/决策链/审计，仅留一条清除审计）。回放模式同上兜底拒绝。 */
  const handlePurgeIssue = async (item: IssueListItemView) => {
    if (issuesSourceMode() === "replay") {
      showToast("回放模式 · 彻底清除不适用于夹具数据");
      return;
    }
    try {
      const receipt = await purgeIssue(item.issue_id);
      showToast(
        `issue 已彻底清除：#${shortId(item.issue_id)}（快照 ${receipt.snapshots} · ` +
          `决策链 ${receipt.decision_chain_nodes} · 审计 ${receipt.audit_events}）`,
      );
      setIssuesReload((n) => n + 1);
    } catch (err) {
      showToast(`清除失败：${errText(err)}`);
    }
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

  if (setupReady === null) {
    return <div className="grid h-screen place-items-center bg-ink"><p className="microlabel">检查平台配置…</p></div>;
  }

  if ((!setupReady || setupRequested) && account.is_admin) {
    return (
      <SetupWizardPage
        account={account}
        onReady={() => {
          setSetupReady(true);
          setSetupRequested(false);
        }}
      />
    );
  }

  // 聊天工作台占主页新会话（无 hash/#/、#/issues/new）与每个 issue 的会话视图
  // （点列表里的 issue 进来就是每轮对话记录，用户 2026-09-05 裁决；旧详情页已删）。
  // 两者都是全高内滚布局；列表与房间页照旧带页边距。
  const isWorkbenchRoute = route.nav === "issues" && route.issueId !== null && route.roomId === null;

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
        onNewIssue={openNewSession}
        onLogout={handleLogout}
        onToast={showToast}
        onOpenSearch={() => setPaletteOpen(true)}
      />

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        issues={issues?.issues ?? null}
        onNavigate={navigate}
        onOpenIssue={openIssue}
        onNewIssue={openNewSession}
      />

      {/* 工作台自带内滚与吸底输入框：容器不给页边距，交给页面自己（其余页面照旧） */}
      <main
        className={
          isWorkbenchRoute
            ? "flex min-w-0 flex-1 overflow-hidden"
            : "min-w-0 flex-1 overflow-y-auto px-8 pt-5 pb-10"
        }
      >
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
              showArchived={showArchived}
              canArchive={issuesSourceMode() === "live"}
              onTab={setIssueTab}
              onToggleArchived={() => setShowArchived((v) => !v)}
              onArchive={handleArchiveIssue}
              onPurge={handlePurgeIssue}
              onLoadMore={loadMoreIssues}
              onRetry={() => setIssuesReload((n) => n + 1)}
              onOpenIssue={(item) => openIssue(item.issue_id)}
            />
          ) : route.issueId === "new" ? (
            <WorkbenchPage
              issueId={null}
              workspaceName={
                workspaces?.find((w) => w.organization_id === workspaceId)?.name ?? null
              }
              onCreateIssue={handleCreateIssue}
              onOpenRoom={(roomId) => openRoom("new", roomId)}
              onToast={showToast}
            />
          ) : route.roomId !== null ? (
            <RoomViewContainer
              issueId={route.issueId}
              roomId={route.roomId}
              onBack={() => openIssue(route.issueId!)}
              onToast={showToast}
            />
          ) : (
            <WorkbenchPage
              issueId={route.issueId}
              workspaceName={
                workspaces?.find((w) => w.organization_id === workspaceId)?.name ?? null
              }
              onCreateIssue={handleCreateIssue}
              onOpenRoom={(roomId) => openRoom(route.issueId!, roomId)}
              onBack={() => navigate("issues")}
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
        {route.nav === "observe" &&
          (route.observeSection === null ? (
            <ObserveHome />
          ) : route.observeSection === "usage" ? (
            <ObserveUsage />
          ) : route.observeSection === "logs" ? (
            <ObserveLogs />
          ) : route.observeSection === "alerts" ? (
            <ObserveAlerts />
          ) : (
            <ObserveTrace />
          ))}
        {route.nav === "decision-chains" && (
          <DecisionChainPage organizationId={workspaceId} onToast={showToast} />
        )}
        {route.nav === "settings" && (
          <SettingsPage
            key={route.settingsSection ?? "general"}
            account={account}
            onConfigure={() => setSetupRequested(true)}
            initialCategory={route.settingsSection === "local-cli" ? "localcli" : "general"}
          />
        )}
      </main>

      {toast && <Toast text={toast} />}
    </div>
  );
}
