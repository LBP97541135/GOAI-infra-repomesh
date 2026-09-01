/** 本机启动器客户端（Task 4 的四条固定路由，`http://127.0.0.1:8121`）。
 *
 *  **与 `api/client.ts` 物理隔离，理由不是风格**：那一套打的是 RepoMesh 服务端，
 *  带 `Authorization: Bearer <动作 token>`、同源、路径前缀 `/api/v1`；这一套打的是
 *  操作者**自己机器上**的另一个进程，跨源、无凭据（启动器的门是 Origin 白名单 +
 *  下面这个自定义头，不是 token），路径前缀 `/v1`。把动作 token 送到 loopback 上的
 *  另一个进程去，是把凭据发给一个不需要它的收件人。
 *
 *  写请求上的 `X-RepoMesh-Launcher-Op: 1` **值不是重点，强制预检才是**：带了这个头的
 *  跨源 POST 不再是「简单请求」，浏览器必须先发 OPTIONS 问过才发正文，于是不在白名单
 *  里的页面根本不会让这台机器被问到「起不起」。头缺失或 Origin 不在白名单 → 403。
 *
 *  基地址写死：启动器是 loopback 上的固定端口（config 的 `port` 默认 8121），
 *  而 Origin 白名单在**启动器那一侧**是配置——控制台的开发端口有好几个（5280/5281/
 *  5533），能不能连上由那份白名单说了算，不由前端多一个环境变量说了算。 */

export const LAUNCHER_BASE = "http://127.0.0.1:8121";

const OP_HEADER = "X-RepoMesh-Launcher-Op";
const OP_VALUE = "1";

/** `/v1/status` 的一行：这台机器此刻对一个 roster 成员的答复。
 *  `role` 保持字符串——那是操作者 members.json 里的字面值，启动器一个字都不校验。 */
export interface LauncherMember {
  agentId: string;
  displayName: string;
  role: string;
  running: boolean;
  pid: number | null;
  logPath: string | null;
}

/** 四条路由**共用的同一个响应体**（启动器的 `_answer`）：进程事实 + roster 版本。 */
export interface LauncherStatus {
  rosterVersion: string;
  members: LauncherMember[];
}

/** start / restart 的 409：有 PID 文件挡路。**这一条必须逐成员渲染**——启动器把它
 *  单独结构化出来，就是因为它可自助解决，而解法是「删掉这个文件」，文件名在 body 里。 */
export interface StalePidFileDetail {
  code: "stale_pid_file";
  message: string;
  members: Array<{ displayName: string; pidFile: string }>;
}

export class LauncherError extends Error {
  /** HTTP 状态码；`0` = 没拿到可用应答（请求没发出去，或答话的根本不是启动器）。 */
  readonly status: number;
  /** FastAPI `detail` 原件，供 `stalePidFile` 消费。 */
  readonly detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "LauncherError";
    this.status = status;
    this.detail = detail;
  }
}

async function call(method: string, path: string, op: boolean): Promise<LauncherStatus> {
  const url = `${LAUNCHER_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, { method, headers: op ? { [OP_HEADER]: OP_VALUE } : undefined });
  } catch (cause) {
    throw new LauncherError(
      0,
      `连不上本机启动器 ${LAUNCHER_BASE}：${cause instanceof Error ? cause.message : String(cause)}`,
      null,
    );
  }
  if (!res.ok) {
    const raw = await res.text().catch(() => "");
    let detail: unknown = raw;
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      if (parsed.detail !== undefined) detail = parsed.detail;
    } catch {
      /* 非 JSON 体，原样展示 */
    }
    const text = typeof detail === "string" ? detail : JSON.stringify(detail);
    throw new LauncherError(res.status, `${method} ${path} → HTTP ${res.status}${text ? ` · ${text.slice(0, 200)}` : ""}`, detail);
  }
  try {
    return (await res.json()) as LauncherStatus;
  } catch (cause) {
    // 固定端口上答话的未必是启动器（一个跑串了的 dev server 也会 200 一段 HTML）。
    // 归到 `0`——「没拿到可用应答」，与连不上同一档：那边确实没有一个启动器在。
    // 不是 `refused`，把一个 200 说成「答了一个错误码」是另一句假话。
    // **只判到「不是 JSON」为止**，不再深挖字段：那就成了在前端补一份服务端形状校验。
    throw new LauncherError(
      0,
      `${LAUNCHER_BASE} 上答话的不是启动器：响应不是 JSON（${cause instanceof Error ? cause.message : String(cause)}）`,
      null,
    );
  }
}

/** 探测结果三态。**「没拿到可用应答」与「答了个错误码」必须分开**：前者页面退回命令
 *  卡片（启动器没起、答话的不是启动器，或这个来源不在它的 Origin 白名单里——状态那条
 *  是简单请求，没有预检，请求照发，只是响应被浏览器挡在页面之外，所以这三种在这边
 *  长得一模一样）；后者是启动器就在那儿、这一趟自己出了错，退回命令卡片会把一个可修
 *  的故障说成「没装」。 */
export type LauncherProbe =
  | { kind: "ok"; status: LauncherStatus }
  | { kind: "launcher_unavailable"; message: string }
  | { kind: "refused"; status: number; message: string };

/** 状态探测，**不抛**：它是轮询的那一条，抛出来只会让每个调用点各写一份 catch。 */
export async function probe(): Promise<LauncherProbe> {
  try {
    return { kind: "ok", status: await call("GET", "/v1/status", false) };
  } catch (err) {
    const failure = err as LauncherError; // call 只抛这一种
    return failure.status === 0
      ? { kind: "launcher_unavailable", message: failure.message }
      : { kind: "refused", status: failure.status, message: failure.message };
  }
}

export function startMembers(): Promise<LauncherStatus> {
  return call("POST", "/v1/members/start", true);
}

export function stopMembers(): Promise<LauncherStatus> {
  return call("POST", "/v1/members/stop", true);
}

export function restartMember(agentId: string): Promise<LauncherStatus> {
  return call("POST", `/v1/members/${encodeURIComponent(agentId)}/restart`, true);
}

/** 从写请求的失败里取出 PID 文件占位那一族；不是这一族则 null（调用方照常显 message）。 */
export function stalePidFile(err: unknown): StalePidFileDetail | null {
  if (!(err instanceof LauncherError)) return null;
  const detail: unknown = err.detail;
  if (typeof detail !== "object" || detail === null) return null;
  return "code" in detail && detail.code === "stale_pid_file" ? (detail as StalePidFileDetail) : null;
}
