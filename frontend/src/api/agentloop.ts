/** AgentLoop 控制台跳转配置（`/api/v1/observe/agentloop/config`）。
 *
 *  **有意不并进 `client.ts`**：那份文件正承载另一条未提交的改造（期 2 之前
 *  的读模型调整），观测入口这个小读面不值得在它的差异里掺一脚——等两边
 *  都落定再合并。端点自身镜像 observability 模块其它 GET 的鉴权（动作
 *  token），base 与 token 的取法和 client 完全一致。
 *
 *  返回的 `console_url` 是服务端从部署既有 OTLP 配置推导的地址；用户在
 *  配置弹层里手动改过的地址只存本机（localStorage，见 ObserveHome），
 *  不回传服务端。 */
import { browserApiToken } from "../runtimeConfig";

export interface AgentLoopConfig {
  configured: boolean;
  console_url: string | null;
  region: string | null;
  project: string | null;
  workspace: string | null;
  /** override = 部署显式配了地址模板；derived = 从 OTLP 配置推导；unconfigured = 没配上报 */
  source: "override" | "derived" | "unconfigured";
}

export async function fetchAgentloopConfig(): Promise<AgentLoopConfig> {
  const base = import.meta.env.VITE_API_BASE ?? "";
  const res = await fetch(`${base}/api/v1/observe/agentloop/config`, {
    headers: { Authorization: `Bearer ${browserApiToken()}` },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as AgentLoopConfig;
}
