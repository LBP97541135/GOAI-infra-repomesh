import { useEffect, useState } from "react";
import type { IssueDetailView, RoomListItemView } from "../api/contract";
import { fetchIssueDetail, fetchRooms } from "../api/rooms";
import { IssueDetailPage } from "./IssueDetailPage";

/** issue 详情取数容器（§3 概览 + §5.1 房间清单）。ConsoleShell 只做路由分发，
 *  取数与加载/失败态收在这里。
 *
 *  空房间清单**不是错误**：未建团的 issue 返回 `{"rooms": []}` 且 HTTP 200，
 *  详情页照常渲染，房间区按仓库显示「无房间」。 */
export function IssueDetailContainer({
  issueId,
  onBack,
  onOpenRoom,
  onToast,
}: {
  issueId: string;
  onBack: () => void;
  onOpenRoom: (room: RoomListItemView) => void;
  onToast: (text: string) => void;
}) {
  const [detail, setDetail] = useState<IssueDetailView | null>(null);
  const [rooms, setRooms] = useState<RoomListItemView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchIssueDetail(issueId), fetchRooms(issueId)])
      .then(([d, r]) => {
        if (cancelled) return;
        setDetail(d);
        setRooms(r);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [issueId, reload]);

  if (loading) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ issue
        </button>
        <p className="py-8 text-center text-[12.5px] text-tx2">加载中…</p>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ issue
        </button>
        <div className="rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">issue 详情加载失败</div>
          <p className="text-[12px] text-salmon">{error ?? "未取到详情"}</p>
          <button
            className="mt-2 rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={() => setReload((n) => n + 1)}
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  return (
    <IssueDetailPage detail={detail} rooms={rooms} onBack={onBack} onOpenRoom={onOpenRoom} onToast={onToast} />
  );
}
