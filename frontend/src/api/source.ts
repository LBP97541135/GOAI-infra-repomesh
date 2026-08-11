/** 数据源开关：live | replay。
 *  - live：打真实读模型 API（契约 §1 端点），后端未就绪时呈现可见失败态；
 *  - replay：本地夹具（issues / issueDetail / grid 三份，同一 issue 世界自洽），默认模式。
 *  选择优先级：URL 参数 ?source=live|replay > VITE_DATA_SOURCE > 默认 replay。
 *  各页自己按需取数（api/issues.ts、api/rooms.ts、api/grid.ts、api/decisions.ts），
 *  本模块只提供这个开关。 */
export type DataSourceMode = "live" | "replay";

export function resolveDataSourceMode(): DataSourceMode {
  const param = new URLSearchParams(window.location.search).get("source");
  if (param === "live" || param === "replay") return param;
  const env = import.meta.env.VITE_DATA_SOURCE;
  if (env === "live" || env === "replay") return env;
  return "replay";
}
