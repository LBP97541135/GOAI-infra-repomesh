import { useCallback, useEffect, useRef, useState } from "react";
import type { ScanTaskView, UrlIdentification } from "../api/contract";
import { ApiError } from "../api/client";
import {
  fetchScanTask,
  forgetScanTask,
  identifyUrl,
  recallScanTask,
  rememberScanTask,
  startOrgScan,
  startRepoScan,
} from "../api/repositoryScan";
import { errText } from "../display";

/** 仓库页 · 添加仓库内嵌卡片（批次 A-3/A-4）。
 *  设计依据 `docs/development/full-loop-gui-design-20260812.md` ① + 原型
 *  `frontend-prototype/full-loop-surfaces.html`。
 *
 *  三条立意，实现时都能在下面找到对应代码：
 *
 *  **一个 URL 输入框走天下，判定不在前端**：防抖调 `GET /repositories/url-type`，
 *  徽标逐字回显后端结论，提交时按同一结论选 scan-org / scan-repo（服务端还会再复核
 *  一次）。前端不写第二套 `detect_platform`——镜像一份 TS 判定必然与 Python 漂移。
 *
 *  **token 不进 GUI**：卡片里明说凭据由服务端 env 配置；请求体是白名单，连字段都没有。
 *
 *  **长任务可以走开**：202 拿到的 task_id 存 sessionStorage，回到页面自动恢复轮询；
 *  存根只存 id，计数一律现问（见 api/repositoryScan.ts 的说明）。 */

const DEBOUNCE_MS = 400;
const POLL_MS = 2000;

/** 徽标：**只是后端结论的回显**，不是「能不能扫」的预测。
 *  allowlist 之外的 host 一样会被判成 group/single_repo，能不能扫由提交后的 400 说了算，
 *  所以这里不做任何提前劝退。 */
function VerdictBadge({
  urlEmpty,
  mode,
  verdict,
  identifyError,
}: {
  urlEmpty: boolean;
  mode: "live" | "replay";
  verdict: UrlIdentification | null;
  identifyError: string | null;
}) {
  // 输入为空不发请求，也就没有可回显的结论
  if (urlEmpty) return null;

  const shell = "rounded-hard border px-2 py-px text-[11px]";

  if (mode === "replay") {
    return (
      <span className={`${shell} border-line text-tx2`}>回放模式不识别 · 判定在后端</span>
    );
  }
  if (identifyError) {
    return (
      <span className={`${shell} border-salmon text-salmon`} title={identifyError}>
        识别请求失败：{identifyError.slice(0, 48)}
      </span>
    );
  }
  // verdict 为 null = 防抖窗口内或请求在途（也包括「结论比输入旧」的一刻）
  if (!verdict) return <span className={`${shell} border-line text-tx2`}>识别中…</span>;

  if (verdict.url_type === "group") {
    return <span className={`${shell} border-amber text-amber`}>识别为组织 · 将批量扫描</span>;
  }
  if (verdict.url_type === "single_repo") {
    return (
      <span className={`${shell} border-amber text-amber`}>
        识别为单仓{verdict.repository_name ? ` · ${verdict.repository_name}` : ""}
      </span>
    );
  }
  return <span className={`${shell} border-line text-tx2`}>无法识别</span>;
}

/** 进度 / 结果区。三态措辞照契约字段的精度写，不多说一个字：
 *   - running：`total` 在组织枚举返回前恒 0 → 显示「n / ?」，不编总数；
 *   - succeeded：三个计数此时才是终值；失败仓**只有计数**，详情在服务端日志；
 *   - failed：注册计数在失败态恒 0，**不当结果展示**——只报错误原文。 */
function ScanProgress({ task }: { task: ScanTaskView }) {
  if (task.status === "running") {
    return (
      <p className="text-[11.5px] text-tx2">
        扫描中 <span className="font-mono text-tx">{task.scanned}</span> /{" "}
        <span className="font-mono text-tx">{task.total === 0 ? "?" : task.total}</span>
        {task.last_scanned_repository && (
          <> · 最近：<span className="font-mono text-tx">{task.last_scanned_repository}</span></>
        )}
      </p>
    );
  }

  if (task.status === "succeeded") {
    return (
      <p className="text-[11.5px] text-tx2">
        扫描完成 · 注册 <span className="font-mono text-olive">{task.registered}</span> / 跳过{" "}
        <span className="font-mono text-tx">{task.skipped}</span>（已存在）/ 失败{" "}
        <span className={`font-mono ${task.failed > 0 ? "text-salmon" : "text-tx"}`}>
          {task.failed}
        </span>
        {task.failed > 0 && "（详情见服务端日志）"}
      </p>
    );
  }

  return (
    <>
      <p className="text-[11.5px] text-salmon">
        扫描失败：{task.error ?? "服务端未给出原因"}
      </p>
      <p className="mt-1 text-[11px] text-tx3">
        注册 / 跳过 / 失败三个计数只在扫描成功后是终值，失败态下不展示，以免把 0 当成结论。
      </p>
    </>
  );
}

export function AddRepositoryCard({
  open,
  mode,
  onClose,
  onRestored,
  onScanSettled,
}: {
  open: boolean;
  /** replay 世界没有外部平台，扫描是真出站动作——不在回放里假装能做 */
  mode: "live" | "replay";
  onClose: () => void;
  /** 存根里捡回一次扫描时请求父级展开卡片（否则「回头再看」看不到） */
  onRestored: () => void;
  /** 扫描走到终态：刷新仓库列表（复用现有取数） */
  onScanSettled: () => void;
}) {
  const [url, setUrl] = useState("");
  const [verdict, setVerdict] = useState<UrlIdentification | null>(null);
  const [identifyError, setIdentifyError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<ScanTaskView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [lost, setLost] = useState(false);
  /** 已经因终态刷过列表的 task_id：重新打开卡片重放一次轮询时不再重复刷新 */
  const settledRef = useRef<string | null>(null);

  const trimmed = url.trim();

  /** 防抖识别。输入为空不发请求；每次输入变化都作废在途结果，
   *  所以晚到的旧响应绝不会盖掉新输入的结论。 */
  useEffect(() => {
    if (mode === "replay" || !trimmed) {
      setVerdict(null);
      setIdentifyError(null);
      return;
    }
    // 输入一变即作废旧结论 → 徽标立刻回到「识别中…」，不会拿上一次的判定糊当前的 URL
    setVerdict(null);
    setIdentifyError(null);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      identifyUrl(trimmed)
        .then((res) => !cancelled && setVerdict(res))
        .catch((err: unknown) => !cancelled && setIdentifyError(errText(err)));
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [trimmed, mode]);

  /** 挂载时从会话存根捡回未看完的扫描（本组件在卡片收起时也保持挂载，
   *  所以收起卡片不会中断轮询）。 */
  useEffect(() => {
    const stored = recallScanTask();
    if (!stored) return;
    // settledRef **不**在这里预置：恢复回来的扫描可能仍在跑，预置会让它真正完成时
    // 拿不到那次列表刷新。捡回一个已完成的任务多刷一次列表只是一次多余的 GET，
    // 而漏刷会让页面停在扫描前的旧列表上——两种代价不对等。
    setTaskId(stored);
    onRestored();
    // 仅挂载时执行一次：存根是「上次离开时的那一次扫描」，不随后续 render 反复捡
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 轮询。终态即停；404 按「状态随重启丢失」处理并清存根；
   *  其它错误不终止轮询（临时不可达不是结论）。 */
  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    let timer = 0;

    const poll = () => {
      fetchScanTask(taskId)
        .then((next) => {
          if (cancelled) return;
          setTask(next);
          setPollError(null);
          setLost(false);
          if (next.status === "running") {
            timer = window.setTimeout(poll, POLL_MS);
            return;
          }
          if (settledRef.current !== next.task_id) {
            settledRef.current = next.task_id;
            onScanSettled();
          }
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          if (err instanceof ApiError && err.status === 404) {
            // 端点 detail 自述：任务状态只活在那个进程里
            forgetScanTask();
            setTaskId(null);
            setTask(null);
            setLost(true);
            return;
          }
          setPollError(errText(err));
          timer = window.setTimeout(poll, POLL_MS);
        });
    };

    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [taskId, onScanSettled]);

  const start = useCallback(
    (target: string, kind: "organization" | "repository") => {
      setSubmitting(true);
      setSubmitError(null);
      setPollError(null);
      setLost(false);
      const started = kind === "organization" ? startOrgScan(target) : startRepoScan(target);
      started
        .then((created) => {
          rememberScanTask(created.task_id);
          settledRef.current = null;
          setTask(created);
          setTaskId(created.task_id);
        })
        // 400（本地路径 / allowlist 外 host / 组织 URL 发到单仓端点）与 422 都在这里，
        // 服务器 detail 原文展示——它比前端的任何预判都准
        .catch((err: unknown) => setSubmitError(errText(err)))
        .finally(() => setSubmitting(false));
    },
    [],
  );

  const submit = () => {
    if (submitting || !trimmed) return;
    if (mode === "replay") {
      setSubmitError("回放模式不写后端；加 ?source=live 后可真实扫描，链接已保留");
      return;
    }
    // 判定还没回来 / 后端说无法识别时不猜端点：两个扫描端点收的是两种 URL，
    // 前端替后端选一个等于把判定搬回前端
    if (!verdict || verdict.url_type === "unknown") return;
    start(trimmed, verdict.url_type === "group" ? "organization" : "repository");
  };

  /** 裁决 1：失败处置只有整体重扫（幂等，已注册的自动跳过），不做单仓重试。
   *  重扫用任务自己记的 url/kind，不用输入框现值——那可能已经被改过了。 */
  const rescan = () => {
    if (!task || submitting) return;
    start(task.url, task.kind);
  };

  if (!open) return null;

  const identified = mode === "live" && verdict !== null && verdict.url_type !== "unknown";
  const canSubmit = trimmed !== "" && !submitting && (mode === "replay" || identified);

  return (
    <div className="mt-3 rounded-hard border border-line bg-panel-2 px-4 py-3">
      <div className="flex items-baseline gap-2">
        <span className="text-[12.5px] text-cream">添加备选仓库</span>
        <button className="ml-auto text-[13px] text-tx2 hover:text-amber-hi" onClick={onClose}>
          ✕
        </button>
      </div>

      <p className="mt-0.5 text-[11px] text-tx3">
        贴组织链接批量扫描，或贴单个仓库链接。识别由后端判定，界面只回显结论。
      </p>
      <p className="mt-0.5 text-[11px] text-tx3">
        私有仓凭据由服务端环境变量配置（REPOMESH_REPOSITORY_SCAN_GITHUB_TOKEN /
        _GITLAB_TOKEN），本界面不接受凭据。
      </p>

      <input
        className="mt-2.5 w-full rounded-hard border border-line bg-ink px-2.5 py-[6px] font-mono text-[12px] text-tx placeholder:text-tx3 focus:border-amber focus:outline-none"
        placeholder="https://github.com/acme-corp 或 https://github.com/acme-corp/checkout"
        value={url}
        spellCheck={false}
        onChange={(e) => setUrl(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
      />

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <VerdictBadge
          urlEmpty={trimmed === ""}
          mode={mode}
          verdict={verdict}
          identifyError={identifyError}
        />
        <button
          className="ml-auto rounded-hard bg-amber px-3.5 py-[6px] text-[12px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
          disabled={!canSubmit}
          onClick={submit}
        >
          {submitting ? "提交中…" : "扫描并添加"}
        </button>
      </div>

      {/* 两种「提交按钮点不动」都说出原因：判定拿不到时前端不替后端选端点，
          但用户得知道卡在哪、怎么再来一次 */}
      {mode === "live" && trimmed !== "" && verdict?.url_type === "unknown" && (
        <p className="mt-1.5 text-[11px] text-tx3">
          后端未能判断这个链接指向组织还是单个仓库，无从选择扫描方式——换一个链接再试。
        </p>
      )}
      {mode === "live" && trimmed !== "" && identifyError && (
        <p className="mt-1.5 text-[11px] text-tx3">
          识别没能完成，因此还不知道该走组织扫描还是单仓扫描——改动链接即重新识别。
        </p>
      )}

      {submitError && (
        <p className="mt-2 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] text-salmon">
          {submitError}
        </p>
      )}

      {lost && (
        <p className="mt-2 rounded-hard border border-line px-2.5 py-1.5 text-[11.5px] text-tx2">
          扫描状态已随服务重启丢失，重新扫描安全且会跳过已注册仓库。
        </p>
      )}

      {(task || taskId) && (
        <div className="mt-2.5 border-t border-line pt-2.5">
          {task ? (
            <ScanProgress task={task} />
          ) : (
            <p className="text-[11.5px] text-tx2">正在读取扫描状态…</p>
          )}

          {pollError && (
            <p className="mt-1 text-[11px] text-salmon">
              进度请求失败（仍在重试）：{pollError.slice(0, 80)}
            </p>
          )}

          {task && task.status !== "running" && (
            <div className="mt-2 flex items-center gap-2">
              <button
                className="rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi disabled:opacity-60"
                disabled={submitting}
                onClick={rescan}
              >
                重新扫描
              </button>
              <span className="text-[11px] text-tx3">
                重扫是整次扫描（幂等，已注册的自动跳过）；不提供单仓重试。
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
