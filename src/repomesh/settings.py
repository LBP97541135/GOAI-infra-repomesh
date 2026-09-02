from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from repomesh.modules.agent_runtime.ports.agent_team import ManagerRuntime, WorkerRuntime


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPOMESH_", extra="ignore")

    app_name: str = "RepoMesh"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    supervised: bool = False
    database_url: str = "postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh"
    local_session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    agentteams_required: bool = False
    agentteams_controller_url: str = "http://localhost:8090"
    agentteams_controller_token: str | None = None
    agentteams_matrix_url: str = "http://localhost:6167"
    agentteams_matrix_access_token: str | None = None
    #: Which runtime the projected Manager/Worker resources ask the controller
    #: for. Not a free choice: the controller pairs each runtime with its own
    #: image env (``AGENTTEAMS_COPAW_WORKER_IMAGE`` vs ``AGENTTEAMS_WORKER_IMAGE``
    #: for openclaw), so asking for a runtime whose image that controller has
    #: not been given spawns containers that exit(1) on boot, never obtain a
    #: Matrix identity, and fail every dispatch — the root cause under defect
    #: A-6. Hardcoded ``openclaw`` on both projection paths before this;
    #: ``copaw`` is the pairing this deployment actually has. Typed as the wire
    #: enums so an unknown value fails at startup rather than at first
    #: dispatch, and so the allowed set is not copied here.
    agentteams_manager_runtime: ManagerRuntime = ManagerRuntime.COPAW
    agentteams_worker_runtime: WorkerRuntime = WorkerRuntime.COPAW
    runner_control_token: str | None = None
    #: Worker-scoped runner credentials: a JSON object mapping worker agent id
    #: to that worker's own bearer token, e.g. ``{"<uuid>": "<token>"}``. The
    #: token above has no subject and so opens every worker's queue; one of
    #: these names exactly one worker, which is all an out-of-cluster Bridge
    #: needs (ADR 0004 decision 6). Kept as an env document rather than a table
    #: because issuance is a deployment act this round, not a product feature.
    runner_worker_tokens: str | None = None
    # Test-team Leader credentials are separate from worker/runtime credentials.
    # JSON map: {"<leader-agent-id>": "<token>"}.
    test_team_leader_tokens: str | None = None
    #: How long an external member's readiness report is believed for. Short on
    #: purpose: the lease is the only evidence that a Bridge on somebody's own
    #: machine is still running, and a long one is a promise about a process
    #: nobody has heard from. A reporter is told to renew after a third of it,
    #: so it may miss two reports before its lease runs out.
    external_readiness_ttl_seconds: int = 45
    agent_action_token: str | None = None
    #: Hosts the scan endpoints may reach, comma separated. Anything else is
    #: refused before a request leaves this process. Known hosting platforms
    #: (github.com, *.github.com, gitlab.com, any host whose name contains
    #: "gitlab") are reachable without an entry — platform support must not be
    #: gated behind a per-domain list. This allowlist only gates custom domains
    #: the platform detection cannot name.
    repository_scan_allowed_hosts: str = "github.com"
    #: Explicit host→platform mapping for self-hosted instances, comma
    #: separated ``host=platform`` pairs (e.g.
    #: ``git.mycorp.com=gitlab,code.mycorp.com=github``). Platforms outside
    #: the built-in known set are otherwise refused as unknown.
    repository_scan_platforms: str = ""
    #: Whether org scans include fork repositories. Default ``False``: a fork
    #: duplicates its upstream's service and would register a second catalog
    #: row for the same code. ``archived``/``empty`` are always skipped — they
    #: are platform facts — while this switch is the operator's preference.
    repository_scan_include_forks: bool = False
    #: Credentials the *console* scan endpoints use to read private
    #: repositories. The console's request bodies carry no token field on
    #: purpose (a browser is not a place to type a PAT), so this env is the
    #: only way to reach a private repo from the GUI. The native RI endpoints
    #: still accept a token in the body for scripts and operators.
    repository_scan_github_token: str = ""
    repository_scan_gitlab_token: str = ""
    #: AgentTeams probe budget per row, and how many may be in flight at once.
    runtime_probe_timeout_seconds: float = 2.0
    runtime_probe_concurrency: int = 16
    worker_task_control_url: str | None = None
    agentteams_storage_root: Path = Path(".agentteams-storage")
    agentteams_storage_endpoint: str | None = None
    agentteams_storage_access_key: str | None = None
    agentteams_storage_secret_key: str | None = None
    agentteams_storage_bucket: str = "agentteams-storage"
    mcp_gateway_token: str | None = None
    mcp_gateway_tokens: tuple[str, ...] = ()
    direct_worker_mcp_enabled: bool = False
    runner_workspace_root: Path = Path(".repomesh-workspaces")
    worker_execution_reservation_lease_seconds: int = Field(default=300, ge=30)
    worker_execution_reservation_wait_seconds: int = Field(default=30, ge=1)
    worker_recovery_enabled: bool = False
    worker_recovery_scan_interval_seconds: int = Field(default=15, ge=5)
    worker_recovery_grace_seconds: int = Field(default=60, ge=10)
    worker_recovery_max_execution_attempts: int = Field(default=3, ge=1)
    worker_recovery_max_reassignments: int = Field(default=2, ge=0)
    capability_root: Path = Path(".")
    # OTLP/HTTP collector base URL (e.g. a local AgentScope Studio). None keeps
    # tracing off; see docs/development/observability-instrumentation-plan-20260807.md.
    otlp_endpoint: str | None = None
    otlp_service_name: str = "repomesh-api"
    # OTLP exporter request headers, "k=v,k2=v2" (e.g. Alibaba Cloud AgentLoop:
    # x-arms-license-key,x-arms-project,x-cms-workspace).
    otlp_headers: str | None = None
    # Additional OTLP signals: metrics (MeterProvider + /v1/metrics) and logs
    # (LoggingHandler + /v1/logs with trace_id attached). Disabled by default.
    otlp_metrics_enabled: bool = False
    otlp_logs_enabled: bool = False
    otlp_log_level: str = "WARNING"
    # The product exposes one default model connection. Legacy planning-specific
    # names remain aliases so existing deployments can override it independently.
    deepseek_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REPOMESH_DEEPSEEK_API_KEY",
            "REPOMESH_MODEL_API_KEY",
            "DEEPSEEK_API_KEY",
        ),
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        validation_alias=AliasChoices(
            "REPOMESH_DEEPSEEK_BASE_URL",
            "REPOMESH_MODEL_BASE_URL",
        ),
    )
    deepseek_model: str = Field(
        default="deepseek-chat",
        validation_alias=AliasChoices(
            "REPOMESH_DEEPSEEK_MODEL",
            "REPOMESH_MODEL",
        ),
    )
    #: L3 semantic retrieval (decision-chain RAG). The endpoint must speak the
    #: OpenAI-compatible ``POST /embeddings`` shape — OpenAI, a local Ollama
    #: (``http://localhost:11434/v1``) and SiliconFlow all do. Unset base URL
    #: disables semantic retrieval: the pipeline falls back to the structural
    #: similarity hits (fail-safe, same spirit as Phase 4b's "no history").
    embedding_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REPOMESH_EMBEDDING_BASE_URL",
            "REPOMESH_EMBEDDING_URL",
        ),
    )
    embedding_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REPOMESH_EMBEDDING_API_KEY",
            "REPOMESH_EMBEDDING_KEY",
        ),
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices("REPOMESH_EMBEDDING_MODEL"),
    )
    embedding_timeout_seconds: float = Field(default=30.0, ge=1.0)
    github_app_id: int | None = None
    github_app_private_key_file: Path | None = None
    github_app_private_key_base64: SecretStr | None = None
    github_webhook_secret: str | None = None
    delivery_auto_enabled: bool = False
    # Local-dev alternative to the GitHub App pair above: one personal
    # token for every repository (StaticTokenProvider). The App path
    # wins when both are configured.
    delivery_github_token: str = ""
    delivery_base_branch: str = "main"
    delivery_required_checks: tuple[str, ...] = ()
    delivery_required_approvals: int = Field(default=1, ge=0)
    delivery_contract_gate: bool = False
    delivery_pr_label: bool = False
    delivery_reconcile_interval_seconds: int = Field(default=60, ge=5)
    delivery_recovery_interval_seconds: int = Field(default=30, ge=5)
    replan_auto_commit: bool = True
    """Default replan mode when the request says ``auto`` (PR-4).

    ``True`` preserves the pre-PR-4 behaviour — a replan request executes the
    full commit immediately. Set ``REPOMESH_REPLAN_AUTO_COMMIT=false`` to
    require an explicit approval round-trip: ``auto`` requests then run in
    ``preview`` mode (zero side effects) and a second call with
    ``mode=commit`` applies the change.
    """
    scm_observation_replay_interval_seconds: int = Field(default=15, ge=5)
    scm_poll_interval_seconds: int = Field(default=60, ge=5)
    scm_poll_scan_interval_seconds: int = Field(default=15, ge=5)
    scm_command_dispatch_interval_seconds: int = Field(default=5, ge=1)
    #: Requirement-sufficiency gate. The model's self-reported confidence must
    #: clear this bar or the analysis counts as insufficient regardless of
    #: what the model claims (fail-closed; the model's "yes" is never trusted
    #: below this confidence).
    discovery_analysis_confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    #: Default candidate-list size for the discovery step — the top-N cut
    #: after ranking. The panel omits ``limit`` and inherits this; script
    #: callers may pass their own.
    discovery_candidate_limit: int = Field(default=10, ge=1, le=50)
    #: Ceiling for the keyword-fallback score, kept below 1.0 so a model
    #: verdict reaching full confidence stays distinguishable from
    #: term-frequency arithmetic.
    discovery_keyword_score_cap: float = Field(default=0.99, ge=0.0, le=1.0)
    #: Bounded parallelism for the confirmation (细筛) LLM calls. This is a
    #: global rate-limit approximation — several issues confirming at once
    #: must stay under the provider's RPM/TPM, so the per-issue value should
    #: be conservative, not aggressive.
    discovery_confirmation_concurrency: int = Field(default=4, ge=1, le=16)
    #: Cap for the Project Manager's graph pre-supplement: how many
    #: first-degree dependency neighbours may join the confirmation list
    #: beyond the scored candidates. ``0`` disables the supplement. The value
    #: is a starting point — track the recorded supplement counts and adjust.
    discovery_confirmation_supplement_cap: int = Field(default=5, ge=0, le=50)
    #: Lease for a dispatched SCM command (task) before another runner may
    #: reclaim it after a crash; renewal interval keeps the lease warm while
    #: the command still executes.
    scm_command_lease_seconds: int = Field(default=300, ge=10)
    scm_command_lease_renew_interval_seconds: int = Field(default=60, ge=1)
    operations_alert_action: Literal["none", "degrade_writes", "pause_intake"] = "none"
    operations_capacity_retry_after_seconds: int = Field(default=30, ge=1, le=3600)
    operations_backup_configured: bool = False
    operations_last_backup_age_hours: int | None = Field(default=None, ge=0)
    operations_restore_drill_age_days: int | None = Field(default=None, ge=0)
    operations_usage_retention_days: int = Field(default=90, ge=7, le=3650)
    operations_log_retention_days: int = Field(default=30, ge=7, le=3650)
    operations_trace_retention_days: int = Field(default=30, ge=7, le=3650)
    operations_retention_batch_size: int = Field(default=500, ge=1, le=10000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
