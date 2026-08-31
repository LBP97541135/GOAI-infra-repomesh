/** 平台就绪与 Coding Agent 探测（`/api/v1/setup/*`，main 装机向导的读面）。
 *
 *  这两个端点**无鉴权**（后端 `platform_setup.py` 只有 onboard 那条要管理员），
 *  故走 `api/client.ts` 的通道即可——带上动作 token 无害，端点不看它。
 *
 *  设置页此前对适配器清单写的是「没有一项有数据源」，那句话在这两个端点合进来之前
 *  是对的。现在**读**的那半有源了：装没装、认没认得上、能不能被已验证的驱动跑。
 *  仍然无源的是**写**（适配器注册表配置入口，二期）和 CLI 版本号（Controller 不
 *  回报），设置页的缺口清单据此收窄而不是清空。 */
import type {
  CodingAgentsProbe,
  SetupStatusView,
} from "./contract";
import { defaultClient } from "./client";
import { sessionRequest } from "./auth";

export interface CredentialItemStatus {
  set: boolean;
  masked: string | null;
  updated_at: string | null;
}

export interface CredentialStatus {
  model: {
    api_key: CredentialItemStatus;
    base_url: CredentialItemStatus;
    model: CredentialItemStatus;
  };
  github_app: {
    app_id: CredentialItemStatus;
    private_key: CredentialItemStatus;
    webhook_secret: CredentialItemStatus;
  };
}

export interface CredentialSaveReceipt {
  saved: boolean;
  restarting: boolean;
  restart_required: boolean;
}

export type BootstrapState =
  | "idle"
  | "pending"
  | "running"
  | "waiting_for_user"
  | "retryable_failure"
  | "terminal_failure"
  | "completed";

export type BootstrapPhase =
  | "waiting_for_model"
  | "installing_agentteams"
  | "verifying_controller"
  | "configuring_matrix"
  | "configuring_storage"
  | "writing_runtime_config"
  | "restarting_api"
  | "verifying_platform"
  | "complete";

export interface BootstrapStatus {
  operation_id: string | null;
  state: BootstrapState;
  phase: BootstrapPhase;
  attempt: number;
  retryable: boolean;
  error_code: string | null;
  error_detail: string | null;
  message: string;
  updated_at: string | null;
}

export type {
  AdapterAuthStatus,
  CodingAgentAdapterView,
  CodingAgentsProbe,
  SetupStatusView,
} from "./contract";

export function fetchSetupStatus(): Promise<SetupStatusView> {
  return defaultClient().getSetupStatus();
}

export function fetchCodingAgents(): Promise<CodingAgentsProbe> {
  return defaultClient().getCodingAgents();
}

export function fetchCredentialStatus(): Promise<CredentialStatus> {
  return sessionRequest<CredentialStatus>("/setup/credentials");
}

export function fetchBootstrapStatus(): Promise<BootstrapStatus> {
  return sessionRequest<BootstrapStatus>("/setup/bootstrap");
}

export function retryBootstrap(): Promise<BootstrapStatus> {
  return sessionRequest<BootstrapStatus>("/setup/bootstrap/retry", { method: "POST" });
}

export function putModelCredential(payload: {
  api_key: string;
  base_url?: string;
  model?: string;
}): Promise<CredentialSaveReceipt> {
  return sessionRequest<CredentialSaveReceipt>("/setup/credentials/model", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function putGitHubAppCredential(payload: {
  app_id: number;
  private_key_pem: string;
  webhook_secret?: string;
}): Promise<CredentialSaveReceipt> {
  return sessionRequest<CredentialSaveReceipt>("/setup/credentials/github-app", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function onboardRepositories(payload: {
  organization_id: string;
  org_url: string;
  default_worker_count: number;
  scan_workers: number;
}): Promise<{ repositories: unknown[] }> {
  return sessionRequest("/setup/repositories/onboard", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
