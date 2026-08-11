import { useEffect, useState } from "react";
import { invalidateGridCache } from "../api/grid";
import type { RuntimePhase } from "../display";

/** 团队页与花名册页共用的**两段式取数**（CONS-44 选型，见 api/grid.ts 头注）。
 *
 *  第一段 `with_runtime=false` 立刻渲染持久化事实（实测 0.06-0.10s），
 *  第二段 `with_runtime=true` 回填运行时（实测 ~2.1s，是超时上界不是均值）。
 *
 *  `phase` 存在的唯一理由是消解一个真实歧义：两段的响应里 `runtime` **都可能是
 *  `null`**——第一段是「没请求探测」，第二段才是「探测过、确实没有」。若拿
 *  `runtime === null` 直接判定，首屏会把尚未取得的事实显示成确定的「未接入」。
 *  故只有 `phase === "done"` 之后，才允许把 null 解读成 §4.4 的「无事实可报」。
 *
 *  第二段失败**不清空** rows：持久化那一半本来可读，§4.4 的立意就是不让运行时
 *  探测拖垮整页——前端这一侧同样成立。
 *
 *  `RuntimePhase` 与措辞派生 `runtimeDisplay()` 同住 display.ts：判定与文案分在两处
 *  会各自漂移，这是 CONS-42 已经收敛过一次的教训。 */
export interface RuntimeRows<T> {
  /** null = 首段尚未返回（或首段失败） */
  rows: T[] | null;
  /** 首段失败：持久化事实都没拿到，整页呈现可见失败态 */
  error: string | null;
  phase: RuntimePhase;
  /** 第二段失败原因；phase === "failed" 时有值 */
  probeError: string | null;
  retry: () => void;
}

export function useRuntimeRows<T>(fetcher: (withRuntime: boolean) => Promise<T[]>): RuntimeRows<T> {
  const [rows, setRows] = useState<T[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<RuntimePhase>("loading");
  const [probeError, setProbeError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);
    setPhase("loading");
    setProbeError(null);

    fetcher(false)
      .then((first) => {
        if (cancelled) return;
        setRows(first);
        // 第二段：整体替换而非逐行合并——同一次列举的两个快照，替换语义最直白
        fetcher(true)
          .then((second) => {
            if (cancelled) return;
            setRows(second);
            setPhase("done");
          })
          .catch((err: unknown) => {
            if (cancelled) return;
            setProbeError(err instanceof Error ? err.message : String(err));
            setPhase("failed");
          });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setPhase("failed");
      });

    return () => {
      cancelled = true;
    };
  }, [fetcher, reload]);

  return {
    rows,
    error,
    phase,
    probeError,
    // B1：手动重试是「我要新事实」的显式表达——绕过会话级缓存强制重取，
    // 否则 controller 恢复在线后重试按钮只会命中离线时代的缓存
    retry: () => {
      invalidateGridCache();
      setReload((n) => n + 1);
    },
  };
}
