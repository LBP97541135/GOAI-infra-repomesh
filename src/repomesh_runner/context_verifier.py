import hashlib
import json
from pathlib import Path
from typing import Any

from repomesh_runner.contracts import RunnerTask


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class WorkspaceContextVerifier:
    """Verify the materialized product context before a vendor CLI is launched."""

    def __call__(self, task: RunnerTask) -> str | None:
        if task.workspace is None:
            return "prepared_workspace_required"
        workspace = Path(task.workspace.path).resolve()
        manifest_path = workspace / ".repomesh" / "context" / "manifest.json"
        try:
            manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "context_manifest_missing"
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "context_manifest_invalid"

        expected = task.context_bundle
        checks = (
            (manifest.get("schemaVersion"), "repomesh.context-manifest.v1", "schema"),
            (manifest.get("bundleId"), str(expected.bundle_id), "bundle_id"),
            (manifest.get("bundleVersion"), expected.version, "bundle_version"),
            (manifest.get("contentHash"), expected.content_hash, "content_hash"),
            (
                manifest.get("codingPackageHash"),
                expected.coding_package_hash,
                "coding_package_hash",
            ),
        )
        for actual, wanted, label in checks:
            if actual != wanted:
                return f"{label}_mismatch"

        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            return "context_files_missing"
        for entry in files:
            if not isinstance(entry, dict):
                return "context_file_entry_invalid"
            relative = entry.get("path")
            content_hash = entry.get("contentHash")
            if not isinstance(relative, str) or not isinstance(content_hash, str):
                return "context_file_entry_invalid"
            target = (workspace / relative).resolve()
            if not target.is_relative_to(workspace):
                return "context_file_escape"
            if not target.is_file():
                return f"context_file_missing:{relative}"
            if _sha256_file(target) != content_hash:
                return f"context_file_hash_mismatch:{relative}"
        return None
