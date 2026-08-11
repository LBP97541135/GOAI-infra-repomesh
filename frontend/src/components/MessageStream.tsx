import { useEffect, useRef } from "react";
import type { ChatMessage, DeliveryView } from "../types";
import { ApproveArtifact, DagArtifact, FailArtifact, ScopeArtifact } from "./artifacts";

function Avatar({ name, tone }: { name: string; tone: ChatMessage["tone"] }) {
  const skin =
    tone === "user"
      ? "bg-[#4a4130] text-cream"
      : tone === "qa"
        ? "bg-salmon text-[#1c0f08]"
        : "bg-amber text-[#1c170c]";
  return (
    <span className={`inline-grid size-7 flex-none place-items-center rounded-hard text-[12px] font-extrabold ${skin}`}>
      {name.slice(0, 1)}
    </span>
  );
}

function MessageHead({ msg }: { msg: ChatMessage }) {
  return (
    <div className="mb-1.5 flex items-center gap-2">
      <Avatar name={msg.author} tone={msg.tone} />
      <b className="text-[13px] text-cream">{msg.author}</b>
      <span className="rounded-hard border border-line px-1.5 py-px font-mono text-[9.5px] font-bold tracking-[0.12em] text-tx2">
        {msg.role}
      </span>
      <time className="font-mono text-[11px] text-tx2">{msg.time}</time>
    </div>
  );
}

function Bubble({ msg }: { msg: ChatMessage }) {
  if (!msg.text && !msg.clarifications) return null;
  const skin = msg.tone === "user" ? "border-[#6e5828] bg-[#29200f]" : "border-line bg-panel";
  return (
    <div className={`ml-9 max-w-[720px] rounded-hard border px-3.5 py-2.5 text-[13px] ${skin}`}>
      {msg.text}
      {msg.clarifications?.map((c) => (
        <div key={c.q} className="mt-2 text-[12.5px] text-tx2">
          <b className="font-mono text-amber">Q</b> {c.q}
          <br />
          <b className="font-mono text-amber">A</b> {c.a}
        </div>
      ))}
    </div>
  );
}

export function MessageStream({
  messages,
  view,
  onApprove,
}: {
  messages: ChatMessage[];
  view: DeliveryView;
  onApprove: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const count = messages.length;

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [count]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-[22px] py-5">
      {messages.map((msg) => (
        <div key={msg.id} className="mb-5">
          <MessageHead msg={msg} />
          <Bubble msg={msg} />
          {msg.attach && (
            <div className="mt-2 ml-9 inline-flex items-center gap-2.5 rounded-hard border border-dashed border-line px-3 py-2 text-[12px] text-tx2">
              {msg.attach.label}
              <b className="font-mono text-[11.5px] text-tx">{msg.attach.name}</b>
              <span>{msg.attach.meta}</span>
            </div>
          )}
          {msg.artifact === "scope" && <ScopeArtifact view={view} />}
          {msg.artifact === "dag" && <DagArtifact view={view} />}
          {msg.artifact === "fail" && <FailArtifact view={view} />}
          {msg.artifact === "approve" && <ApproveArtifact view={view} onApprove={onApprove} />}
        </div>
      ))}
    </div>
  );
}
