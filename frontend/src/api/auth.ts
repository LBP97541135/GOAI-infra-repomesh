/** 本地身份系统客户端（`/api/v1/auth`）。
 *
 *  会话是 httpOnly cookie `repomesh_session`（后端 human_control.py），故所有
 *  请求带 `credentials: "include"`；**前端不持有也不存储 token**。
 *
 *  **控制台有两套凭据，物理隔离在两个客户端模块里**（2026-08-14 裁决恢复登录门）：
 *
 *   - 读模型 / 发现链 / console 网格 → `api/client.ts`，走 `Authorization: Bearer`
 *     动作 token（vite env 注入，`ACTION_TOKEN` 守卫比对 `agent_action_token`）；
 *   - human_control 面（建团、审核台、检查点决策）→ 本模块与 `api/humanControl.ts`，
 *     走这里的登录会话。
 *
 *  两者**不能混用，且不是风格问题**：后端 `_bearer()` 先读 Authorization 头、
 *  取不到才回落 cookie，所以给 human_control 请求带上动作 token 会让它拿动作
 *  token 去验会话，直接 401 —— cookie 明明有效也进不来。凡打 human_control 的
 *  请求一律不带 Authorization 头，由 cookie 认人。 */

export interface Account {
  id: string;
  username: string;
  display_name: string;
  is_admin: boolean;
  active: boolean;
}

export class AuthError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "AuthError";
    this.status = status;
  }
}

const BASE = import.meta.env.VITE_API_BASE ?? "";

/** human_control 面的**唯一** cookie 通道（`api/humanControl.ts` 复用本函数）。
 *  刻意不带 `Authorization` 头：后端 `_bearer()` 先读该头、取不到才回落 cookie，
 *  带上动作 token 会让有效会话也被判 401（见文件头）。 */
export async function sessionRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE}/api/v1${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      credentials: "include",
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (cause) {
    throw new AuthError(0, `无法连接身份服务：${cause instanceof Error ? cause.message : String(cause)}`);
  }
  if (!res.ok) {
    const raw = await res.text().catch(() => "");
    let detail = raw;
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      if (parsed.detail !== undefined) {
        detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
      }
    } catch {
      /* 非 JSON 体，原样展示 */
    }
    throw new AuthError(res.status, detail || `请求失败（HTTP ${res.status}）`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const authApi = {
  me: () => sessionRequest<Account>("/auth/me"),

  login: (username: string, password: string) =>
    sessionRequest<{ account: Account }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  /** 首账号引导：仅本地部署首次可用；已存在管理员时后端返回 409 */
  bootstrap: (username: string, password: string, displayName: string) =>
    sessionRequest<Account>("/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: displayName }),
    }),

  logout: () => sessionRequest<void>("/auth/logout", { method: "POST" }),
};
