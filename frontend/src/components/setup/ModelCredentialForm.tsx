import { useState } from "react";
import { putModelCredential, type CredentialStatus } from "../../api/platformSetup";
import { errText } from "../../display";
import { CredentialField, credentialInputClass } from "./CredentialField";

export function ModelCredentialForm({ status, onSaved }: { status: CredentialStatus["model"]; onSaved: (message: string) => void }) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const receipt = await putModelCredential({ api_key: apiKey, base_url: baseUrl || undefined, model: model || undefined });
      setApiKey("");
      onSaved(receipt.restarting ? "模型凭证已保存，API 正在自动重启。" : "模型凭证已保存，请重新运行启动脚本使其生效。");
    } catch (reason) {
      setError(errText(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <CredentialField mode="editable" label="API Key" note={status.api_key.set ? `当前已配置 ${status.api_key.masked ?? ""}；填写将覆盖。` : "尚未配置。"}>
        <input className={credentialInputClass} type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="new-password" required />
      </CredentialField>
      <CredentialField mode="editable" label="Base URL" note="留空时继续使用启动环境中的默认地址。">
        <input className={credentialInputClass} type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.deepseek.com/v1" />
      </CredentialField>
      <CredentialField mode="editable" label="模型名" note="留空时继续使用当前默认模型。">
        <input className={credentialInputClass} value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
      </CredentialField>
      {error ? <p className="mt-3 text-[11.5px] text-salmon">{error}</p> : null}
      <button className="mt-4 rounded-hard bg-amber px-4 py-2 text-[12px] font-bold text-paper-ink disabled:opacity-50" disabled={busy}>{busy ? "保存中…" : "保存模型配置"}</button>
    </form>
  );
}
