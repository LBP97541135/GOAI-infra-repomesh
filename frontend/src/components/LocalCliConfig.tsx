import { useState } from "react";
import type { RosterDocument, RosterMemberEntry, SecretEntry } from "../api/launcher";
import { envTokenNames, putRoster, putSecrets } from "../api/launcher";
import { errText, shortId } from "../display";

/** 设置 · 本地 CLI 的三块配置编辑：连接配置 / 成员密钥 / 成员名册。
 *
 *  写路径全部经过本机启动器（Origin 白名单 + OP 头，同 start/stop 一道门）；
 *  启动器负责落盘的原子性与 `.bak` 备份，本组件只负责表单与确认。
 *  三块共同的纪律：
 *   - 提交是**整份 roster 替换**（GET 到什么就送回什么，只改表单字段）——
 *     不在前端拼"差异"，差异拼错就是静默丢字段；
 *   - 密钥**只写不读**：读回来的是掩码尾巴，输入框永远留空 = 不改；
 *   - 每次保存成功后由父级重取 roster/secrets/状态，本组件不本地拼清单。 */

function Field({
  label,
  hint,
  value,
  onChange,
  mono = false,
  placeholder,
  type = "text",
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (next: string) => void;
  mono?: boolean;
  placeholder?: string;
  type?: "text" | "password";
}) {
  return (
    <label className="block">
      <span className="microlabel mb-1 block">{label}</span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full rounded-hard border border-line bg-ink px-2.5 py-[6px] text-[12px] text-tx focus:border-amber focus:outline-none ${mono ? "font-mono" : ""}`}
      />
      {hint && <span className="mt-1 block text-[10.5px] leading-[1.6] text-tx3">{hint}</span>}
    </label>
  );
}

function ConfigSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-5">
      <div className="eyebrow mb-2">{title}</div>
      {children}
    </section>
  );
}

const SAVE_BUTTON =
  "rounded-hard bg-amber px-3 py-[6px] text-[11.5px] font-extrabold text-on-amber hover:bg-amber-hi disabled:cursor-not-allowed disabled:opacity-40";
const SUBTLE_BUTTON =
  "rounded-hard border border-line px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:border-amber hover:text-amber-hi";

/* ── 连接配置：members.json 的全局连接字段 ───────────────────────────────── */

export function ConnectionConfigSection({
  roster,
  onSaved,
  onError,
}: {
  roster: RosterDocument;
  onSaved: () => void;
  onError: (text: string) => void;
}) {
  const [endpoint, setEndpoint] = useState(roster.repomeshEndpoint ?? "");
  const [homeserver, setHomeserver] = useState(roster.matrixHomeserverUrl ?? "");
  const [controller, setController] = useState(roster.controllerUrl ?? "");
  const [profile, setProfile] = useState(roster.codingProfile ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await putRoster({
        ...roster,
        ...(endpoint.trim() ? { repomeshEndpoint: endpoint.trim() } : {}),
        ...(homeserver.trim() ? { matrixHomeserverUrl: homeserver.trim() } : {}),
        ...(controller.trim() ? { controllerUrl: controller.trim() } : {}),
        ...(profile.trim() ? { codingProfile: profile.trim() } : {}),
      });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      onSaved();
    } catch (err) {
      onError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ConfigSection title="连接配置">
      <p className="mb-2 text-[11px] text-tx3">成员启动时连接的四个地址与档案；改动保存后需重启成员生效。</p>
      <div className="grid gap-2.5 md:grid-cols-2">
        <Field label="RepoMesh API" mono value={endpoint} onChange={setEndpoint} placeholder="http://127.0.0.1:8077" />
        <Field label="Matrix Homeserver" mono value={homeserver} onChange={setHomeserver} placeholder="http://127.0.0.1:18080" />
        <Field label="AgentTeams Controller" mono value={controller} onChange={setController} placeholder="http://127.0.0.1:18090" />
        <Field label="Coding Profile" mono value={profile} onChange={setProfile} placeholder="codex" />
      </div>
      <div className="mt-3 flex items-center gap-2.5">
        <button type="button" className={SAVE_BUTTON} disabled={saving} onClick={save}>
          {saving ? "保存中…" : "保存连接配置"}
        </button>
        {saved && <span className="text-[11.5px] text-olive">已保存（原文件备份为 .bak）</span>}
      </div>
    </ConfigSection>
  );
}

/* ── 成员密钥：e1-members.env 的两把 token，只写不读 ──────────────────────── */

function MemberSecretsRow({
  memberKey,
  role,
  secrets,
  onSaved,
  onError,
}: {
  memberKey: string;
  role: string;
  secrets: SecretEntry[];
  onSaved: () => void;
  onError: (text: string) => void;
}) {
  const names = envTokenNames(memberKey);
  const masked = (name: string) => secrets.find((entry) => entry.name === name)?.masked ?? "未设置";
  const [editing, setEditing] = useState(false);
  const [matrix, setMatrix] = useState("");
  const [repomesh, setRepomesh] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const save = async () => {
    const entries = [
      ...(matrix.trim() ? [{ name: names.matrix, value: matrix.trim() }] : []),
      ...(repomesh.trim() ? [{ name: names.repomesh, value: repomesh.trim() }] : []),
    ];
    if (entries.length === 0) return;
    setSaving(true);
    try {
      await putSecrets(entries);
      setDone("已写入，重启该成员后生效");
      setMatrix("");
      setRepomesh("");
      setEditing(false);
      onSaved();
    } catch (err) {
      onError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border-b border-panel py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <span className="font-mono text-[12px] text-tx">{memberKey}</span>
          <span className="ml-2 text-[10.5px] text-tx3">{role}</span>
        </div>
        <button
          type="button"
          className={SUBTLE_BUTTON}
          onClick={() => {
            setEditing((v) => !v);
            setDone(null);
          }}
        >
          {editing ? "取消" : "更换密钥"}
        </button>
      </div>
      {!editing && (
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[10.5px] text-tx3">
          <span>
            Matrix <span className="text-tx2">{masked(names.matrix)}</span>
          </span>
          <span>
            RepoMesh <span className="text-tx2">{masked(names.repomesh)}</span>
          </span>
        </div>
      )}
      {editing && (
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <Field
            label={`新 Matrix token（${names.matrix}）`}
            mono
            type="password"
            value={matrix}
            onChange={setMatrix}
            placeholder="留空 = 不改"
          />
          <Field
            label={`新 RepoMesh token（${names.repomesh}）`}
            mono
            type="password"
            value={repomesh}
            onChange={setRepomesh}
            placeholder="留空 = 不改"
          />
          <div className="md:col-span-2">
            <button type="button" className={SAVE_BUTTON} disabled={saving || (!matrix.trim() && !repomesh.trim())} onClick={save}>
              {saving ? "写入中…" : "写入新密钥"}
            </button>
            {done && <span className="ml-2 text-[11.5px] text-olive">{done}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

export function SecretsSection({
  roster,
  secrets,
  onSaved,
  onError,
}: {
  roster: RosterDocument;
  secrets: SecretEntry[];
  onSaved: () => void;
  onError: (text: string) => void;
}) {
  return (
    <ConfigSection title="成员密钥">
      <p className="mb-2 text-[11px] text-tx3">
        只写不读：清单里是掩码尾巴，写入的新值不回显、不经过保存。改完需重启该成员生效。
      </p>
      {roster.members.map((member) => (
        <MemberSecretsRow
          key={member.key}
          memberKey={member.key}
          role={member.role}
          secrets={secrets}
          onSaved={onSaved}
          onError={onError}
        />
      ))}
    </ConfigSection>
  );
}

/* ── 成员名册：members.json 的成员行增删改 ────────────────────────────────── */

const EMPTY_MEMBER: RosterMemberEntry = {
  key: "",
  role: "worker",
  agentId: "",
  repositoryId: "",
  resourceName: "",
  responsibilityPaths: ["**"],
  subsets: ["e1"],
};

function RosterForm({
  initial,
  isNew,
  onSave,
  onCancel,
}: {
  initial: RosterMemberEntry;
  isNew: boolean;
  onSave: (entry: RosterMemberEntry) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    key: initial.key,
    role: initial.role,
    agentId: initial.agentId,
    repositoryId: initial.repositoryId ?? "",
    resourceName: initial.resourceName ?? "",
    responsibilityPaths: (initial.responsibilityPaths ?? []).join(", "),
    subsets: (initial.subsets ?? []).join(", "),
    leaderKey: initial.leaderKey ?? "",
    workspaceRoot: initial.workspaceRoot ?? "",
  });
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<typeof form>) => setForm((prev) => ({ ...prev, ...patch }));
  const list = (text: string) =>
    text
      .split(/[，,]/)
      .map((item) => item.trim())
      .filter(Boolean);

  const save = async () => {
    setBusy(true);
    try {
      const entry: RosterMemberEntry = {
        ...initial,
        key: form.key.trim(),
        role: form.role,
        agentId: form.agentId.trim(),
        repositoryId: form.repositoryId.trim(),
        resourceName: form.resourceName.trim(),
        responsibilityPaths: list(form.responsibilityPaths),
        subsets: list(form.subsets),
        ...(form.role === "worker" && form.leaderKey.trim() ? { leaderKey: form.leaderKey.trim() } : {}),
        ...(form.workspaceRoot.trim() ? { workspaceRoot: form.workspaceRoot.trim() } : {}),
      };
      await onSave(entry);
    } finally {
      setBusy(false);
    }
  };

  const canSave =
    form.key.trim() !== "" && form.agentId.trim() !== "" && !(form.role === "worker" && !form.leaderKey.trim());

  return (
    <div className="mt-2 rounded-hard border border-line bg-well px-3 py-2.5">
      <div className="grid gap-2.5 md:grid-cols-2">
        <Field label="key（名册键）" mono value={form.key} onChange={(v) => set({ key: v })} />
        <label className="block">
          <span className="microlabel mb-1 block">角色</span>
          <select
            value={form.role}
            onChange={(e) => set({ role: e.target.value })}
            className="w-full rounded-hard border border-line bg-ink px-2.5 py-[6px] text-[12px] text-tx focus:border-amber focus:outline-none"
          >
            <option value="repository_leader">repository_leader</option>
            <option value="worker">worker</option>
          </select>
        </label>
        <Field label="agentId" mono value={form.agentId} onChange={(v) => set({ agentId: v })} />
        <Field label="repositoryId" mono value={form.repositoryId} onChange={(v) => set({ repositoryId: v })} />
        <Field label="resourceName" mono value={form.resourceName} onChange={(v) => set({ resourceName: v })} />
        {form.role === "worker" && (
          <Field label="leaderKey（所属 Leader 的 key）" mono value={form.leaderKey} onChange={(v) => set({ leaderKey: v })} />
        )}
        <Field label="职责路径（逗号分隔）" mono value={form.responsibilityPaths} onChange={(v) => set({ responsibilityPaths: v })} />
        <Field label="subsets（逗号分隔）" mono value={form.subsets} onChange={(v) => set({ subsets: v })} />
        {form.role === "worker" && (
          <Field label="workspaceRoot（可选）" mono value={form.workspaceRoot} onChange={(v) => set({ workspaceRoot: v })} />
        )}
      </div>
      <div className="mt-3 flex items-center gap-2.5">
        <button type="button" className={SAVE_BUTTON} disabled={!canSave || busy} onClick={save}>
          {busy ? "提交中…" : isNew ? "添加成员" : "保存修改"}
        </button>
        <button type="button" className={SUBTLE_BUTTON} onClick={onCancel}>
          取消
        </button>
        {!canSave && <span className="text-[10.5px] text-tx3">key、agentId 必填；worker 需要 leaderKey。</span>}
      </div>
    </div>
  );
}

export function RosterSection({
  roster,
  onSaved,
  onError,
}: {
  roster: RosterDocument;
  onSaved: () => void;
  onError: (text: string) => void;
}) {
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const commit = async (next: RosterDocument, message: string) => {
    try {
      await putRoster(next);
      onError(""); // 清掉上一条失败提示（成功路径）
      onSaved();
    } catch (err) {
      onError(`${message}失败：${errText(err)}`);
    }
  };

  const remove = (member: RosterMemberEntry) => {
    if (!window.confirm(`从名册移除成员「${member.key}」？\n这只改名册：若其进程仍在运行，请先停止。`)) return;
    void commit(
      { ...roster, members: roster.members.filter((m) => m.key !== member.key) },
      "移除成员",
    );
  };

  const saveMember = async (entry: RosterMemberEntry) => {
    const exists = roster.members.some((m) => m.key === entry.key);
    const members = exists
      ? roster.members.map((m) => (m.key === entry.key ? entry : m))
      : [...roster.members, entry];
    await commit({ ...roster, members }, exists ? "保存成员" : "添加成员");
    setEditingKey(null);
    setAdding(false);
  };

  return (
    <ConfigSection title="成员名册">
      <p className="mb-2 text-[11px] text-tx3">
        新增成员只写名册：provision 与 enrollment 两步预置仍需按脚本流程完成后，成员才能启动就绪。
      </p>
      {roster.members.map((member) => (
        <div key={member.key} className="border-b border-panel py-2.5">
          {editingKey === member.key ? (
            <RosterForm initial={member} isNew={false} onSave={saveMember} onCancel={() => setEditingKey(null)} />
          ) : (
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <span className="font-mono text-[12px] text-tx">{member.key}</span>
                <span className="ml-2 text-[10.5px] text-tx3">{member.role}</span>
                <div className="mt-px truncate font-mono text-[10.5px] text-tx3">
                  {shortId(member.agentId)}
                  {member.subsets?.length ? ` · subsets: ${member.subsets.join(", ")}` : ""}
                </div>
              </div>
              <div className="flex flex-none gap-1.5">
                <button type="button" className={SUBTLE_BUTTON} onClick={() => setEditingKey(member.key)}>
                  编辑
                </button>
                <button
                  type="button"
                  className="rounded-hard border border-line px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:border-salmon hover:text-salmon"
                  onClick={() => remove(member)}
                >
                  移除
                </button>
              </div>
            </div>
          )}
        </div>
      ))}

      {adding ? (
        <RosterForm initial={{ ...EMPTY_MEMBER }} isNew onSave={saveMember} onCancel={() => setAdding(false)} />
      ) : (
        <button type="button" className={`mt-3 ${SUBTLE_BUTTON}`} onClick={() => setAdding(true)}>
          ＋ 新增成员
        </button>
      )}
    </ConfigSection>
  );
}

