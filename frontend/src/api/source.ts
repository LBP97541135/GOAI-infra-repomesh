/** 数据源开关：live | replay。
 *  - live：打真实读模型 API（契约 §1 端点），后端未就绪时呈现可见失败态，默认模式；
 *  - replay：本地夹具（issues / issueDetail / grid 三份，同一 issue 世界自洽），
 *    显式带 ?source=replay 才进——它渲染的是编好的演示剧本，不是本机事实。
 *  选择优先级：URL 参数 ?source=live|replay > VITE_DATA_SOURCE > 默认 live。
 *  默认档 2026-08-12 由 replay 改为 live（用户裁决）：后端 8100 已常驻，
 *  「打开就是真数据」；早期「后端未就绪先看夹具」的取舍不再成立。
 *  各页自己按需取数（api/issues.ts、api/rooms.ts、api/grid.ts、api/decisions.ts），
 *  本模块只提供这个开关。 */
export type DataSourceMode = "live" | "replay";

export function resolveDataSourceMode(): DataSourceMode {
  const param = new URLSearchParams(window.location.search).get("source");
  if (param === "live" || param === "replay") return param;
  const env = import.meta.env.VITE_DATA_SOURCE;
  if (env === "live" || env === "replay") return env;
  return "live";
}
