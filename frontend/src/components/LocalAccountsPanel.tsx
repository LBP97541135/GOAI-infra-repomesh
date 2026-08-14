import { useCallback, useEffect, useState, type FormEvent } from "react";
import { AuthError, authApi, type Account } from "../api/auth";
import { errText } from "../display";
import { ErrorPanel, LoadingLine } from "./StatusBlocks";

/** 设置页 · 人员与权限（迁移 5-2：`web/` 的 AccountPanel 进控制台）。
 *
 *  **为什么补这一段**：上一批把人工审核台迁了进来，但审核单要落到**具体的人**头上，
 *  而控制台此前没有任何地方能建人——`auth/accounts` 这个字符串在 `frontend/` 全目录
 *  一次都没出现过。于是审核台是个永远等不到审核人的空桌子：能看见待办的账号只有
 *  装机时 bootstrap 出来的那一个管理员。
 *
 *  **凭据走 cookie 会话，不带 `Authorization` 头**（`api/auth.ts` 的 `sessionRequest`）。
 *  这不是风格选择：后端 `_bearer()` 先读 Authorization 头、取不到才回落 cookie，
 *  所以给这两个端点带上读模型那把动作 token，会让它拿动作 token 去验会话直接 401 ——
 *  cookie 明明有效也进不来，而且失败看上去就是一次普通的「未登录」，查起来毫无线索。
 *  故本组件只经 `authApi`，绝不碰 `api/client.ts`。
 *
 *  **非管理员照样渲染这一段**，只把内容换成「需要管理员权限」。藏掉区块会让人以为
 *  控制台压根没有账号管理这回事，转头去 `web/` 里找——而这批迁移的目的正是让人
 *  不必再回去。
 *
 *  **「审核人」不是账号上的一个属性**，所以这里也不发这么一个标记：账号只有两种事实，
 *  是不是管理员、停没停用。要收得到审核待办，得在项目拓扑的 `human_grants` 里被授权
 *  到具体项目（迁移 5-1，尚未进控制台）。把非管理员账号一律标成「审核人」是替后端
 *  许了一个它没许的诺。 */

/** 服务端状态码 → 下一步该做什么。**只作补充，不替换 detail 原文**：
 *  后端的话说的是「哪里不对」，这一行说的是「所以现在做什么」，两者都要。
 *  没列在这里的状态码不编一句——宁可只显示原文。 */
const NEXT_STEP: Record<number, string> = {
  422: "输入不合规：按上面的规则改一处再提交。",
  409: "这个用户名已被占用：换一个再提交。",
  403: "这是权限问题，不是填写问题——当前账号不是管理员，改输入重试没有用。",
};

function Field({
  label,
  hint,
  type = "text",
  autoComplete,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  type?: "text" | "password";
  autoComplete?: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <label className="mt-2.5 block">
      <span className="microlabel mb-1 block">{label}</span>
      <input
        type={type}
        required
        spellCheck={false}
        autoComplete={autoComplete}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-hard border border-line bg-ink px-2.5 py-[6px] font-mono text-[12px] text-tx focus:border-amber focus:outline-none"
      />
      {hint && <span className="mt-1 block text-[10.5px] leading-[1.6] text-tx3">{hint}</span>}
    </label>
  );
}

function AccountRow({ item, self }: { item: Account; self: boolean }) {
  return (
    <div className="flex items-baseline gap-2.5 border-b border-panel py-2">
      <span className="min-w-0 flex-1">
        <span className="block text-[12px] text-tx">
          {item.display_name}
          {self && <span className="ml-1.5 text-[10.5px] text-tx3">当前登录</span>}
        </span>
        <span className="mt-px block font-mono text-[11px] text-tx3">@{item.username}</span>
      </span>
      {/* 措辞与本页「本地身份服务」那一行同一对，不另起一套说法 */}
      <span
        className={`rounded-hard border px-2 py-px text-[11px] ${
          item.is_admin ? "border-amber text-amber" : "border-line text-tx2"
        }`}
      >
        {item.is_admin ? "管理员" : "本地账户"}
      </span>
      {/* 停用是少数态，只在为真时出徽标；没有徽标即在用 */}
      {!item.active && (
        <span className="rounded-hard border border-salmon px-2 py-px text-[11px] text-salmon">
          已停用
        </span>
      )}
    </div>
  );
}

const SOURCE_NOTE =
  "数据源：live · GET /auth/accounts（human_control 面，认本地登录会话，不带 Authorization 头）；" +
  "新增走 POST /auth/accounts。两者都要管理员。口令只随这一次提交发出，前端不保存、不回显。";

export function LocalAccountsPanel({ account }: { account: Account }) {
  const [rows, setRows] = useState<Account[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ display_name: "", username: "", password: "", is_admin: false });
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ text: string; status: number } | null>(null);
  const [created, setCreated] = useState<Account | null>(null);

  const load = useCallback(() => {
    setError(null);
    authApi
      .accounts()
      .then(setRows)
      .catch((err: unknown) => setError(errText(err)));
  }, []);

  useEffect(() => {
    // 非管理员打这个端点必 403。不发这个注定被拒的请求，是为了不把一个权限事实
    // 渲染成一次取数失败——那两件事的下一步完全不同。
    if (!account.is_admin) return;
    load();
  }, [account.is_admin, load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setFailure(null);
    setCreated(null);
    try {
      // 原样送出，不在前端 trim / 转小写：归一由后端做（`_normalize_username`），
      // 前端先做一遍只会让「我填的」和「存下来的」看起来一样，掩盖真实的归一规则。
      const next = await authApi.createAccount({
        username: form.username,
        password: form.password,
        display_name: form.display_name,
        is_admin: form.is_admin,
      });
      setCreated(next);
      setForm({ display_name: "", username: "", password: "", is_admin: false });
      // 重取而不是把返回体 push 进本地数组：清单的内容与次序归服务端，
      // 本地拼一份就有了第二个真相源。
      load();
    } catch (err) {
      setFailure({ text: errText(err), status: err instanceof AuthError ? err.status : 0 });
    } finally {
      setBusy(false);
    }
  };

  if (!account.is_admin) {
    return (
      <>
        <p className="pt-1 text-[11.5px] leading-[1.7] text-tx2">
          账号管理需要管理员权限。当前账号{" "}
          <span className="font-mono text-tx">@{account.username}</span>{" "}
          不是管理员，列出与新增两个端点都会被后端拒绝（403）。需要新账号时请管理员代建。
        </p>
        <p className="pt-2 text-[11px] text-tx3">{SOURCE_NOTE}</p>
      </>
    );
  }

  return (
    <>
      <p className="pt-1 text-[11.5px] leading-[1.7] text-tx2">
        审核台的待办要落到具体的人身上，人得先在这里有账号。
        <b className="text-tx">建号只给了登录身份</b>
        ，不等于收得到待办——那要在项目拓扑的 human_grants 里把这个账号授权到具体项目，
        该入口尚未迁入控制台。
      </p>

      <div className="mt-2.5 grid gap-4 md:grid-cols-[minmax(0,1fr)_268px]">
        <div>
          {/* rows === null 是「还没取到」，空数组是「真的一个都没有」，两态不合并 */}
          {error ? (
            <ErrorPanel title="账号清单加载失败" message={error} onRetry={load} className="" />
          ) : rows === null ? (
            <LoadingLine className="" />
          ) : rows.length === 0 ? (
            <p className="py-6 text-[11.5px] leading-[1.7] text-tx3">
              清单里一个账号都没有。但你正以本地账号登录，二者相互矛盾——这更可能是取数
              问题而不是事实，值得排查。
            </p>
          ) : (
            rows.map((item) => (
              <AccountRow key={item.id} item={item} self={item.id === account.id} />
            ))
          )}
        </div>

        <form className="rounded-hard border border-line bg-panel-2 px-3 py-2.5" onSubmit={submit}>
          <div className="eyebrow">新增账号</div>
          <Field
            label="显示名称"
            value={form.display_name}
            onChange={(next) => setForm({ ...form, display_name: next })}
            hint="审核台上署名用的就是它。"
          />
          <Field
            label="用户名"
            autoComplete="off"
            value={form.username}
            onChange={(next) => setForm({ ...form, username: next })}
            hint="至少 3 字符，只允许字母数字与 . _ -；后端会转为小写。"
          />
          <Field
            label="初始密码"
            type="password"
            // 不写 new-password 的话，浏览器会把管理员**自己**的密码填进这个框，
            // 于是新账号悄悄用了管理员的口令
            autoComplete="new-password"
            value={form.password}
            onChange={(next) => setForm({ ...form, password: next })}
            hint="至少 12 个字符（后端强制，不是界面自己加的规矩）。"
          />

          <label className="mt-3 flex items-center gap-2">
            <input
              type="checkbox"
              className="accent-amber"
              checked={form.is_admin}
              onChange={(e) => setForm({ ...form, is_admin: e.target.checked })}
            />
            <span className="text-[11.5px] text-tx2">设为管理员</span>
          </label>
          <span className="mt-1 block text-[10.5px] leading-[1.6] text-tx3">
            管理员能建账号、给仓库建团，并看得见全部审核待办；非管理员只看得见指派给自己的。
          </span>

          <button
            className="mt-3 w-full rounded-hard bg-amber px-3 py-[6px] text-[12px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
            disabled={busy}
          >
            {busy ? "创建中…" : "创建账号"}
          </button>

          {created && (
            <p className="mt-2 rounded-hard border border-olive/60 bg-olive/10 px-2.5 py-1.5 text-[11.5px] leading-[1.7] text-olive">
              已创建 {created.display_name} · @{created.username}
              {created.is_admin && "（管理员）"}。用户名以此处回显为准——后端会做归一。
            </p>
          )}

          {failure && (
            <div className="mt-2 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] leading-[1.7] text-salmon">
              {/* 服务端 detail 原文在前，它比任何改写都准 */}
              {failure.text}
              {NEXT_STEP[failure.status] && (
                <span className="mt-1 block text-tx2">{NEXT_STEP[failure.status]}</span>
              )}
            </div>
          )}
        </form>
      </div>

      <p className="pt-2 text-[11px] text-tx3">{SOURCE_NOTE}</p>
    </>
  );
}
