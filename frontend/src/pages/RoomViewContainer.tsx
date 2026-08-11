import { useEffect, useState } from "react";
import type { RepositoryPlanView, RoomListItemView, RoomStreamPage } from "../api/contract";
import { fetchRepositoryPlan, fetchRoomStream, fetchRooms } from "../api/rooms";
import { resolveDataSourceMode } from "../api/source";
import { RoomView } from "./RoomView";

/** 房间视图取数容器（§5.1 元数据 + §5.2 流 + §5.4 纸面）。
 *
 *  房间元数据从所属 issue 的房间清单里取，而不是靠调用方传——直接粘 URL 进来时
 *  没有上游状态可依赖。未知 room_id 在清单里找不到，呈现明确的「不存在」而非空白。 */
export function RoomViewContainer({
  issueId,
  roomId,
  onBack,
  onToast,
}: {
  issueId: string;
  roomId: string;
  onBack: () => void;
  onToast: (text: string) => void;
}) {
  const [room, setRoom] = useState<RoomListItemView | null>(null);
  const [stream, setStream] = useState<RoomStreamPage | null>(null);
  const [plan, setPlan] = useState<RepositoryPlanView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPlan(null);

    fetchRooms(issueId)
      .then(async (rooms) => {
        const found = rooms.find((r) => r.room_id === roomId);
        if (!found) throw new Error(`房间 ${roomId} 不在该 issue 的房间清单内`);
        const page = await fetchRoomStream(roomId);
        if (cancelled) return;
        setRoom(found);
        setStream(page);
        setLoading(false);
        // 纸面属第二视图，失败不该挡住聊天流——单独取、单独降级
        fetchRepositoryPlan(issueId, found.repository_id)
          .then((p) => {
            if (!cancelled) setPlan(p);
          })
          .catch((err: unknown) => {
            if (!cancelled) onToast(`DAG·PLAN·SPEC 取用失败：${err instanceof Error ? err.message : String(err)}`);
          });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [issueId, roomId, reload, onToast]);

  if (loading) return <p className="py-8 text-center text-[12.5px] text-tx2">加载房间…</p>;

  if (error || !room || !stream) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ 返回 issue
        </button>
        <div className="rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">房间加载失败</div>
          <p className="text-[12px] text-salmon">{error ?? "未取到房间"}</p>
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
    <RoomView
      room={room}
      stream={stream}
      plan={plan}
      sourceNote={
        resolveDataSourceMode() === "live"
          ? "live · GET /rooms/{room_id}/stream（一次性读取，轮询未接入）"
          : "replay 夹具（非实时）"
      }
      onBack={onBack}
      onToast={onToast}
    />
  );
}
