import asyncio
import ctypes
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


class WorkspacePreparationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedGitWorkspace:
    workspace_id: str
    path: Path
    base_sha: str
    created: bool


class GitWorktreeManager:
    """Prepare one isolated, immutable-base worktree for each Runner run."""

    def __init__(self, root: Path, *, git_binary: str = "git") -> None:
        self._root = root.resolve()
        self._git_binary = git_binary
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def prepare(
        self,
        *,
        repository_id: UUID,
        repository_url: str,
        base_revision: str,
        run_id: UUID,
        workspace_id: UUID | None = None,
    ) -> PreparedGitWorkspace:
        if not repository_url.strip() or not base_revision.strip():
            raise WorkspacePreparationError("repository URL and base revision are required")
        if base_revision.startswith("-"):
            raise WorkspacePreparationError("base revision cannot start with '-'")
        lock = self._locks.setdefault(repository_id, asyncio.Lock())
        async with lock:
            return await self._prepare_locked(
                repository_id, repository_url, base_revision, run_id, workspace_id
            )

    async def release(self, workspace: PreparedGitWorkspace) -> None:
        if not workspace.path.is_relative_to(self._root):
            raise WorkspacePreparationError("workspace is outside the configured root")
        mirror = self._root / "repositories" / f"{workspace.path.name}.git"
        metadata = workspace.path / ".repomesh" / "workspace.json"
        if metadata.is_file():
            document = json.loads(metadata.read_text(encoding="utf-8"))
            repository_id = UUID(str(document["repositoryId"]))
            mirror = self._mirror_path(repository_id)
        if workspace.path.exists():
            await self._run(
                "--git-dir",
                str(mirror),
                "worktree",
                "remove",
                "--force",
                str(workspace.path),
            )

    async def _prepare_locked(
        self,
        repository_id: UUID,
        repository_url: str,
        base_revision: str,
        run_id: UUID,
        requested_workspace_id: UUID | None,
    ) -> PreparedGitWorkspace:
        mirror = self._mirror_path(repository_id)
        workspace_id = str(requested_workspace_id or f"{run_id}-{repository_id}")
        run_key = hashlib.sha256(run_id.bytes).hexdigest()[:20]
        repository_key = hashlib.sha256(repository_id.bytes).hexdigest()[:20]
        workspace = self._root / "w" / run_key / repository_key
        mirror.parent.mkdir(parents=True, exist_ok=True)
        workspace.parent.mkdir(parents=True, exist_ok=True)

        if mirror.exists():
            await self._run("--git-dir", str(mirror), "fetch", "--prune", "origin")
        else:
            await self._run("clone", "--mirror", "--", repository_url, str(mirror))
        await self._run("--git-dir", str(mirror), "config", "core.longpaths", "true")

        base_sha = (
            await self._run(
                "--git-dir",
                str(mirror),
                "rev-parse",
                "--verify",
                f"{base_revision}^{{commit}}",
            )
        ).strip()
        if workspace.exists():
            metadata = workspace / ".repomesh" / "workspace.json"
            if not metadata.is_file():
                raise WorkspacePreparationError("existing workspace has no RepoMesh metadata")
            document = json.loads(metadata.read_text(encoding="utf-8"))
            if document.get("repositoryId") != str(repository_id) or document.get("runId") != str(
                run_id
            ):
                raise WorkspacePreparationError("workspace path hash collision detected")
            self._make_gitdir_portable(workspace, mirror)
            current = (await self._run("-C", str(workspace), "rev-parse", "HEAD")).strip()
            if current != base_sha:
                raise WorkspacePreparationError(
                    "existing workspace no longer matches its immutable base revision"
                )
            return PreparedGitWorkspace(workspace_id, workspace, base_sha, False)

        await self._run(
            "--git-dir", str(mirror), "worktree", "add", "--detach", str(workspace), base_sha
        )
        self._make_gitdir_portable(workspace, mirror)
        metadata = workspace / ".repomesh" / "workspace.json"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(
            json.dumps(
                {
                    "schemaVersion": "repomesh.workspace.v1",
                    "workspaceId": workspace_id,
                    "repositoryId": str(repository_id),
                    "runId": str(run_id),
                    "baseSha": base_sha,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return PreparedGitWorkspace(workspace_id, workspace, base_sha, True)

    def _mirror_path(self, repository_id: UUID) -> Path:
        return self._root / "repositories" / f"{repository_id}.git"

    @staticmethod
    def _make_gitdir_portable(workspace: Path, mirror: Path) -> None:
        """Make a mounted worktree usable from both container and host paths."""

        git_file = workspace / ".git"
        if not git_file.is_file():
            raise WorkspacePreparationError("prepared worktree has no .git pointer")
        pointer = git_file.read_text(encoding="utf-8").strip()
        prefix = "gitdir: "
        if not pointer.startswith(prefix):
            raise WorkspacePreparationError("prepared worktree has an invalid .git pointer")
        admin_name = Path(pointer[len(prefix) :].replace("\\", "/")).name
        portable_target = mirror / "worktrees" / admin_name
        relative = os.path.relpath(portable_target, workspace).replace("\\", "/")
        git_file.chmod(stat.S_IREAD | stat.S_IWRITE)
        if os.name == "nt":
            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(git_file))
            if attributes != -1:
                ctypes.windll.kernel32.SetFileAttributesW(str(git_file), attributes & ~0x3)
        git_file.write_text(f"gitdir: {relative}\n", encoding="utf-8")

    async def _run(self, *arguments: str) -> str:
        process = await asyncio.create_subprocess_exec(
            self._git_binary,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip()
            raise WorkspacePreparationError(message or "git command failed")
        return stdout.decode(errors="replace")
