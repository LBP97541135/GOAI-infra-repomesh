/** 读模型 API typed client。端点与 JSON 形状唯一来源：
 *  docs/contracts/delivery-read-model-v0.1.md §1-§4。 */
import type {
  DecisionsResponse,
  DeliveryAggregate,
  DeliveryEventsPage,
  DeliveryListResponse,
  DeliveryMessagesPage,
  GovernanceDecisionRequest,
} from "./contract";

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;

  constructor(status: number, url: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

export interface ApiClientConfig {
  /** 形如 ""（同源）或 "http://127.0.0.1:8000"；路径前缀 /api/v1 固定 */
  baseUrl: string;
  /** Bearer token；空则不带 Authorization 头 */
  token: string;
}

async function request<T>(config: ApiClientConfig, method: string, path: string, body?: unknown): Promise<T> {
  const url = `${config.baseUrl}/api/v1${path}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (config.token) headers.Authorization = `Bearer ${config.token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (cause) {
    throw new ApiError(0, url, `无法连接 ${url}：${cause instanceof Error ? cause.message : String(cause)}`);
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(res.status, url, `${method} ${path} → HTTP ${res.status}${detail ? ` · ${detail.slice(0, 200)}` : ""}`);
  }
  return (await res.json()) as T;
}

export function createApiClient(config: ApiClientConfig) {
  return {
    listDeliveries: (cursor?: string) =>
      request<DeliveryListResponse>(config, "GET", `/deliveries${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`),

    getDelivery: (deliveryId: string) =>
      request<DeliveryAggregate>(config, "GET", `/deliveries/${deliveryId}`),

    getEvents: (deliveryId: string, cursor?: string) =>
      request<DeliveryEventsPage>(
        config,
        "GET",
        `/deliveries/${deliveryId}/events${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
      ),

    getMessages: (deliveryId: string, cursor?: string) =>
      request<DeliveryMessagesPage>(
        config,
        "GET",
        `/deliveries/${deliveryId}/messages${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
      ),

    getDecisions: (deliveryId: string) =>
      request<DecisionsResponse>(config, "GET", `/deliveries/${deliveryId}/decisions`),

    postGovernanceDecision: (deliveryId: string, payload: GovernanceDecisionRequest) =>
      request<unknown>(config, "POST", `/deliveries/${deliveryId}/governance-decisions`, payload),

    archiveDelivery: (deliveryId: string) =>
      request<unknown>(config, "POST", `/deliveries/${deliveryId}/archive`),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
