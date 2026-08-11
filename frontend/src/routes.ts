import type { NavKey } from "./components/SidebarV2";

/** v2 的 hash 路由（不引入路由库）。
 *  抽成独立模块而非留在 ConsoleShell 里，是为了能被直接验证——组件文件按
 *  react(only-export-components) 只能导出组件。 */

export const NAV_HASH: Record<NavKey, string> = {
  issues: "#/issues",
  repositories: "#/repositories",
  teams: "#/teams",
  agents: "#/agents",
  settings: "#/settings",
};

export interface Route {
  nav: NavKey;
  /** #/issues/{issue_id} 命中时为该 id；列表页为 null */
  issueId: string | null;
  /** #/issues/{issue_id}/rooms/{room_id} 命中时为该 room_id；否则 null。
   *  room_id 形如 `!room-core-team:local`，含 `!` 与 `:`，写入 hash 前须编码 */
  roomId: string | null;
}

export function parseRoute(hash: string): Route {
  const h = hash.replace(/^#/, "");

  // 房间路径必须先于 issue 详情匹配，否则会被 `/issues/{id}` 的前缀吃掉
  const room = h.match(/^\/issues\/([^/?]+)\/rooms\/([^/?]+)/);
  if (room) {
    return {
      nav: "issues",
      issueId: decodeURIComponent(room[1]),
      roomId: decodeURIComponent(room[2]),
    };
  }

  const detail = h.match(/^\/issues\/([^/?]+)/);
  if (detail) return { nav: "issues", issueId: decodeURIComponent(detail[1]), roomId: null };

  // 未知 hash（含已退役的 #/delivery-v1）回落到 issue 列表，不留半死路由
  const found = (Object.keys(NAV_HASH) as NavKey[]).find((k) => h.startsWith(NAV_HASH[k].slice(1)));
  return { nav: found ?? "issues", issueId: null, roomId: null };
}

export function readRoute(): Route {
  return parseRoute(window.location.hash);
}
