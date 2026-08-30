import { useState } from "react";
import { putGitHubAppCredential, type CredentialStatus } from "../../api/platformSetup";
import { errText } from "../../display";
import { CredentialField, credentialInputClass } from "./CredentialField";

export function GitHubAppCredentialForm({ status, onSaved }: { status: CredentialStatus["github_app"]; onSaved: (message: string) => void }) {
  const [appId, setAppId] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chooseFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) setPrivateKey(await file.text());
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
      <CredentialField mode="editable" label="App ID" note={status.app_id.set ? `当前已配置 ${status.app_id.masked ?? ""}。` : "GitHub App 的数字 ID。"}>
        <input className={credentialInputClass} type="number" min="1" value={appId} onChange={(e) => setAppId(e.target.value)} required />
      </CredentialField>
      <CredentialField mode="editable" label="私钥 PEM" note={status.private_key.set ? "当前已配置；选择文件或粘贴内容将覆盖。" : "可选择 .pem 文件或直接粘贴。"}>
        <input className="mb-2 block w-full text-[11px] text-tx2 file:mr-3 file:rounded-hard file:border file:border-line file:bg-panel-2 file:px-3 file:py-1.5 file:text-tx" type="file" accept=".pem,.key,text/plain" onChange={chooseFile} />
        <textarea className={`${credentialInputClass} min-h-28 resize-y`} value={privateKey} onChange={(e) => setPrivateKey(e.target.value)} required />
      </CredentialField>
      <CredentialField mode="editable" label="Webhook Secret" note={status.webhook_secret.set ? `当前已配置 ${status.webhook_secret.masked ?? ""}；留空不会清除。` : "可选。"}>
        <input className={credentialInputClass} type="password" value={webhookSecret} onChange={(e) => setWebhookSecret(e.target.value)} autoComplete="new-password" />
      </CredentialField>
      {error ? <p className="mt-3 text-[11.5px] text-salmon">{error}</p> : null}
      <button className="mt-4 rounded-hard bg-amber px-4 py-2 text-[12px] font-bold text-paper-ink disabled:opacity-50" disabled={busy}>{busy ? "保存中…" : "保存 GitHub App"}</button>
    </form>
  );
}
