/** 数据源开关：live | replay。
 *  - live：打真实读模型 API（契约 §1 端点），后端未就绪时呈现可见失败态；
 *  - replay：本地夹具（issues / issueDetail / grid 三份，同一 issue 世界自洽），默认模式。
 *  选择优先级：URL 参数 ?source=live|replay > VITE_DATA_SOURCE > 默认 replay。
 *
 *  v1 控制台退役后，本模块只剩这个开关。原先的 `DeliveryDataSource` 接口与
 *  `createDataSource` / `createReplaySource`（含场景状态机）是 v1 那套「一次取全部、
 *  按场景推进」的取数模型；v2 各页自己按需取数（api/issues.ts、api/rooms.ts、
 *  api/grid.ts、api/decisions.ts），不再需要这层统一数据源。 */
export type DataSourceMode = "live" | "replay";

export function resolveDataSourceMode(): DataSourceMode {
  const param = new URLSearchParams(window.location.search).get("source");
  if (param === "live" || param === "replay") return param;
  const env = import.meta.env.VITE_DATA_SOURCE;
  if (env === "live" || env === "replay") return env;
  return "replay";
}
