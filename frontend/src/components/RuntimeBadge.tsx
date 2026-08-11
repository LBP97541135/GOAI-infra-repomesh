import type { RuntimeBlock } from "../api/contract";
import { runtimeDisplay, type RuntimeDisplay, type RuntimePhase } from "../display";

/** §4.4 运行时状态徽标，团队页与花名册页共用（两者的 runtime 块字段不同，
 *  但都带 `phase`，本组件只用这一个字段，故按结构收窄而不绑定具体页面类型）。
 *
 *  配色刻意「安静」：除 observed 外一律中性令牌，**不用 salmon**——探测不可达
 *  是契约规定的降级（HTTP 仍 200，持久化事实照常为真），涂成红色等于在说系统坏了。 */

const RUNTIME_SKIN: Record<RuntimeDisplay["kind"], string> = {
  probing: "border-line text-tx3",
  probe_failed: "border-line text-tx2",
  unreachable: "border-line text-tx2",
  absent: "border-line text-tx2",
  observed: "border-bluegray text-bluegray",
};

export function RuntimeBadge({
  phase,
  runtime,
  prefix = "运行时 · ",
}: {
  phase: RuntimePhase;
  runtime: RuntimeBlock<{ phase: string | null }>;
  prefix?: string;
}) {
  const d = runtimeDisplay(phase, runtime);
  return (
    <span className={`rounded-hard border px-2 py-px text-[11px] ${RUNTIME_SKIN[d.kind]}`} title={d.hint}>
      {prefix}
      {d.label}
    </span>
  );
}
