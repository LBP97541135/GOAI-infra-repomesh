import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from repomesh.modules.capability_management.contracts import AgentCapabilityBundle
from repomesh.modules.specification.contracts import CodingAgentPackage
from repomesh_runner.contracts import RunnerTask


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


@dataclass(frozen=True, slots=True)
class MaterializedRunnerContext:
    manifest_path: Path
    file_hashes: tuple[tuple[str, str], ...]


class RunnerContextMaterializer:
    """Mount immutable task context and reviewed Skill wrappers into a workspace."""

    def __init__(self, capability_root: Path) -> None:
        self._capability_root = capability_root.resolve()

    def materialize(
        self,
        task: RunnerTask,
        package: CodingAgentPackage,
        capabilities: AgentCapabilityBundle,
    ) -> MaterializedRunnerContext:
        if task.workspace is None:
            raise ValueError("context materialization requires a prepared workspace")
        if task.task_id != package.task_id or task.worker_agent_id != package.worker_agent_id:
            raise ValueError("task and coding package binding mismatch")
        if task.context_bundle.coding_package_hash != package.content_hash:
            raise ValueError("task coding package hash mismatch")

        workspace = Path(task.workspace.path).resolve()
        if not workspace.is_dir():
            raise ValueError("prepared workspace does not exist")

        files: list[tuple[str, str]] = []
        for rendered in package.context_files:
            self._write(workspace, rendered.mount_path, rendered.content, files)
        for skill in capabilities.skills:
            if skill.local_path is None:
                raise ValueError(f"skill {skill.id} has no reviewed local wrapper")
            source = (self._capability_root / skill.local_path).resolve()
            if not source.is_relative_to(self._capability_root) or not source.is_file():
                raise ValueError(f"skill wrapper not found: {skill.id}")
            content = source.read_text(encoding="utf-8")
            actual_hash = _sha256_bytes(content.encode("utf-8"))
            if skill.content_hash is not None and actual_hash != skill.content_hash:
                raise ValueError(f"skill wrapper content hash changed: {skill.id}")
            self._write(
                workspace,
                f".repomesh/skills/{skill.id}/SKILL.md",
                content,
                files,
            )

        manifest = {
            "schemaVersion": "repomesh.context-manifest.v1",
            "bundleId": str(task.context_bundle.bundle_id),
            "bundleVersion": task.context_bundle.version,
            "contentHash": task.context_bundle.content_hash,
            "codingPackageHash": package.content_hash,
            "skills": [
                {
                    "id": skill.id,
                    "version": skill.version,
                    "releaseId": str(skill.release_id) if skill.release_id else None,
                    "assignmentId": str(skill.assignment_id) if skill.assignment_id else None,
                    "contentHash": skill.content_hash,
                }
                for skill in capabilities.skills
            ],
            "files": [
                {"path": path, "contentHash": content_hash} for path, content_hash in sorted(files)
            ],
        }
        manifest_path = workspace / ".repomesh" / "context" / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_content = json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        self._write_immutable(manifest_path, manifest_content.encode("utf-8"))
        self._make_read_only(manifest_path)
        return MaterializedRunnerContext(manifest_path, tuple(sorted(files)))

    @staticmethod
    def _write(
        workspace: Path,
        relative_path: str,
        content: str,
        files: list[tuple[str, str]],
    ) -> None:
        normalized = PurePosixPath(relative_path.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError(f"unsafe context mount path: {relative_path}")
        target = (workspace / Path(*normalized.parts)).resolve()
        if not target.is_relative_to(workspace):
            raise ValueError(f"context mount escapes workspace: {relative_path}")
        encoded = content.encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        RunnerContextMaterializer._write_immutable(target, encoded)
        RunnerContextMaterializer._make_read_only(target)
        files.append((normalized.as_posix(), _sha256_bytes(encoded)))

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError(
                    f"immutable context file already exists with different content: {path}"
                )
            return
        path.write_bytes(content)

    @staticmethod
    def _make_read_only(path: Path) -> None:
        path.chmod(0o444)
