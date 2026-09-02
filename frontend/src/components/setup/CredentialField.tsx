import type { ReactNode } from "react";

type CredentialFieldProps =
  | { mode: "editable"; label: string; note?: string; children: ReactNode }
  | { mode: "managed"; label: string; note: string }
  | { mode: "operations"; label: string; note: string };

export function CredentialField(props: CredentialFieldProps) {
  if (props.mode === "editable") {
    return (
      <label className="block border-b border-line py-3 last:border-b-0">
        <span className="mb-1.5 block text-[12px] font-semibold text-kraft">{props.label}</span>
        {props.children}
        {props.note ? <span className="mt-1 block text-[10.5px] text-tx3">{props.note}</span> : null}
      </label>
    );
  }
  return (
    <div className="border-b border-line py-3 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[12px] text-tx2">{props.label}</span>
        <span className="microlabel">{props.mode === "managed" ? "系统管理" : "高级运维"}</span>
      </div>
      <p className="mt-1 text-[10.5px] text-tx3">{props.note}</p>
    </div>
  );
}

export const credentialInputClass =
  "w-full rounded-hard border border-line bg-ink px-3 py-2 font-mono text-[12px] text-tx placeholder:text-tx3 focus:border-amber focus:outline-none";
