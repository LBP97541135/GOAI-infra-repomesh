import asyncio
import hashlib
import io
import json
from pathlib import Path

from repomesh.modules.task_orchestration.contracts import (
    PublishedTaskPackage,
    TaskAssignmentPublisher,
    TaskView,
)


class AgentTeamsTaskPublisher(TaskAssignmentPublisher):
    """Project a RepoMesh Worker task into AgentTeams Team-scoped storage."""

    def __init__(self, storage_root: Path) -> None:
        self._storage_root = storage_root

    async def publish(
        self,
        task: TaskView,
        *,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
    ) -> PublishedTaskPackage:
        return await asyncio.to_thread(
            self._publish,
            task,
            team_name,
            room_id,
            assignee_resource_name,
            idempotency_key,
        )

    def _publish(
        self,
        task: TaskView,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
    ) -> PublishedTaskPackage:
        task_path = f"teams/{team_name}/shared/tasks/{task.id}"
        target = self._storage_root / Path(task_path)
        spec = self._render_spec(task)
        meta = {
            "task_id": str(task.id),
            "project_id": str(task.project_id),
            "task_title": task.title,
            "assigned_to": assignee_resource_name,
            "room_id": room_id,
            "status": "assigned",
            "depends_on": [],
            "database_change": self._database_change(task),
            "repomesh": {
                "organization_id": str(task.organization_id),
                "repository_id": str(task.repository_id),
                "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
                "idempotency_key": idempotency_key,
            },
        }
        meta_text = json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        digest = self._digest(spec, meta_text)
        manifest = json.dumps(
            {
                "schema": "repomesh.agentteams-task.v1",
                "content_hash": digest,
                "files": ["meta.json", "spec.md"],
            },
            indent=2,
            sort_keys=True,
        ) + "\n"
        existing_manifest = target / "manifest.json"
        if existing_manifest.exists():
            existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
            if existing.get("content_hash") != digest:
                raise ValueError("published AgentTeams task conflicts with existing content")
            return PublishedTaskPackage(team_name, task_path, digest)

        target.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target / "spec.md", spec)
        self._atomic_write(target / "meta.json", meta_text)
        self._atomic_write(existing_manifest, manifest)
        if self._digest(
            (target / "spec.md").read_text(encoding="utf-8"),
            (target / "meta.json").read_text(encoding="utf-8"),
        ) != digest:
            raise OSError("AgentTeams task publication verification failed")
        return PublishedTaskPackage(team_name, task_path, digest)

    @staticmethod
    def _render_spec(task: TaskView) -> str:
        acceptance = "\n".join(f"- {item}" for item in task.acceptance)
        database = task.database_change
        database_section = (
            "\n## Database change requirements\n\n"
            f"- Declared: {str(database.declared).lower()}\n"
            f"- Required: {str(database.required).lower()}\n"
            f"- Change kinds: {', '.join(item.value for item in database.change_kinds) or 'none'}\n"
            f"- Affected tables: {', '.join(database.affected_tables) or 'none'}\n"
            f"- Migration required: {str(database.migration_required).lower()}\n"
            f"- Backfill required: {str(database.backfill_required).lower()}\n"
            f"- Required checks: {', '.join(database.required_checks) or 'none'}\n"
        )
        if database.required:
            database_section += (
                "\nWrite structured evidence to `.repomesh/database-change-report.json` "
                "with migrationFiles, backfillFiles, affectedTables, and checks "
                "[{name, exitCode}]. RepoMesh removes this control report before committing.\n"
            )
        return (
            f"# {task.title}\n\n"
            f"## Current task\n\n{task.instruction}\n\n"
            f"## Acceptance criteria\n\n{acceptance}\n{database_section}"
        )

    @staticmethod
    def _database_change(task: TaskView) -> dict[str, object]:
        requirement = task.database_change
        return {
            "declared": requirement.declared,
            "required": requirement.required,
            "change_kinds": [item.value for item in requirement.change_kinds],
            "affected_tables": list(requirement.affected_tables),
            "migration_required": requirement.migration_required,
            "backfill_required": requirement.backfill_required,
            "required_checks": list(requirement.required_checks),
        }

    @staticmethod
    def _digest(spec: str, meta: str) -> str:
        value = hashlib.sha256((spec + "\0" + meta).encode()).hexdigest()
        return f"sha256:{value}"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


class AgentTeamsObjectTaskPublisher(AgentTeamsTaskPublisher):
    """Publish the same verified task package through AgentTeams' S3 API."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = "agentteams-storage",
    ) -> None:
        from minio import Minio

        secure = endpoint.startswith("https://")
        host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._client = Minio(host, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket

    async def publish(
        self,
        task: TaskView,
        *,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
    ) -> PublishedTaskPackage:
        return await asyncio.to_thread(
            self._publish_object,
            task,
            team_name,
            room_id,
            assignee_resource_name,
            idempotency_key,
        )

    def _publish_object(
        self,
        task: TaskView,
        team_name: str,
        room_id: str,
        assignee_resource_name: str,
        idempotency_key: str,
    ) -> PublishedTaskPackage:
        task_path = f"teams/{team_name}/shared/tasks/{task.id}"
        spec = self._render_spec(task)
        meta = {
            "task_id": str(task.id),
            "project_id": str(task.project_id),
            "task_title": task.title,
            "assigned_to": assignee_resource_name,
            "room_id": room_id,
            "status": "assigned",
            "depends_on": [],
            "database_change": AgentTeamsTaskPublisher._database_change(task),
            "repomesh": {
                "organization_id": str(task.organization_id),
                "repository_id": str(task.repository_id),
                "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
                "idempotency_key": idempotency_key,
            },
        }
        meta_text = json.dumps(meta, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        digest = self._digest(spec, meta_text)
        manifest = json.dumps(
            {
                "schema": "repomesh.agentteams-task.v1",
                "content_hash": digest,
                "files": ["meta.json", "spec.md"],
            },
            indent=2,
            sort_keys=True,
        ) + "\n"
        for name, content, content_type in (
            ("spec.md", spec, "text/markdown"),
            ("meta.json", meta_text, "application/json"),
            ("manifest.json", manifest, "application/json"),
        ):
            payload = content.encode()
            self._client.put_object(
                self._bucket,
                f"{task_path}/{name}",
                io.BytesIO(payload),
                len(payload),
                content_type=content_type,
            )
        stored = self._client.get_object(self._bucket, f"{task_path}/manifest.json")
        try:
            stored_manifest = json.loads(stored.read().decode())
        finally:
            stored.close()
            stored.release_conn()
        if stored_manifest.get("content_hash") != digest:
            raise OSError("AgentTeams object task publication verification failed")
        return PublishedTaskPackage(team_name, task_path, digest)
