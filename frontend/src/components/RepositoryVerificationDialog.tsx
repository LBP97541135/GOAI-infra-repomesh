import { useState } from "react";
import type { ConsoleRepositoryView } from "../api/contract";
import { defaultClient } from "../api/client";
import { errText } from "../display";

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 仓库自己的验证命令与其测试路径必须作为一对由操作者明确维护。 */
export function RepositoryVerificationDialog({
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
  const [commands, setCommands] = useState(repo.test_commands.join("\n"));
  const [paths, setPaths] = useState(repo.test_paths.join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    if (saving) return;
    setSaving(true);
    setError(null);
    defaultClient()
      .updateRepositoryVerification(repo.repository_id, {
        test_commands: lines(commands),
        test_paths: lines(paths),
      })
      .then(() => {
        onToast(`${repo.name} 的验证配置已保存`);
        onSaved();
        onClose();
      })
      .catch((reason: unknown) => setError(errText(reason)))
      .finally(() => setSaving(false));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 px-4">
      <div className="w-full max-w-[620px] rounded-hard border border-line bg-panel p-4 shadow-2xl">
        <div className="flex items-baseline gap-3 border-b border-line pb-2.5">
          <h2 className="text-[14px] font-semibold text-cream">仓库验证配置</h2>
          <span className="font-mono text-[11.5px] text-tx2">{repo.name}</span>
          <button className="ml-auto text-[13px] text-tx2 hover:text-amber-hi" onClick={onClose}>
            ✕
          </button>
        </div>

        <p className="mt-3 text-[11.5px] text-tx2">
          每行一项。这里保存的是仓库自己的验证事实；Runner 在任务未声明测试时读取它，空配置会让交付门禁拒绝无测试证据的候选提交。
        </p>

        <label className="mt-3 block text-[11.5px] text-tx2" htmlFor="repository-test-commands">
          测试命令
        </label>
        <textarea
          id="repository-test-commands"
          aria-label="测试命令"
          className="mt-1 h-24 w-full resize-y rounded-hard border border-line bg-ink px-2.5 py-2 font-mono text-[12px] text-tx focus:border-amber focus:outline-none"
          placeholder="python scripts/run_tests.py"
          spellCheck={false}
          value={commands}
          onChange={(event) => setCommands(event.target.value)}
        />

        <label className="mt-3 block text-[11.5px] text-tx2" htmlFor="repository-test-paths">
          测试路径
        </label>
        <textarea
          id="repository-test-paths"
          aria-label="测试路径"
          className="mt-1 h-24 w-full resize-y rounded-hard border border-line bg-ink px-2.5 py-2 font-mono text-[12px] text-tx focus:border-amber focus:outline-none"
          placeholder="tests/**"
          spellCheck={false}
          value={paths}
          onChange={(event) => setPaths(event.target.value)}
        />

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
            {saving ? "保存中…" : "保存验证配置"}
          </button>
        </div>
      </div>
    </div>
  );
}
