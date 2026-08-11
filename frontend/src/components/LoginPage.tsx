import { useState } from "react";
import { AuthError, authApi, type Account } from "../api/auth";

/** 登录门 / 首账号引导（CONS-40）。
 *  语义参考 main 的 web/src/Login.tsx（本地账户、bootstrap 仅首次），
 *  视觉按 Variant D 令牌重绘（暖黑/琥珀/奶油纸/2px 圆角/等宽微标签）。
 *  诚实数据：无法探测是否已初始化（后端 bootstrap 的密码校验与「已存在管理员」
 *  同为 409），故不猜状态——由使用者显式选择模式。 */

const inputClass =
  "w-full rounded-hard border border-line bg-ink px-3 py-2 font-sans text-[13px] text-tx placeholder:text-[#6b6046] focus:border-amber focus:outline-none";

export function LoginPage({ onAuthenticated }: { onAuthenticated: (account: Account) => void }) {
  const [setupMode, setSetupMode] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (setupMode) await authApi.bootstrap(username, password, displayName);
      const { account } = await authApi.login(username, password);
      onAuthenticated(account);
    } catch (err) {
      setError(err instanceof AuthError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid h-screen place-items-center bg-ink px-6">
      <div className="w-full max-w-[560px]">
        <div className="mb-7 flex items-center gap-3">
          <span className="grid size-[38px] flex-none place-items-center rounded-hard bg-amber font-mono text-[17px] font-extrabold text-[#16120a]">
            R
          </span>
          <div>
            <strong className="block font-mono text-[14px] tracking-[0.14em] text-cream">REPOMESH</strong>
            <span className="microlabel">交付控制平面 · 本地部署</span>
          </div>
        </div>

        <form
          className="rounded-hard border border-line bg-panel px-7 pt-6 pb-7 shadow-[0_18px_48px_rgba(0,0,0,0.45)]"
          onSubmit={submit}
        >
          <h1 className="text-[15px] font-semibold text-cream">
            {setupMode ? "初始化本地管理员" : "登录控制平面"}
          </h1>
          <p className="mt-1 mb-4 text-[12px] text-tx2">
            {setupMode ? "仅首次部署执行一次；已存在管理员时后端会拒绝。" : "使用本地账户进入控制台。凭据仅保存在本机。"}
          </p>

          {setupMode && (
            <label className="mb-3 block">
              <span className="microlabel mb-1.5 block">显示名称</span>
              <input
                className={inputClass}
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="例如：王倩"
                required
              />
            </label>
          )}

          <label className="mb-3 block">
            <span className="microlabel mb-1.5 block">用户名</span>
            <input
              className={inputClass}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="username"
              autoComplete="username"
              required
            />
          </label>

          <label className="block">
            <span className="microlabel mb-1.5 block">密码</span>
            <input
              className={inputClass}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="至少 12 位"
              minLength={12}
              autoComplete={setupMode ? "new-password" : "current-password"}
              required
            />
          </label>

          {error && (
            <div className="mt-4 rounded-hard border border-[#7a4530] bg-[#2b1712] px-3 py-2 text-[12px] text-[#e8a184]">
              {error}
            </div>
          )}

          <button
            className="mt-5 w-full rounded-hard bg-amber py-[9px] text-[13px] font-extrabold tracking-[0.04em] text-[#191308] hover:bg-amber-hi disabled:opacity-60"
            disabled={busy}
          >
            {busy ? "正在连接…" : setupMode ? "初始化并登录" : "登录"}
          </button>

          <button
            type="button"
            className="mt-3 w-full text-[12px] text-tx2 hover:text-amber-hi"
            onClick={() => {
              setSetupMode((v) => !v);
              setError(null);
            }}
          >
            {setupMode ? "已有账户，返回登录" : "首次部署？初始化管理员"}
          </button>
        </form>
      </div>
    </div>
  );
}
