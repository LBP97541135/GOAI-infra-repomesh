/** 工作区（Organization）数据源——契约 v0.3 §2（验收缺陷 B-2）。
 *
 *  仅 live 模式有数据：replay 是回放夹具，夹具世界没有组织注册表，切换器如实
 *  显示「回放模式不适用」而不是编一份列表。 */
import type { OrganizationCreateResponse, OrganizationView } from "./contract";
import { createApiClient } from "./client";
import { resolveDataSourceMode } from "./source";

function client() {
  return createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
}

/** replay 模式返回 null（区别于 live 的空列表 []：前者是「不适用」，后者是
 *  「注册表真的没有条目」——两态文案不同，不可合并）。 */
export async function fetchWorkspaces(): Promise<OrganizationView[] | null> {
  if (resolveDataSourceMode() === "replay") return null;
  const res = await client().listOrganizations();
  return res.organizations;
}

/** 幂等键按 §1.3/§2.3 客户端生成：每次逻辑创建一个新随机 UUID。 */
export async function createWorkspace(name: string): Promise<OrganizationCreateResponse> {
  return client().createOrganization({
    name,
    idempotency_key: crypto.randomUUID(),
  });
}
