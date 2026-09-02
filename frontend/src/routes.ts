import type { NavKey } from "./components/SidebarV2";

/** v2 的 hash 路由（不引入路由库）。
 *  抽成独立模块而非留在 ConsoleShell 里，是为了能被直接验证——组件文件按
 *  react(only-export-components) 只能导出组件。 */

export const NAV_HASH: Record<NavKey, string> = {
  issues: "#/issues",
  reviews: "#/reviews",
  repositories: "#/repositories",
  teams: "#/teams",
  agents: "#/agents",
  observe: "#/observe",
  "decision-chains": "#/decision-chains",
  settings: "#/settings",
};

/** 观测中心的板块子页面。null = 门户总览（#/observe）。
 *  每个板块对应赛题可观测要求的一个覆盖面：
 *  - trace  推理轨迹（Skill/MCP/Agent 会话——赛题点名覆盖项，路线 1）
 *  - usage  用量大盘（Metrics：LLM token/成本/延迟/成功率）
 *  - logs   统一日志（Log：结构化日志查询，含按 Issue 分组视图）
 *  - alerts 在线告警（阈值规则 + 触发历史） */
export type ObserveSection = "trace" | "usage" | "logs" | "alerts";
export type SettingsSection = "local-cli";

export const OBSERVE_SECTIONS: ReadonlyArray<ObserveSection> = [
  "trace",
  "usage",
  "logs",
  "alerts",
];

export interface Route {
  nav: NavKey;
  /** #/issues/{issue_id} 命中时为该 id；列表页为 null */
  issueId: string | null;
  /** #/issues/{issue_id}/rooms/{room_id} 命中时为该 room_id；否则 null。
   *  room_id 形如 `!room-core-team:local`，含 `!` 与 `:`，写入 hash 前须编码 */
  roomId: string | null;
  /** nav === "observe" 时的板块子页；null = 门户总览 */
  observeSection: ObserveSection | null;
  /** nav === "settings" 时的子页；null = 设置总览 */
  settingsSection: SettingsSection | null;
}

/** B7：hash 可能被手改/外部粘贴出裸 `%`——decodeURIComponent 抛 URIError 的话
 *  `readRoute` 在首屏渲染期执行会直接白屏。坏段原样返回，比白屏诚实。 */
function safeDecode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function parseRoute(hash: string): Route {
  const h = hash.replace(/^#/, "");

  // 房间路径必须先于 issue 详情匹配，否则会被 `/issues/{id}` 的前缀吃掉
  const room = h.match(/^\/issues\/([^/?]+)\/rooms\/([^/?]+)/);
  if (room) {
    return {
      nav: "issues",
      issueId: safeDecode(room[1]),
      roomId: safeDecode(room[2]),
      observeSection: null,
      settingsSection: null,
    };
  }

  const detail = h.match(/^\/issues\/([^/?]+)/);
  if (detail)
    return {
      nav: "issues",
      issueId: safeDecode(detail[1]),
      roomId: null,
      observeSection: null,
      settingsSection: null,
    };

  if (/^\/settings\/local-cli(?:[/?]|$)/.test(h)) {
    return {
      nav: "settings",
      issueId: null,
      roomId: null,
      observeSection: null,
      settingsSection: "local-cli",
    };
  }

  // 观测板块子页：#/observe/usage|logs|alerts|trace（未知段回落门户）
  const observe = h.match(/^\/observe\/([^/?]+)/);
  if (observe) {
    const section = observe[1] as ObserveSection;
    return {
      nav: "observe",
      issueId: null,
      roomId: null,
      observeSection: OBSERVE_SECTIONS.includes(section) ? section : null,
      settingsSection: null,
    };
  }

  // 未知 hash 回落到 issue 列表，不留半死路由
  const found = (Object.keys(NAV_HASH) as NavKey[]).find((k) => h.startsWith(NAV_HASH[k].slice(1)));
  return {
    nav: found ?? "issues",
    issueId: null,
    roomId: null,
    observeSection: null,
    settingsSection: null,
  };
}

export function readRoute(): Route {
  return parseRoute(window.location.hash);
}
