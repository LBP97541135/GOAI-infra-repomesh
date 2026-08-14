from pathlib import Path

from .contracts import RunnerPermissionMode, RunnerTask


class RunnerTaskValidationError(RuntimeError):
    pass


class StrictRunnerTaskValidator:
    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root.resolve()

    async def validate(self, task: RunnerTask) -> None:
        if task.worker_agent_id is None:
            raise RunnerTaskValidationError("worker_agent_id_required")
        if task.workspace is None:
            raise RunnerTaskValidationError("workspace_required")
        if task.context_bundle.coding_package_hash is None:
            raise RunnerTaskValidationError("coding_package_hash_required")
        if task.permissions.mode is RunnerPermissionMode.BYPASS_PERMISSIONS:
            raise RunnerTaskValidationError("worker_bypass_permissions_denied")
        if not task.permissions.allowed_paths:
            raise RunnerTaskValidationError("allowed_paths_required")
        if set(task.permissions.allowed_tools) & set(task.permissions.disallowed_tools):
            raise RunnerTaskValidationError("tool_allow_deny_conflict")
        workspace = Path(task.workspace.path).resolve()
        if workspace == self._workspace_root or not workspace.is_relative_to(
            self._workspace_root
        ):
            raise RunnerTaskValidationError("workspace_outside_configured_root")
        if not workspace.is_dir():
            raise RunnerTaskValidationError("workspace_not_found")
        if task.workspace.base_sha != task.repository.base_revision:
            raise RunnerTaskValidationError("workspace_base_sha_mismatch")
