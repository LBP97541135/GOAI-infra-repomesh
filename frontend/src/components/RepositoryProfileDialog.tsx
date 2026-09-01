import { useState } from "react";
import type { ConsoleRepositoryView } from "../api/contract";
import { defaultClient } from "../api/client";
import { errText } from "../display";

/** 合法档案集与后端 `TEAM_CAPABILITY_PROFILES` 一致；前端写死这两个值，
 *  后端 422 是兜底（spec S-2b）。 */
const PROFILE_OPTIONS = [
  {
    value: "default",
    label: "default",
    hint: "常规仓库团队：角色预设技能，无附加编制语义",
  },
  {
    value: "cross-repo-test-team",
    label: "cross-repo-test-team",
    hint: "测试团队档案：新拓扑会在本仓上追加一支跨仓联调团队",
  },
] as const;

/** 档案开关（供给侧）：改档入口，见 CONTEXT.md。`default` 以 null 提交——
 *  后端把 "default" 字面量当 422 拒绝，存储侧只有 NULL 一种写法。 */
export function RepositoryProfileDialog({
  repo,
  onClose,
  onSaved,
  onToast,
}: {
  repo: ConsoleRepositoryView;
  onClose: () => void;
  onSaved: () => void;
  onToast: (text: string) => void;
}) {
  const [selected, setSelected] = useState(
    repo.capability_profile ?? "default",
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    defaultClient()
      .updateRepositoryCapabilityProfile(repo.repository_id, {
        capability_profile: selected === "default" ? null : selected,
      })
      .then(() => {
        // 冻结文案（spec S-2b）：成功回显处的供给侧语义提示。
        onToast(
          `${repo.name} 的团队档案已设为 ${selected}。该档案只影响之后新建的团队编制，已建团队不受影响`,
        );
        onSaved();
        onClose();
      })
      // 422（未知档名）与 404（仓不存在）在此呈现 detail，不静默。
      .catch((reason: unknown) => setError(errText(reason)))
      .finally(() => setSaving(false));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 px-4">
      <div className="w-full max-w-[620px] rounded-hard border border-line bg-panel p-4 shadow-2xl">
        <div className="flex items-baseline gap-3 border-b border-line pb-2.5">
          <h2 className="text-[14px] font-semibold text-cream">团队档案</h2>
          <span className="font-mono text-[11.5px] text-tx2">{repo.name}</span>
          <button className="ml-auto text-[13px] text-tx2 hover:text-amber-hi" onClick={onClose}>
            ✕
          </button>
        </div>

        {/* 冻结文案两句（spec S-2b）：供给侧语义 + 顺序约束。 */}
        <p className="mt-3 text-[11.5px] text-tx2">
          该档案只影响之后新建的团队编制，已建团队不受影响。请在建团（materialize）之前设置。
        </p>

        <div className="mt-3 grid gap-2" role="radiogroup" aria-label="团队档案">
          {PROFILE_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`flex cursor-pointer items-baseline gap-2.5 rounded-hard border px-3 py-2 ${
                selected === option.value
                  ? "border-amber bg-ink"
                  : "border-line bg-ink-deep hover:border-amber"
              }`}
            >
              <input
                type="radio"
                name="repository-capability-profile"
                value={option.value}
                checked={selected === option.value}
                onChange={() => setSelected(option.value)}
              />
              <span className="font-mono text-[12px] text-tx">{option.label}</span>
              <span className="text-[11px] text-tx3">{option.hint}</span>
            </label>
          ))}
        </div>

        {error && (
          <p className="mt-3 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] text-salmon">
            {error}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2 border-t border-line pt-3">
          <button
            className="rounded-hard border border-line px-3 py-[5px] text-[12px] text-tx2 hover:border-amber hover:text-amber-hi"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="rounded-hard bg-amber px-3.5 py-[6px] text-[12px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
            disabled={saving}
            onClick={save}
          >
            {saving ? "保存中…" : "保存团队档案"}
          </button>
        </div>
      </div>
    </div>
  );
}
