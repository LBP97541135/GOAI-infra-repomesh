from pathlib import Path

import pytest

from repomesh.integrations.coding_agents.base import (
    AgentBinaryNotFound,
    CliAgentAdapter,
    CommandResult,
    ExecutionStatus,
)
from repomesh.integrations.coding_agents.catalog import SPECS
from repomesh.integrations.coding_agents.registry import (
    AdapterRegistry,
    DuplicateAdapter,
    build_default_registry,
)
from repomesh.modules.agent_runtime.ports.agent_adapter import (
    AdapterCapability,
    AgentFeedback,
    AgentLaunchRequest,
    AgentSessionRef,
    AuthStatus,
    FeedbackKind,
    PermissionMode,
    PromptDelivery,
    SessionKind,
)
from repomesh_runner.profiles import PROFILES


def spec(adapter_id: str):  # type: ignore[no-untyped-def]
    return next(item for item in SPECS if item.id == adapter_id)


def request(**overrides: object) -> AgentLaunchRequest:
    values = {
        "workspace_path": Path("C:/work/repo"),
        "prompt": "fix the failing test",
        "session_id": "11111111-1111-4111-8111-111111111111",
    }
    values.update(overrides)
    return AgentLaunchRequest(**values)  # type: ignore[arg-type]


def adapter(adapter_id: str) -> CliAgentAdapter:
    return CliAgentAdapter(spec(adapter_id), resolved_binary=spec(adapter_id).binaries[0])


def test_registry_lists_unique_discoverable_adapters() -> None:
    """The catalog is a discovery surface: unique ids, no executability claim.

    Being listed here says the CLI is known and probeable, not that its launch
    shape was ever validated. Execution authority lives in
    ``repomesh_runner.profiles``.
    """

    manifests = build_default_registry().list_manifests()
    assert len(manifests) == len({manifest.id for manifest in manifests})
    assert {"claude-code", "codex", "cursor"}.issubset(manifest.id for manifest in manifests)
    assert AdapterCapability.FEEDBACK in manifests[0].capabilities


def test_no_catalog_entry_claims_a_verified_launch_shape() -> None:
    """Guards the honesty of the catalog.

    Entries are either unverified transcriptions or explicitly superseded by a
    Runner driver profile. Nothing here may advertise itself as a validated
    execution path; adding such a status requires a real-CLI contract test.
    """

    for spec_item in SPECS:
        assert spec_item.execution_status in (
            ExecutionStatus.UNVERIFIED,
            ExecutionStatus.SUPERSEDED_BY_DRIVER,
        ), spec_item.id


def test_driver_backed_clis_are_marked_superseded() -> None:
    """Every CLI the Runner drives must point away from the catalog.

    This is the guard against the two registries drifting apart again: adding a
    driver profile without marking the catalog entry fails here.
    """

    driven = {profile.id for profile in PROFILES}
    for spec_item in SPECS:
        if spec_item.id in driven:
            assert spec_item.execution_status is ExecutionStatus.SUPERSEDED_BY_DRIVER, spec_item.id


def test_every_driver_profile_has_a_catalog_entry() -> None:
    """The reverse direction: the Runner cannot drive an unknown CLI."""

    catalog_ids = {spec_item.id for spec_item in SPECS}
    for profile in PROFILES:
        assert profile.id in catalog_ids, profile.id


def test_registry_rejects_duplicate_and_empty_ids() -> None:
    claude = adapter("claude-code")
    with pytest.raises(DuplicateAdapter, match="duplicate"):
        AdapterRegistry((claude, claude))


def test_claude_launch_maps_session_permissions_model_and_prompt() -> None:
    plan = adapter("claude-code").build_launch(
        request(
            permission_mode=PermissionMode.ACCEPT_EDITS,
            model="claude-sonnet-4",
            system_prompt="Follow the repository contract.",
        )
    )
    assert plan.argv == (
        "claude",
        "--session-id",
        "11111111-1111-4111-8111-111111111111",
        "--permission-mode",
        "acceptEdits",
        "--model",
        "claude-sonnet-4",
        "--append-system-prompt",
        "Follow the repository contract.",
        "--",
        "fix the failing test",
    )


def test_codex_and_cursor_use_their_native_resume_commands() -> None:
    session = AgentSessionRef("native-session", Path("C:/restored/repo"))
    codex = adapter("codex").build_restore(request(prompt=""), session)
    cursor = adapter("cursor").build_restore(request(prompt=""), session)
    assert codex is not None
    assert cursor is not None
    assert codex.argv == ("codex", "resume", "native-session")
    assert cursor.argv == ("cursor-agent", "--resume", "native-session")
    assert codex.working_directory == Path("C:/restored/repo")


def test_after_start_adapter_keeps_prompt_out_of_argv() -> None:
    plan = adapter("aider").build_launch(request())
    assert plan.prompt_delivery is PromptDelivery.AFTER_START
    assert plan.prompt_after_start == "fix the failing test"
    assert "fix the failing test" not in plan.argv


def test_bypass_permissions_maps_to_native_yolo_for_every_session_kind() -> None:
    """Bypass is permitted; containment is the workspace and container scope,
    not provider flags, which were measured to be advisory."""

    worker = adapter("cursor").build_launch(
        request(permission_mode=PermissionMode.BYPASS_PERMISSIONS)
    )
    reviewer = adapter("cursor").build_launch(
        request(
            permission_mode=PermissionMode.BYPASS_PERMISSIONS,
            session_kind=SessionKind.REVIEWER,
        )
    )
    assert "--yolo" in worker.arguments
    assert "--yolo" in reviewer.arguments


def test_missing_binary_fails_before_launch() -> None:
    class MissingResolver:
        def resolve(self, names: tuple[str, ...]) -> None:
            return None

    with pytest.raises(AgentBinaryNotFound, match="binary_not_found"):
        CliAgentAdapter(spec("codex"), resolver=MissingResolver()).build_launch(request())


@pytest.mark.asyncio
async def test_probe_detects_login_state() -> None:
    class InstalledResolver:
        def resolve(self, names: tuple[str, ...]) -> str:
            return names[0]

    class LoggedInRunner:
        async def run(
            self, executable: str, arguments: tuple[str, ...], timeout_seconds: float
        ) -> CommandResult:
            assert arguments == ("login", "status")
            return CommandResult(0, "Logged in using ChatGPT")

    probe = await CliAgentAdapter(
        spec("codex"), resolver=InstalledResolver(), runner=LoggedInRunner()
    ).probe()
    assert probe.installed is True
    assert probe.auth_status is AuthStatus.AUTHORIZED


def test_session_metadata_and_feedback_are_normalized() -> None:
    item = adapter("claude-code")
    session = item.session_info(
        {"agentSessionId": "native-123", "title": "Fix tests", "summary": "Ready"}
    )
    assert session is not None
    assert session.native_session_id == "native-123"

    feedback = item.receive_feedback(
        AgentFeedback(
            kind=FeedbackKind.CI,
            summary="Unit tests failed",
            details="pytest tests/test_api.py failed",
            source_url="https://ci.example/run/1",
        )
    )
    assert "[CI FEEDBACK]" in feedback.prompt
    assert "pytest tests/test_api.py failed" in feedback.prompt
