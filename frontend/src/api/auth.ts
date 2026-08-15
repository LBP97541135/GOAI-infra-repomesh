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

/** 建账号的请求体（后端 `AccountCreate`）。
 *
 *  `is_admin` 可省：默认值是**后端的**（false），前端不镜像一份——镜像的默认值
 *  日后与后端漂移时不会有任何东西提醒我们。 */
export interface AccountCreateRequest {
  username: string;
  password: string;
  display_name: string;
  is_admin?: boolean;
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

  /** 列出全部本地账号。**要管理员**。
   *
   *  非管理员拿到的是 403 而**不是空列表**：后端先认会话、再查 `is_admin`。调用方
   *  据此说「权限不够」，不得画成「一个账号都没有」——那是把一次拒绝说成了一个事实。 */
  accounts: () => sessionRequest<Account[]>("/auth/accounts"),

  /** 新建一个本地账号。**要管理员**。
   *
   *  三种可预期的失败各有各的下一步，detail 一律原文上抛不归并：
   *   - 422 输入不合规——规则在后端（`local_accounts.py` 的 `_create` 与
   *     `_normalize_username`：密码 ≥12 字符、显示名非空、用户名 ≥3 字符且只含
   *     字母数字与 `. _ -`）。**前端不复刻这些判定**，只回显规则与服务端原话，
   *     否则同一条规则会有两份实现，改后端时前端悄悄拦下本该通过的输入；
   *   - 409 用户名已存在；
   *   - 403 当前账号不是管理员——这不是填写问题，改输入重试没有用。
   *
   *  用户名会被后端 `strip().lower()` 归一，所以回显以返回体为准，不要拿表单原值
   *  当结果——那两个值可以不一样。 */
  createAccount: (payload: AccountCreateRequest) =>
    sessionRequest<Account>("/auth/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
