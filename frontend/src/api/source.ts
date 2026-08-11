/** 数据源开关：live | replay。
 *  - live：打真实读模型 API（契约 §1 端点），后端未就绪时呈现可见失败态；
 *  - replay：本地夹具（Saleor 四仓回放案例 DLV-0042），默认模式。
 *  选择优先级：URL 参数 ?source=live|replay > VITE_DATA_SOURCE > 默认 replay。 */
import type {
  DecisionsResponse,
  DeliveryAggregate,
  DeliveryEventsPage,
  DeliveryListResponse,
  DeliveryMessagesPage,
  GovernanceDecisionRequest,
  GovernanceDecisionView,
} from "./contract";
import type { PresentationOverlay } from "../types";
import { ApiError, createApiClient } from "./client";
import { createReplaySource } from "../data/scenes";

export type DataSourceMode = "live" | "replay";

/** 一次交付视图所需的全部读模型数据（契约形状）+ 回放模式的演示叙事覆盖层。 */
export interface DeliveryData {
  list: DeliveryListResponse;
  aggregate: DeliveryAggregate | null;
  events: DeliveryEventsPage;
  messages: DeliveryMessagesPage;
  decisions: DecisionsResponse;
  /** 仅 replay 数据源提供：对话叙事、clarify 演示等契约未覆盖的 Demo 层 */
  overlay: PresentationOverlay | null;
}

export interface DeliveryDataSource {
  readonly mode: DataSourceMode;
  /** 拉取列表 + 指定（或默认）交付的全量视图数据 */
  fetchAll(deliveryId?: string): Promise<DeliveryData>;
  /** 回放场景切换（CONS-13 状态机使用；live 数据源为 undefined） */
  setScene?(index: number): void;
  readonly sceneCount?: number;
  /** 治理决策写回（契约 §4.4；仅 live 数据源提供，回放为前端演示） */
  submitGovernanceDecision?(deliveryId: string, payload: GovernanceDecisionRequest): Promise<GovernanceDecisionView>;
}

export function resolveDataSourceMode(): DataSourceMode {
  const param = new URLSearchParams(window.location.search).get("source");
  if (param === "live" || param === "replay") return param;
  const env = import.meta.env.VITE_DATA_SOURCE;
  if (env === "live" || env === "replay") return env;
  return "replay";
}

/** 可选端点（events/messages 属 CONS-04，decisions 派生）：404 = 端点未实现，
 *  降级为空页而非拖垮整个视图；其他错误照常抛出。 */
async function optional<T>(promise: Promise<T>, fallback: T): Promise<T> {
  try {
    return await promise;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return fallback;
    throw err;
  }
}

function createLiveSource(): DeliveryDataSource {
  const client = createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
  return {
    mode: "live",
    async fetchAll(deliveryId?: string): Promise<DeliveryData> {
      const list = await client.listDeliveries();
      const target =
        deliveryId ??
        list.projects.flatMap((p) => p.deliveries).find((d) => d.delivery_id !== null)?.delivery_id;
      if (!target) {
        return {
          list,
          aggregate: null,
          events: { items: [], next_cursor: null },
          messages: { items: [], next_cursor: null },
          decisions: { items: [] },
          overlay: null,
        };
      }
      const [aggregate, events, messages, decisions] = await Promise.all([
        client.getDelivery(target),
        optional(client.getEvents(target), { items: [], next_cursor: null }),
        optional(client.getMessages(target), { items: [], next_cursor: null }),
        optional(client.getDecisions(target), { items: [] }),
      ]);
      return { list, aggregate, events, messages, decisions, overlay: null };
    },
    submitGovernanceDecision: (deliveryId, payload) => client.postGovernanceDecision(deliveryId, payload),
  };
}

export function createDataSource(mode: DataSourceMode): DeliveryDataSource {
  return mode === "live" ? createLiveSource() : createReplaySource();
}
