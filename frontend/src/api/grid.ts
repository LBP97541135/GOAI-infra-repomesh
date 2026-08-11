/** 网格 / 团队 / 花名册数据源（契约 v0.2 §4）：live | replay，开关沿用
 *  `resolveDataSourceMode()`。两侧返回同一契约类型，页面无分支。
 *
 *  **两段式加载**（CONS-44 选型，实测支撑）：controller 8090 离线是当前常态，
 *  `with_runtime=true` 的墙钟实测 teams 2.12s / agents 2.11s，而 `false` 只要
 *  0.10s / 0.06s。整页等 2 秒与故障无异，可持久化的那一半本来就秒回——所以首屏
 *  取 `false` 立刻渲染，运行时列另发一次 `true` 回填，该列自带加载态。
 *
 *  这也正是 §4.4 立意的延伸：那里要求「运行时不可达不得拖垮整页」，逐条隔离 +
 *  并发探测（后端 15d9a76）解决了失败传播与串行等待，但 2s 的**有界等待**仍在，
 *  只有把它挪出首屏才真正兑现。 */
import type { ConsoleAgentView, ConsoleRepositoryView, ConsoleTeamView } from "./contract";
import { createApiClient } from "./client";
import { resolveDataSourceMode, type DataSourceMode } from "./source";
import { consoleAgentsFixture, consoleRepositoriesFixture, consoleTeamsFixture } from "../data/grid";

function client() {
  return createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
}

export function gridSourceMode(): DataSourceMode {
  return resolveDataSourceMode();
}

/** B1：teams/agents 的会话级共享缓存（Promise 缓存，与 governanceAgentCache 同一
 *  手法——并发调用也去重）。此前智能体页与设置页各自全量重取，来回切一次就是
 *  4 × 2.1s 的运行时探测。键 = 端点 + with_runtime。
 *
 *  失效（主脑裁决硬性要求）：**创建工作区成功后必须清缓存**——POST
 *  /console/organizations 会同请求登记新的 Org Leader，缓存不失效的话花名册
 *  看不到它，B-2 的闭环显示就是坏的（api/workspaces.ts 调 invalidate）。 */
const gridCache = new Map<string, Promise<unknown>>();

function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
  const hit = gridCache.get(key);
  if (hit) return hit as Promise<T>;
  const pending = load();
  gridCache.set(key, pending);
  // 失败不缓存否定结果：临时不可用不该让本会话永久拿不到花名册
  pending.catch(() => gridCache.delete(key));
  return pending;
}

export function invalidateGridCache(): void {
  gridCache.clear();
}

/** §4.1：本端点没有运行时代理，一次取全，无需两段式。 */
export async function fetchConsoleRepositories(): Promise<ConsoleRepositoryView[]> {
  if (gridSourceMode() === "replay") return consoleRepositoriesFixture;
  return (await client().listConsoleRepositories()).repositories;
}

export async function fetchConsoleTeams(withRuntime: boolean): Promise<ConsoleTeamView[]> {
  if (gridSourceMode() === "replay") return replayRuntime(consoleTeamsFixture, withRuntime);
  return cached(`teams:${withRuntime}`, async () => (await client().listConsoleTeams({ withRuntime })).teams);
}

export async function fetchConsoleAgents(withRuntime: boolean): Promise<ConsoleAgentView[]> {
  if (gridSourceMode() === "replay") return replayRuntime(consoleAgentsFixture, withRuntime);
  return cached(`agents:${withRuntime}`, async () => (await client().listConsoleAgents({ withRuntime })).agents);
}

/** ⚠ **两段式加载的关键陷阱**：`with_runtime=false` 的响应里 `runtime` 也是 `null`
 *  （后端把它初始化为 None 后直接返回，不调 Controller）。而 `null` 在 §4.4 的三态
 *  语义里表示「未配置或 Controller 说没有这个资源」——**两个来源同形**。
 *
 *  所以页面**不得**用 `runtime === null` 判断首屏那一段的运行时状态，否则会把
 *  「还没探测」渲染成「未接入」，即用一个确定的否定结论冒充尚未取得的事实。
 *  判据必须是容器持有的探测阶段（loading / done / failed），只有 done 之后
 *  才允许把 null 解读成「未接入」。同构于 `operational_status: null ≠ active`。 */

/** 夹具侧也要如实模拟 `with_runtime=false`：那时后端**不发任何 Controller 请求**，
 *  整块 runtime 缺省。若夹具在首屏就把运行时给出来，两段式加载的第一段就永远验不到
 *  「探测中」这个真实状态——夹具骗过自己比没有夹具更糟。 */
function replayRuntime<T extends { runtime: unknown }>(rows: T[], withRuntime: boolean): T[] {
  return withRuntime ? rows : rows.map((row) => ({ ...row, runtime: null }));
}
