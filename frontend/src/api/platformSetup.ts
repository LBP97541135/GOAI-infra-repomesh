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

export interface GitHubAppRegistrationStatus {
  app_id: number;
  slug: string;
  owner_login: string;
  owner_type: string;
  requested_owner_login: string | null;
  /** 建好 ≠ 装好。私有 App 不装在账号上，一个仓库都看不见。 */
  installed: boolean;
  installation_id: number | null;
  installation_account_login: string | null;
  installed_at: string | null;
  install_url: string;
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
  /** 明文表，与加密凭证并列而**不是** `github_app` 的下级：凭证在不在，
   *  和 App 归谁、装没装，是两件会各自为政地出错的事。尚未建 App 时为 null。 */
  github_app_registration: GitHubAppRegistrationStatus | null;
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

export interface GitHubAppManifest {
  state: string;
  github_url: string;
  /** 服务端生成并留了底的 App 名。GitHub 上 App 名全局唯一，前端自己编一个，
   *  服务端就不知道究竟什么名字送了出去——而那正是交换失败时用户在自己的 App
   *  列表里找到那个孤儿的唯一线索。 */
  app_name: string;
  owner_login: string | null;
  manifest: Record<string, unknown>;
}

export function createGitHubAppManifest(payload?: {
  displayName?: string;
  ownerLogin?: string;
  /** 重建会把 GitHub 上那个旧 App 变成孤儿，所以得显式说一声（后端 409）。 */
  replace?: boolean;
}): Promise<GitHubAppManifest> {
  return sessionRequest<GitHubAppManifest>("/setup/credentials/github-app/manifest", {
    method: "POST",
    body: JSON.stringify({
      // The browser's own origin is authoritative for the GitHub callback URL:
      // behind the nginx port mapping the Host header loses the external port.
      origin: window.location.origin,
      display_name: payload?.displayName?.trim() || null,
      owner_login: payload?.ownerLogin?.trim() || null,
      replace: payload?.replace ?? false,
    }),
  });
}

export interface GitHubAppInstallationCheck {
  installed: boolean;
  installation_id: number | null;
  account: string | null;
  restarting?: boolean;
}

/** 主动问 GitHub「这个 App 到底装在哪些账号上」。
 *
 *  不是锦上添花：GitHub 只在**安装那一刻**重定向到 `setup_url`，从 GitHub 自己
 *  界面装的用户永远不会触发我们的回调；而 `setup_url` 是创建时冻结的 origin
 *  化石，部署换了地址之后那条回调就永久死了。这是安装那半场唯一的可靠退路。 */
export function verifyGitHubAppInstallation(): Promise<GitHubAppInstallationCheck> {
  return sessionRequest<GitHubAppInstallationCheck>(
    "/setup/credentials/github-app/verify-installation",
    { method: "POST" },
  );
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
