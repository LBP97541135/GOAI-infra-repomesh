/** 本地身份系统客户端（main 的 /api/v1/auth，v2 直接采用）。
 *  会话是 httpOnly cookie `repomesh_session`（后端 human_control.py），
 *  故所有请求带 credentials: "include"；前端不持有也不存储 token。
 *  注意：读模型端点（/deliveries 等）走的是另一套 Bearer agent-action-token，
 *  与本会话无关——登录身份目前不参与读模型鉴权（缺口，见 CONS-40 回报）。 */

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
  me: () => request<Account>("/auth/me"),

  login: (username: string, password: string) =>
    request<{ account: Account }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  /** 首账号引导：仅本地部署首次可用；已存在管理员时后端返回 409 */
  bootstrap: (username: string, password: string, displayName: string) =>
    request<Account>("/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: displayName }),
    }),

  logout: () => request<void>("/auth/logout", { method: "POST" }),
};
