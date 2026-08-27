import { useCallback, useEffect, useState } from "react";
import type { Account } from "../api/auth";
import type { SetupDependencyView, SetupStatusView } from "../api/contract";
import {
  fetchBootstrapStatus,
  fetchCredentialStatus,
  fetchSetupStatus,
  onboardRepositories,
  retryBootstrap,
  type BootstrapStatus,
  type CredentialStatus,
} from "../api/platformSetup";
import { fetchWorkspaces } from "../api/workspaces";
import { CredentialField, credentialInputClass } from "../components/setup/CredentialField";
import { GitHubAppCredentialForm } from "../components/setup/GitHubAppCredentialForm";
import { ModelCredentialForm } from "../components/setup/ModelCredentialForm";
import { errText } from "../display";

const STEP_NAMES = ["平台检测", "模型连接", "GitHub App", "管理员账号", "仓库与团队"];

const DEPENDENCY_LABEL: Record<string, string> = {
  model: "模型连接",
  database: "数据库",
  agentteams: "AgentTeams",
  matrix: "Matrix 消息面",
  internal_auth: "内部服务凭证",
  github_app: "GitHub App",
  administrator: "本地管理员",
  agent_directory: "智能体目录",
  repositories: "仓库目录",
};

const STATE_LABEL: Record<SetupDependencyView["state"], string> = {
  checking: "检测中",
  ready: "已就绪",
  missing: "等待自动补全",
  repairing: "自动补全中",
  waiting_for_user: "待你配置",
  failed: "自动补全失败",
  optional: "可稍后配置",
  pending_onboarding: "接入后生成",
};

const BOOTSTRAP_PHASE_LABEL: Record<BootstrapStatus["phase"], string> = {
  waiting_for_model: "等待模型连接",
  installing_agentteams: "安装 AgentTeams",
  verifying_controller: "验证 Controller",
  configuring_matrix: "配置 Matrix",
  configuring_storage: "配置对象存储",
  writing_runtime_config: "写入运行配置",
  restarting_api: "重启 RepoMesh API",
  verifying_platform: "验证完整平台",
  complete: "执行面已就绪",
};

const BOOTSTRAP_STATE_LABEL: Record<BootstrapStatus["state"], string> = {
  idle: "等待配置",
  pending: "已排队",
  running: "自动配置中",
  waiting_for_user: "等待你的配置",
  retryable_failure: "可以重试",
  terminal_failure: "需要检查环境",
  completed: "已完成",
};

function DependencyRow({ dependency }: { dependency: SetupDependencyView }) {
  const healthy = dependency.state === "ready";
  const userOwned = dependency.state === "waiting_for_user";
  const failed = dependency.state === "failed";
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-3 py-2.5 last:border-b-0">
      <span>
        <strong className="block text-[12px] font-medium text-tx">
          {DEPENDENCY_LABEL[dependency.id] ?? dependency.id}
        </strong>
        <span className="mt-0.5 block text-[10.5px] text-tx3">
          {dependency.owner === "system"
            ? "由启动器自动管理，无需填写"
            : dependency.remediation === "workflow"
              ? "在仓库与团队接入步骤生成"
              : dependency.remediation === "optional"
                ? "不影响进入控制台"
                : "需要你的外部服务信息"}
        </span>
      </span>
      <span className={`flex-none font-mono text-[11px] ${healthy ? "text-olive" : failed ? "text-salmon" : userOwned ? "text-amber" : "text-tx2"}`}>
        {STATE_LABEL[dependency.state]}
      </span>
    </div>
  );
}

function BootstrapProgress({
  status,
  retrying,
  onRetry,
  onOpenModel,
}: {
  status: BootstrapStatus;
  retrying: boolean;
  onRetry: () => void;
  onOpenModel: () => void;
}) {
  if (status.state === "idle") return null;
  const failed = status.state === "retryable_failure" || status.state === "terminal_failure";
  return (
    <section className="mb-5 border-y border-line py-3">
      <div className="flex min-h-10 items-center justify-between gap-4">
        <span className="min-w-0">
          <span className="eyebrow block">执行面自动配置</span>
          <strong className="mt-1 block text-[12.5px] font-medium text-cream">
            {BOOTSTRAP_PHASE_LABEL[status.phase]}
          </strong>
          {failed && status.error_detail ? (
            <span className="mt-1 block text-[10.5px] text-salmon">{status.error_detail}</span>
          ) : null}
        </span>
        <span className="flex-none text-right">
          <span className={`block font-mono text-[11px] ${failed ? "text-salmon" : status.state === "completed" ? "text-olive" : "text-amber"}`}>
            {BOOTSTRAP_STATE_LABEL[status.state]}
          </span>
          {status.attempt > 0 ? <span className="mt-0.5 block text-[10px] text-tx3">第 {status.attempt} 次</span> : null}
        </span>
      </div>
      {status.retryable ? (
        <button className="mt-2 rounded-hard border border-amber px-3 py-1.5 text-[11px] text-amber disabled:opacity-50" disabled={retrying} onClick={onRetry}>
          {retrying ? "正在重试…" : "重试自动配置"}
        </button>
      ) : status.state === "waiting_for_user" ? (
        <button className="mt-2 text-[11px] text-amber hover:text-amber-hi" onClick={onOpenModel}>返回模型配置</button>
      ) : null}
    </section>
  );
}

export function SetupWizardPage({ account, onReady }: { account: Account; onReady: () => void }) {
  const [step, setStep] = useState(0);
  const [setup, setSetup] = useState<SetupStatusView | null>(null);
  const [credentials, setCredentials] = useState<CredentialStatus | null>(null);
  const [bootstrap, setBootstrap] = useState<BootstrapStatus | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [organizationId, setOrganizationId] = useState("");
  const [orgUrl, setOrgUrl] = useState("");
  const [workerCount, setWorkerCount] = useState(1);
  const [onboarding, setOnboarding] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const refresh = useCallback(async () => {
    setFailure(null);
    try {
      const [status, credentialStatus, workspaces, bootstrapStatus] = await Promise.all([
        fetchSetupStatus(),
        fetchCredentialStatus(),
        fetchWorkspaces(),
        fetchBootstrapStatus(),
      ]);
      setSetup(status);
      setCredentials(credentialStatus);
      setBootstrap(bootstrapStatus);
      if (!organizationId && workspaces?.length) setOrganizationId(workspaces[0].organization_id);
    } catch (reason) {
      setFailure(errText(reason));
    }
  }, [organizationId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (bootstrap?.state !== "pending" && bootstrap?.state !== "running") return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const status = await fetchBootstrapStatus();
        if (cancelled) return;
        setBootstrap(status);
        if (status.state === "completed") {
          await refresh();
        } else if (status.state === "pending" || status.state === "running") {
          timer = window.setTimeout(() => void poll(), 1500);
        }
      } catch (reason) {
        if (!cancelled) setFailure(errText(reason));
      }
    };
    timer = window.setTimeout(() => void poll(), 1500);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [bootstrap?.state, refresh]);

  const saved = (message: string) => {
    setNotice(message);
    void refresh();
  };

  const retry = async () => {
    setRetrying(true);
    setFailure(null);
    try {
      setBootstrap(await retryBootstrap());
    } catch (reason) {
      setFailure(errText(reason));
    } finally {
      setRetrying(false);
    }
  };

  const onboard = async (event: React.FormEvent) => {
    event.preventDefault();
    setOnboarding(true);
    setFailure(null);
    try {
      const result = await onboardRepositories({
        organization_id: organizationId,
        org_url: orgUrl,
        default_worker_count: workerCount,
        scan_workers: 5,
      });
      setNotice(`接入完成：处理 ${result.repositories.length} 个仓库。`);
      await refresh();
    } catch (reason) {
      setFailure(errText(reason));
    } finally {
      setOnboarding(false);
    }
  };

  const systemDependencies = setup?.dependencies.filter((item) => item.owner === "system") ?? [];
  const userDependencies = setup?.dependencies.filter((item) => item.owner === "user") ?? [];
  const onboardingDependencies =
    setup?.dependencies.filter((item) => item.owner === "onboarding") ?? [];
  return (
    <div className="min-h-screen bg-ink px-5 py-6 text-tx sm:px-8">
      <div className="mx-auto max-w-[1040px]">
        <header className="flex flex-wrap items-end justify-between gap-4 border-b border-line pb-4">
          <div>
            <div className="eyebrow mb-1">REPOMESH / 安装配置</div>
            <h1 className="text-[20px] font-semibold text-cream">平台启动向导</h1>
          </div>
          <button className="rounded-hard border border-line px-3 py-1.5 text-[11px] text-tx2 hover:border-amber hover:text-amber" onClick={() => void refresh()}>重新检测</button>
        </header>

        <div className="mt-5 grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
          <nav aria-label="安装步骤" className="border-r border-line pr-5">
            {STEP_NAMES.map((name, index) => (
              <button key={name} className={`mb-1 flex w-full items-center gap-3 rounded-hard px-2 py-2 text-left text-[12px] ${step === index ? "bg-panel-2 text-cream" : "text-tx2 hover:text-tx"}`} onClick={() => setStep(index)}>
                <span className={`grid size-6 place-items-center border font-mono text-[10px] ${step === index ? "border-amber text-amber" : "border-line text-tx3"}`}>{String(index + 1).padStart(2, "0")}</span>
                {name}
              </button>
            ))}
          </nav>

          <main className="min-w-0">
            {notice ? <div className="mb-4 border border-olive bg-olive/10 px-3 py-2 text-[11.5px] text-olive">{notice}</div> : null}
            {failure ? <div className="mb-4 border border-salmon bg-salmon/10 px-3 py-2 text-[11.5px] text-salmon">{failure}</div> : null}
            {bootstrap ? (
              <BootstrapProgress
                status={bootstrap}
                retrying={retrying}
                onRetry={() => void retry()}
                onOpenModel={() => setStep(1)}
              />
            ) : null}

            {step === 0 ? (
              <section>
                <h2 className="text-[15px] font-semibold text-cream">平台检测</h2>
                <p className="mt-1 text-[11.5px] text-tx2">系统依赖由产品启动器自动安装和接线；这里只展示进度，不要求你处理内部凭证。</p>
                <div className="eyebrow mt-5 mb-1.5">系统自动管理</div>
                <div className="border border-line bg-panel">
                  {systemDependencies.map((dependency) => (
                    <DependencyRow key={dependency.id} dependency={dependency} />
                  ))}
                </div>
                <div className="eyebrow mt-5 mb-1.5">你的配置</div>
                <div className="border border-line bg-panel">
                  {userDependencies.map((dependency) => (
                    <DependencyRow key={dependency.id} dependency={dependency} />
                  ))}
                </div>
                <details className="mt-5 border-t border-line pt-2">
                  <summary className="cursor-pointer py-2 text-[11.5px] text-tx2">高级运维信息</summary>
                  <CredentialField mode="managed" label="Runner / agent-action / MCP gateway token" note="启动器自动生成并持久化。" />
                  <CredentialField mode="operations" label="Controller / Matrix / MinIO" note="由 AgentTeams 安装器管理。" />
                  <CredentialField mode="operations" label="Coding Agent CLI 登录态" note="属于执行任务的目标机器或容器。" />
                </details>
              </section>
            ) : null}

            {step === 1 ? <section><h2 className="mb-3 text-[15px] font-semibold text-cream">模型连接配置</h2>{credentials ? <ModelCredentialForm status={credentials.model} onSaved={saved} /> : <p className="text-tx3">读取中…</p>}</section> : null}
            {step === 2 ? <section><h2 className="text-[15px] font-semibold text-cream">GitHub App 配置</h2><p className="mt-1 mb-3 text-[11.5px] text-tx2">可跳过；需要 GitHub 自动交付或 webhook 时再配置。</p>{credentials ? <GitHubAppCredentialForm status={credentials.github_app} onSaved={saved} /> : <p className="text-tx3">读取中…</p>}</section> : null}
            {step === 3 ? <section><h2 className="text-[15px] font-semibold text-cream">管理员账号</h2><div className="mt-4 border border-olive bg-olive/10 px-4 py-3"><span className="text-[12px] text-olive">已完成</span><p className="mt-1 text-[11px] text-tx2">当前管理员：{account.display_name}（{account.username}）</p></div></section> : null}
            {step === 4 ? (
              <section>
                <h2 className="text-[15px] font-semibold text-cream">仓库与团队接入</h2>
                <div className="mt-3 border border-line bg-panel">
                  {onboardingDependencies.map((dependency) => (
                    <DependencyRow key={dependency.id} dependency={dependency} />
                  ))}
                </div>
                <form className="mt-4" onSubmit={onboard}>
                  <CredentialField mode="editable" label="工作区 ID"><input className={credentialInputClass} value={organizationId} onChange={(e) => setOrganizationId(e.target.value)} required /></CredentialField>
                  <CredentialField mode="editable" label="GitHub / GitLab 组织地址"><input className={credentialInputClass} type="url" value={orgUrl} onChange={(e) => setOrgUrl(e.target.value)} placeholder="https://github.com/acme" required /></CredentialField>
                  <CredentialField mode="editable" label="每仓库 Worker 数"><input className={credentialInputClass} type="number" min="1" max="20" value={workerCount} onChange={(e) => setWorkerCount(Number(e.target.value))} required /></CredentialField>
                  <button className="mt-4 rounded-hard bg-amber px-4 py-2 text-[12px] font-bold text-paper-ink disabled:opacity-50" disabled={onboarding}>{onboarding ? "接入中…" : "扫描并接入"}</button>
                </form>
              </section>
            ) : null}

            <footer className="mt-7 flex items-center justify-between border-t border-line pt-4">
              <span className="text-[11px] text-tx3">{setup?.ready_for_project_creation ? "必需配置已就绪" : "完成未通过的必检项后即可进入控制台"}</span>
              <button className="rounded-hard border border-amber px-4 py-2 text-[12px] text-amber disabled:border-line disabled:text-tx3" disabled={!setup?.ready_for_project_creation} onClick={onReady}>进入控制台</button>
            </footer>
          </main>
        </div>
      </div>
    </div>
  );
}
