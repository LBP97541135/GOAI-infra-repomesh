import { useState } from "react";
import {
  createGitHubAppManifest,
  putGitHubAppCredential,
  verifyGitHubAppInstallation,
  type CredentialStatus,
  type GitHubAppRegistrationStatus,
} from "../../api/platformSetup";
import { AuthError } from "../../api/auth";
import { errText } from "../../display";
import { CredentialField, credentialInputClass } from "./CredentialField";

/** GitHub App 的三段状态，对应后端 `/setup/status` 上分开的三个检查项。
 *
 *  这里刻意不把它们并成一个「已配置」：建好了但没装、装好了但进程还没重启，
 *  都是凭证俱在而交付实际是死的——合并显示等于把两种坏状态画成绿的。 */
function registrationNote(registration: GitHubAppRegistrationStatus | null): string {
  if (!registration) return "";
  if (!registration.installed) {
    return `已创建 App「${registration.slug}」（归属 ${registration.owner_login}），但尚未安装到任何账号，目前看不到任何仓库。`;
  }
  const account = registration.installation_account_login ?? registration.owner_login;
  return `已创建并安装 App「${registration.slug}」（安装在 ${account}）。`;
}

export function GitHubAppCredentialForm({
  status,
  registration,
  onSaved,
}: {
  status: CredentialStatus["github_app"];
  registration: GitHubAppRegistrationStatus | null;
  onSaved: (message: string) => void;
}) {
  const [appId, setAppId] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [ownerKind, setOwnerKind] = useState<"personal" | "organization">("personal");
  const [ownerLogin, setOwnerLogin] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [verifyNote, setVerifyNote] = useState<string | null>(null);

  const chooseFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) setPrivateKey(await file.text());
  };

  const oneClick = async (replace = false) => {
    setCreating(true);
    setCreateError(null);
    try {
      const issued = await createGitHubAppManifest({
        displayName,
        ownerLogin: ownerKind === "organization" ? ownerLogin : "",
        replace,
      });
      const form = document.createElement("form");
      form.method = "POST";
      form.action = `${issued.github_url}?state=${encodeURIComponent(issued.state)}`;
      form.style.display = "none";
      const field = document.createElement("input");
      field.type = "hidden";
      field.name = "manifest";
      field.value = JSON.stringify(issued.manifest);
      form.appendChild(field);
      document.body.appendChild(form);
      form.submit();
      // 不清 creating：页面正在离开，清掉只会让按钮在跳转前闪一下。
    } catch (reason) {
      // 409 是「继续会遗弃 GitHub 上那个旧 App」，必须由人来点头。detail 里点了
      // 名，原样显示，不要自己编一句更简略的。
      if (reason instanceof AuthError && reason.status === 409) {
        setCreating(false);
        if (window.confirm(`${reason.message}\n\n继续创建新的 App？`)) {
          void oneClick(true);
          return;
        }
        setCreateError("已取消，现有 App 保持不变。");
        return;
      }
      setCreateError(errText(reason));
      setCreating(false);
    }
  };

  const verify = async () => {
    setVerifying(true);
    setVerifyNote(null);
    setCreateError(null);
    try {
      const result = await verifyGitHubAppInstallation();
      if (!result.installed) {
        setVerifyNote("GitHub 说这个 App 还没有安装在任何账号上。请到 GitHub 上完成安装后再试。");
        return;
      }
      onSaved(
        result.restarting
          ? `已确认安装在「${result.account ?? "该账号"}」，API 正在自动重启使其生效。`
          : `已确认安装在「${result.account ?? "该账号"}」，请重新运行启动脚本使其生效。`,
      );
    } catch (reason) {
      setVerifyNote(errText(reason));
    } finally {
      setVerifying(false);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const receipt = await putGitHubAppCredential({ app_id: Number(appId), private_key_pem: privateKey, webhook_secret: webhookSecret || undefined });
      setPrivateKey("");
      setWebhookSecret("");
      onSaved(receipt.restarting ? "GitHub App 凭证已保存，API 正在自动重启。" : "GitHub App 凭证已保存，请重新运行启动脚本使其生效。");
    } catch (reason) {
      setError(errText(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <CredentialField
        mode="editable"
        label="App 建在谁名下"
        note={
          ownerKind === "organization"
            ? "私有 App 只能安装在它的归属账号上。要交付组织仓库，就得把 App 建在该组织下——这需要你在该组织里是 owner 或 GitHub App 管理员。"
            : "App 归属你的个人账号，只能安装到该账号名下的仓库。要交付组织仓库请选「组织」。"
        }
      >
        <div className="flex flex-wrap items-center gap-4 text-[12px] text-tx">
          <span className="inline-flex items-center gap-1.5">
            <input type="radio" name="owner-kind" checked={ownerKind === "personal"} onChange={() => setOwnerKind("personal")} />
            我的个人账号
          </span>
          <span className="inline-flex items-center gap-1.5">
            <input type="radio" name="owner-kind" checked={ownerKind === "organization"} onChange={() => setOwnerKind("organization")} />
            组织
          </span>
        </div>
        {ownerKind === "organization" ? (
          <input className={`${credentialInputClass} mt-2`} value={ownerLogin} onChange={(e) => setOwnerLogin(e.target.value)} placeholder="组织登录名，例如 acme-corp" autoCapitalize="off" autoCorrect="off" spellCheck={false} />
        ) : null}
      </CredentialField>

      <CredentialField mode="editable" label="App 名称（可留空）" note="GitHub 上 App 名全局唯一，留空则由服务端生成一个带随机后缀的名字，避免撞名。上限 34 字符。">
        <input className={credentialInputClass} value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={34} placeholder="留空自动生成" />
      </CredentialField>

      <CredentialField
        mode="editable"
        label="用 GitHub 账号一键创建（推荐）"
        note={
          registration
            ? `${registrationNote(registration)}重新创建会让 GitHub 上那个 App 变成无人使用的孤儿，需要你自己去删。`
            : "点击后跳转 GitHub 确认，随后自动进入安装页选择仓库；App ID、私钥、Webhook Secret 全程自动回填并加密保存。请先确认当前浏览器已登录 GitHub，否则会落到一张空白表单上。"
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="rounded-hard bg-amber px-4 py-2 text-[12px] font-bold text-paper-ink disabled:opacity-50" disabled={creating || (ownerKind === "organization" && !ownerLogin.trim())} onClick={() => void oneClick()}>
            {creating ? "正在跳转 GitHub…" : registration ? "重新创建 App" : "跳转 GitHub 创建"}
          </button>
          {registration && !registration.installed ? (
            <a className="rounded-hard border border-amber px-4 py-2 text-[12px] text-amber" href={registration.install_url} target="_blank" rel="noreferrer">
              去 GitHub 安装
            </a>
          ) : null}
          {registration ? (
            <button type="button" className="rounded-hard border border-line px-4 py-2 text-[12px] text-tx2 disabled:opacity-50" disabled={verifying} onClick={() => void verify()}>
              {verifying ? "核对中…" : "我已在 GitHub 上安装完成"}
            </button>
          ) : null}
        </div>
        {createError ? <p className="mt-2 text-[11.5px] text-salmon">{createError}</p> : null}
        {verifyNote ? <p className="mt-2 text-[11.5px] text-salmon">{verifyNote}</p> : null}
      </CredentialField>
      <div className="my-5 border-t border-line pt-4">
        <p className="mb-3 text-[11px] text-tx3">或手动填写（已有 App、或一键流程中断需要自救时使用）：</p>
        <CredentialField mode="editable" label="App ID" note={status.app_id.set ? `当前已配置 ${status.app_id.masked ?? ""}。` : "GitHub App 的数字 ID。"}>
          <input className={credentialInputClass} type="number" min="1" value={appId} onChange={(e) => setAppId(e.target.value)} />
        </CredentialField>
        <CredentialField mode="editable" label="私钥 PEM" note={status.private_key.set ? "当前已配置；选择文件或粘贴内容将覆盖。" : "可选择 .pem 文件或直接粘贴。私钥可在 GitHub 的 App 设置页重新生成。"}>
          <input className="mb-2 block w-full text-[11px] text-tx2 file:mr-3 file:rounded-hard file:border file:border-line file:bg-panel-2 file:px-3 file:py-1.5 file:text-tx" type="file" accept=".pem,.key,text/plain" onChange={chooseFile} />
          <textarea className={`${credentialInputClass} min-h-28 resize-y`} value={privateKey} onChange={(e) => setPrivateKey(e.target.value)} />
        </CredentialField>
        <CredentialField mode="editable" label="Webhook Secret" note={status.webhook_secret.set ? `当前已配置 ${status.webhook_secret.masked ?? ""}；留空不会清除。` : "可选。本部署默认停用 webhook（实时事件走观察轮询），留空不影响使用。"}>
          <input className={credentialInputClass} type="password" value={webhookSecret} onChange={(e) => setWebhookSecret(e.target.value)} autoComplete="new-password" />
        </CredentialField>
        {error ? <p className="mt-3 text-[11.5px] text-salmon">{error}</p> : null}
        <button className="mt-4 rounded-hard border border-amber px-4 py-2 text-[12px] text-amber disabled:opacity-50" disabled={busy}>{busy ? "保存中…" : "保存 GitHub App"}</button>
      </div>
    </form>
  );
}
