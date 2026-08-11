/** 回放模式场景状态机（CONS-13）：4 阶段推进 replay 夹具，
 *  参考原型 4 状态机（frontend-prototype ref-agent-workspace 场景切换器）。
 *  契约冻结 → 执行 → 失败修复 → 审批合并；clarify 决策仅存在于回放（契约 §6.5）。 */
import type { DeliveryData, DeliveryDataSource } from "../api/source";
import type { DisplayStatus } from "../api/contract";
import {
  IDS,
  aggregate,
  decisionsResponse,
  eventsPage,
  listResponse,
  messagesPage,
  overlay,
} from "./replay";

export interface ReplayScene {
  key: string;
  label: string;
  note: string;
  build: () => DeliveryData;
}

/** 终态（审批合并）快照 —— replay.ts 夹具的原始形状 */
function releaseScene(): DeliveryData {
  const data: DeliveryData = {
    list: structuredClone(listResponse),
    aggregate: structuredClone(aggregate),
    events: structuredClone(eventsPage),
    messages: structuredClone(messagesPage),
    decisions: structuredClone(decisionsResponse),
    overlay: structuredClone(overlay),
  };
  data.events.items.unshift({
    at: "2026-08-09T16:15:02Z",
    kind: "gate",
    text: "Release Guardian：saleor-core 门禁全绿，等待人工审批",
    task_id: null,
    repository_id: IDS.repo.core,
    payload_ref: null,
  });
  return data;
}

function patchListItem(data: DeliveryData, phase: DeliveryData["list"]["projects"][0]["deliveries"][0]["phase"], note: string, pending: number) {
  const item = data.list.projects[0].deliveries.find((d) => d.delivery_id === IDS.delivery);
  if (item) {
    item.phase = phase;
    item.phase_note = note;
    item.pending_decision_count = pending;
  }
}

function patchTask(
  data: DeliveryData,
  key: string,
  backend: "assigned" | "in_progress" | "succeeded",
  display: DisplayStatus,
  patch?: { attempt?: number; result?: string | null; clearRepair?: boolean },
) {
  const t = data.aggregate?.tasks.find((x) => x.task_key === key);
  if (!t) return;
  t.backend_status = backend;
  t.display_status = display;
  if (patch?.attempt !== undefined) t.attempt = patch.attempt;
  if (patch?.result !== undefined) t.result_summary = patch.result;
  if (patch?.clearRepair) t.repair_timeline = [];
}

function emptyDiffs(data: DeliveryData, note: Record<string, string>) {
  if (data.overlay) {
    data.overlay.repoDiffs = ["saleor-core", "saleor-dashboard", "saleor-apps", "saleor-docs"].map((id) => ({
      id,
      add: 0,
      del: 0,
      note: note[id] ?? "尚无变更",
      files: [],
    }));
  }
}

/** S1 契约冻结：PRD → 澄清 → 契约 v3 冻结与范围确认；clarify 决策入夹 */
function contractScene(): DeliveryData {
  const data = releaseScene();
  const agg = data.aggregate!;
  for (const key of ["T1", "T2", "T3", "T4", "T5"]) {
    patchTask(data, key, "assigned", "pending", { attempt: 1, result: null, clearRepair: true });
  }
  agg.change_set = null;
  agg.validation_snapshot = null;
  agg.diffs = [];
  data.events.items = [
    {
      at: "2026-08-09T14:55:20Z",
      kind: "plan",
      text: "Engineering Spec v3 冻结：4 验收标准 · 隐藏验收套件由 QA Guardian 独立执行",
      task_id: null,
      repository_id: null,
      payload_ref: null,
    },
    {
      at: "2026-08-09T14:31:07Z",
      kind: "matrix",
      text: "Product Analyst → 王倩：2 个关键歧义已确认，结论写入契约",
      task_id: null,
      repository_id: null,
      payload_ref: null,
    },
  ];
  data.messages.items = [];
  data.decisions.items = [];
  if (data.overlay) {
    data.overlay.chat = data.overlay.chat.slice(0, 3);
    data.overlay.runLabel = "RUN 0H53M";
    data.overlay.costLabel = "0.21M tok · ¥1.38 · 0h53m";
    data.overlay.stagingNote = "未部署";
  }
  emptyDiffs(data, {});
  patchListItem(data, "plan", "契约 v3 冻结 · DAG 生成中", 1);
  return data;
}

/** S2 执行：DAG 冻结，Worker 在隔离 Worktree 执行；T1 率先完成 */
function executeScene(): DeliveryData {
  const data = releaseScene();
  const agg = data.aggregate!;
  patchTask(data, "T2", "in_progress", "running", { result: "Runner 执行中 · 权限矩阵测试编写中" });
  patchTask(data, "T3", "in_progress", "running", {
    attempt: 1,
    result: "Runner 执行中 · GraphQL 类型生成完成",
    clearRepair: true,
  });
  agg.change_set = null;
  agg.validation_snapshot = null;
  agg.diffs = agg.diffs.filter((d) => d.commit_sha.startsWith("8825f6bb"));
  data.events.items = data.events.items.filter((e) =>
    ["15:02:08", "15:31:26", "15:47:31"].some((t) => e.at.includes(t)),
  );
  data.messages.items = data.messages.items.filter((m) => m.correlation_id === IDS.task.t4);
  data.decisions.items = [];
  if (data.overlay) {
    data.overlay.chat = data.overlay.chat.slice(0, 4);
    data.overlay.runLabel = "RUN 1H45M";
    data.overlay.costLabel = "0.86M tok · ¥5.10 · 1h45m";
    data.overlay.stagingNote = "未部署";
    data.overlay.repoDiffs = [
      {
        id: "saleor-core",
        add: 212,
        del: 18,
        note: "8825f6bb · T2 执行中",
        files: [
          { path: "saleor/order/models.py", add: 38, del: 2 },
          { path: "saleor/graphql/order/types.py", add: 54, del: 0 },
          { path: "migrations/0042_price_override_reason.py", add: 61, del: 0 },
          { path: "tests/order/test_price_override_reason.py", add: 59, del: 16 },
        ],
      },
      { id: "saleor-dashboard", add: 0, del: 0, note: "执行中 · 变更采集中", files: [] },
      { id: "saleor-apps", add: 0, del: 0, note: "执行中 · 变更采集中", files: [] },
      { id: "saleor-docs", add: 0, del: 0, note: "等待 T5 启动", files: [] },
    ];
  }
  patchListItem(data, "execute", "1 完成 · 3 执行中", 1);
  return data;
}

/** S3 失败修复：隐藏验收测试失败、治理拦截、Repair Loop 第 2 次尝试。
 *  docs 的候选 commit 尚未产出（T5 未执行）→ change_set 不含 docs 仓（主脑裁决时序）。 */
function repairScene(): DeliveryData {
  const data = releaseScene();
  const agg = data.aggregate!;
  const cs = agg.change_set!;
  cs.repositories = cs.repositories.filter((r) => r.repository_id !== IDS.repo.docs);
  if (agg.validation_snapshot) delete agg.validation_snapshot.candidate_heads[IDS.repo.docs];
  const core = cs.repositories.find((r) => r.repository_id === IDS.repo.core)!;
  core.status = "review_pending";
  core.gate_display = "running";
  core.reviews = [];
  core.merge_gate = { allowed: false, reasons: ["等待独立 Review"] };
  data.events.items = data.events.items.filter((e) => !e.at.includes("16:15:02"));
  data.decisions.items = data.decisions.items.filter((d) => d.kind === "watch");
  if (data.overlay) {
    data.overlay.chat = data.overlay.chat.slice(0, 5);
    data.overlay.runLabel = "RUN 2H01M";
    data.overlay.costLabel = "1.02M tok · ¥6.77 · 2h01m";
  }
  patchListItem(data, "validate", "1 门禁受阻 · 修复循环第 2 次", 2);
  return data;
}

export const SCENES: ReplayScene[] = [
  { key: "contract", label: "契约冻结", note: "PRD → 澄清 → 契约 v3 冻结与范围确认", build: contractScene },
  { key: "execute", label: "执行", note: "DAG 冻结 · 4 Worker 隔离 Worktree 执行", build: executeScene },
  { key: "repair", label: "失败修复", note: "隐藏验收失败 · 治理拦截 · Repair Loop", build: repairScene },
  { key: "release", label: "审批合并", note: "门禁全绿 · 快照绑定授权 · 按序合并", build: releaseScene },
];

export function createReplaySource(): DeliveryDataSource {
  let scene = SCENES.length - 1;
  return {
    mode: "replay",
    sceneCount: SCENES.length,
    setScene(index: number) {
      scene = Math.max(0, Math.min(SCENES.length - 1, index));
    },
    fetchAll: () => Promise.resolve(SCENES[scene].build()),
  };
}
