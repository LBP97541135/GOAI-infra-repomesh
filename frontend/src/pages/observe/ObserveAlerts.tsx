import { AlertPanel } from "../../components/AlertPanel";
import { ObserveCrumb } from "./ObserveCrumb";

/** 观测 · 告警（#/observe/alerts）。
 *
 * 「在线监控与告警」场景的落地面：阈值规则管理 + 触发历史。数据源
 * `observability.alert_rules` + `alert_events`，规则按尾随窗口评估 llm_usage
 * 聚合指标。本页复用 AlertPanel（30s 轮询）；正在 firing 的告警横幅在门户页
 * 全局可见，不在此重复渲染。 */

export function ObserveAlerts() {
  return (
    <div className="max-w-[860px]">
      <ObserveCrumb section="告警" />
      <p className="mt-4 text-[11.5px] leading-relaxed text-tx3">
        在线监控与告警：规则按尾随窗口评估 LLM 用量聚合指标（成功率 / 错误数 /
        P95 延迟 / 成本 / 调用数），违反 → 触发中（firing）、恢复 → 已恢复。
        后台任务每 60s 评估一轮，「立即评估」按钮手动补一轮；正在触发中的告警
        显示在观测门户顶部横幅。
      </p>
      <AlertPanel />
    </div>
  );
}
