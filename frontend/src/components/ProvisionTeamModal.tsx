import { useState } from "react";
import { onboardRepositoryTeam } from "../api/humanControl";
import { errText } from "../display";
import { Modal } from "./Modal";

/** 「为仓库建团」弹窗（迁移 1：main 装机向导的建团能力进控制台）。
 *
 *  **补的是哪一段**：控制台的扫描接入只把仓库写进 catalog，不建人，所以仓库接入完
 *  停在「无驻扎团队」，而此前控制台没有任何地方能把它推到就绪——建团只发生在 issue
 *  物化时（`EnsureProjectAgentTopology` 顺路建）。本弹窗把「先建好人、再派活」补上，
 *  也让装机时的 AgentTeams 配置问题在这里就暴露，而不是等第一个 issue 物化才炸。
 *
 *  **与物化自动建团是同一批人，不是第二套**：两条路径都往 directory 里放该仓库的
 *  repository leader，而 leader 是目录单例（`repository:{id}:leader`）。谁先建，
 *  后来者复用谁——物化路径显式按 role+repository_id 查了再决定建还是用，所以预建之后
 *  再物化不会重复建人。反向（物化先建、这里后建）会撞单例，后端答 409「已经有人了」
 *  而不是 503「稍后重试」；入口本身也只对没有 leader 的仓库出现，正常路径够不到它。
 *
 *  **鉴权走登录会话**（`api/humanControl.ts`），与读模型的动作 token 是两套凭据。 */
export function ProvisionTeamModal({
  open,
  repositoryId,
  repositoryName,
  organizationId,
  onClose,
  onProvisioned,
  onToast,
}: {
  open: boolean;
  repositoryId: string;
  repositoryName: string;
  /** 当前选定工作区。未选定时上游不给入口（见 RepositoriesPage）。 */
  organizationId: string;
  onClose: () => void;
  onProvisioned: () => void;
  onToast: (text: string) => void;
}) {
  const [workerCount, setWorkerCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setErrorText(null);
    try {
      // 幂等键按仓库定，与 main 装机向导同一把（`repository-onboarding:{id}`）：
      // 两个入口互为重放而不是各建一套。
      const result = await onboardRepositoryTeam(repositoryId, {
        organization_id: organizationId,
        worker_count: workerCount,
        idempotency_key: `repository-onboarding:${repositoryId}`,
      });
      // 房间是 Controller 回报的事实：team_room_id 为 null = Team 建了但房间尚未
      // 发布。如实说，不写「已就绪」。
      const rooms = result.team.team_room_id === null ? "房间待发布" : "房间已就绪";
      onToast(
        `${result.repository_name} 已建团：${result.team.name} · 1 leader + ` +
          `${result.workers.length} worker · ${rooms}`,
      );
      onProvisioned();
      onClose();
    } catch (err) {
      // detail 原文上抛：409 已有团队 / 422 组织不是恰好一个活跃 Org Leader /
      // 403 需要管理员 / 503 AgentTeams 未配置——四种各有各的下一步，归并成
      // 「建团失败」会把可自助解决的前置问题伪装成系统故障。
      setErrorText(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      className="m-auto w-[min(520px,92vw)] rounded-[3px] border border-[#4a4128] bg-panel p-0 text-tx shadow-[0_24px_70px_rgba(0,0,0,0.7)]"
    >
      <div className="flex items-start justify-between border-b border-line px-[22px] pt-5 pb-3.5">
        <div>
          <span className="eyebrow">REPOSITORY TEAM</span>
          <h2 className="mt-1 text-[16px] font-semibold text-cream">为仓库建团</h2>
        </div>
        <button className="text-[18px] text-tx2" aria-label="关闭" onClick={onClose}>
          ×
        </button>
      </div>

      <div className="px-[22px] py-4">
        <p className="text-[12.5px] leading-[1.75] text-tx">
          将为 <b className="text-cream">{repositoryName}</b> 建出常驻的 Repository
          Leader 与 Worker，并在 AgentTeams 上装配它们的 Team。
          <b className="text-cream">人建在仓库上而不是某个 issue 上</b>
          ——此后每个触及该仓库的 issue 复用同一批人与同一个房间。
        </p>

        <label className="mt-4 block">
          <span className="microlabel mb-1.5 block">Worker 数量</span>
          <input
            type="number"
            min={1}
            max={20}
            value={workerCount}
            onChange={(e) => {
              const next = Number(e.target.value);
              // 后端 ge=1 le=20；这里夹住是为了不发一个注定 422 的请求
              setWorkerCount(Number.isFinite(next) ? Math.min(20, Math.max(1, next)) : 1);
            }}
            className="w-[110px] rounded-hard border border-line bg-ink px-3 py-1.5 font-mono text-[13px] text-tx focus:border-amber focus:outline-none"
          />
          <span className="mt-1 block text-[11px] text-tx3">
            1–20。Leader 恒为 1 个（仓库的目录单例）。模型与运行时用服务端默认值，
            控制台不镜像那份配置。
          </span>
        </label>

        {errorText && (
          <div className="mt-4 rounded-hard border border-salmon/60 bg-salmon/10 px-3 py-2 text-[12px] leading-[1.7] text-salmon">
            {errorText}
          </div>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-line px-[22px] py-3">
        <button
          className="rounded-hard border border-line px-3 py-1.5 text-[12.5px] text-tx2 hover:border-amber hover:text-amber-hi disabled:opacity-60"
          onClick={onClose}
          disabled={busy}
        >
          取消
        </button>
        <button
          className="rounded-hard bg-amber px-3.5 py-1.5 text-[12.5px] font-bold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
          onClick={submit}
          disabled={busy}
        >
          {busy ? "正在建团…" : "建团"}
        </button>
      </div>
    </Modal>
  );
}
