import { useCallback, useEffect, useRef, useState } from "react";
import { AuthError, authApi, type Account } from "./api/auth";
import { LoginPage } from "./components/LoginPage";
import { NewIssueModal } from "./components/NewIssueModal";
import { SidebarV2, type NavKey } from "./components/SidebarV2";
import type { IssueListResponse } from "./api/contract";
import { fetchIssues, issuesSourceMode } from "./api/issues";
import { IssueDetailContainer } from "./pages/IssueDetailContainer";
import { IssueListPage } from "./pages/IssueListPage";
import { RoomViewContainer } from "./pages/RoomViewContainer";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { NAV_HASH, readRoute, type Route } from "./routes";
import DeliveryConsole from "./App";

/** v2 控制台外壳（CONS-40）：身份门 → 侧栏导航 → 主区页面。
 *  路由用 hash（#/issues 等），不引入路由库；v1 交付控制台（App.tsx）保留在
 *  #/delivery-v1 可达，供 CONS-42/43 迁移复用，其组件本批不改。 */

export default function ConsoleShell() {
  const [account, setAccount] = useState<Account | null>(null);
  const [authState, setAuthState] = useState<"checking" | "anonymous" | "authenticated" | "unreachable">("checking");
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
          setAuthNote(err instanceof Error ? err.message : String(err));
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
    setIssuesLoading(true);
    setIssuesError(null);
    fetchIssues({ state: issueTab })
      .then((page) => {
        if (cancelled) return;
        setIssues(page);
        setIssuesLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setIssuesError(err instanceof Error ? err.message : String(err));
        setIssuesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authState, issueTab, issuesReload]);

  const loadMoreIssues = () => {
    const cursor = issues?.next_cursor;
    if (!cursor || issuesMore) return;
    setIssuesMore(true);
    fetchIssues({ state: issueTab, cursor })
      .then((page) =>
        // 续读只追加条目；计数是全量值，以最新一页为准即可
        setIssues((prev) => (prev ? { ...page, issues: [...prev.issues, ...page.issues] } : page)),
      )
      .catch((err: unknown) => showToast(`加载更多失败：${err instanceof Error ? err.message : String(err)}`))
      .finally(() => setIssuesMore(false));
  };

  const navigate = (nav: NavKey) => {
    window.location.hash = NAV_HASH[nav];
    setRoute({ nav, deliveryV1: false, issueId: null, roomId: null });
  };

  const openIssue = (issueId: string) => {
    window.location.hash = `#/issues/${issueId}`;
    setRoute({ nav: "issues", deliveryV1: false, issueId, roomId: null });
  };

  const openRoom = (issueId: string, roomId: string) => {
    window.location.hash = `#/issues/${issueId}/rooms/${encodeURIComponent(roomId)}`;
    setRoute({ nav: "issues", deliveryV1: false, issueId, roomId });
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

  // v1 交付控制台自带侧栏，整屏渲染（本批不改其组件；CONS-42/43 迁移后退役）
  if (route.deliveryV1) {
    return (
      <div className="flex h-screen flex-col overflow-hidden bg-ink text-tx">
        <div className="flex flex-none items-center gap-3 border-b border-line bg-ink-deep px-4 py-1.5">
          <button
            className="rounded-hard border border-line px-2 py-px text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={() => navigate("issues")}
          >
            ‹ 返回 issue
          </button>
          <span className="microlabel">v1 交付控制台 · 待 CONS-42/43 迁入 issue 详情</span>
        </div>
        <div className="min-h-0 flex-1">
          <DeliveryConsole />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-ink text-tx">
      <SidebarV2
        account={account}
        nav={route.nav}
        issueCount={issues?.open_count ?? null}
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
        {route.nav === "repositories" && (
          <PlaceholderPage
            title="仓库"
            workItem="CONS-44"
            depends="后端 CONS-32（GET /repositories 拓扑与驻扎团队）"
            note="将展示每仓 · 驻扎团队数 · 团队状态。"
          />
        )}
        {route.nav === "teams" && (
          <PlaceholderPage
            title="团队"
            workItem="CONS-44"
            depends="后端 CONS-32（GET /teams）"
            note="将展示 rm-team-* · 归属仓库 · 所属 issue · 成员及状态。"
          />
        )}
        {route.nav === "agents" && (
          <PlaceholderPage
            title="智能体"
            workItem="CONS-44"
            depends="后端 CONS-32（GET /agents，运行时字段走 AgentTeams Controller 实时代理）"
            note="将展示花名册：状态 / 归属 / 运行时 / 时长。"
          />
        )}
        {route.nav === "settings" && (
          <PlaceholderPage
            title="设置"
            workItem="CONS-44"
            depends="Agent runtime 适配器注册表（只读首版）"
            note="写路径（适配器配置/接入）为二期。"
          />
        )}
      </main>

      <NewIssueModal
        open={newIssueOpen}
        workspaceLabel="工作区未接入"
        onClose={() => setNewIssueOpen(false)}
        onToast={showToast}
      />

      {toast && (
        <div className="fixed bottom-[28px] left-1/2 z-[999] -translate-x-1/2 rounded-hard bg-kraft px-4 py-2 text-[12.5px] text-paper-ink">
          {toast}
        </div>
      )}
    </div>
  );
}
