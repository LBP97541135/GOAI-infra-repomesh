"""Wave-1 freeze of the team construction-mode contract (hosted-native spec M7).

Shared by the project module (producer: the team row persists the mode) and
three consumers that land after it — the delivery fork in task_orchestration
(``_deliver_assignment``), the execution-plane readiness gate (M8) and the
shared-directory observer (M2). These tests pin the vocabulary, the default and
the derivation so none of them can drift before the others exist: the enum is
exactly ``hosted_native``/``local_cli``, a team view that says nothing is a
hosted-native team, the derived projection facts are the ones D-17 froze, and
the reader protocol keeps its one-question shape.
"""

import inspect
from uuid import UUID

from repomesh.modules.agent_runtime.contracts import WorkerRuntime
from repomesh.modules.project.contracts import (
    ConstructionMode,
    DerivedRuntime,
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
    TeamConstructionModeReader,
    TeamDecompositionMode,
    derive_runtime,
)


def make_team_view(**overrides: object) -> RepositoryTeamView:
    values: dict[str, object] = {
        "id": UUID("00000000-0000-0000-0000-000000000031"),
        "project_id": UUID("00000000-0000-0000-0000-000000000011"),
        "repository_id": UUID("00000000-0000-0000-0000-000000000021"),
        "leader_agent_id": UUID("00000000-0000-0000-0000-000000000003"),
        "worker_agent_ids": (UUID("00000000-0000-0000-0000-000000000002"),),
        "agentteams_team_name": "pricing-repo-team",
        "runtime_status": ProjectTeamRuntimeStatus.READY,
        "room_id": "!team-pricing:matrix.example.org",
        "leader_room_id": "!dm-pricing-leader:matrix.example.org",
    }
    values.update(overrides)
    return RepositoryTeamView(**values)  # type: ignore[arg-type]


def test_the_mode_vocabulary_is_exactly_hosted_native_and_local_cli() -> None:
    assert {mode.value for mode in ConstructionMode} == {"hosted_native", "local_cli"}


def test_a_team_that_says_nothing_is_a_hosted_native_team() -> None:
    """The product default (D-1): every row written before the column existed,
    and every construction site that never names the field, means the copaw
    workers build in their own containers. ``local_cli`` is opt-in."""
    assert make_team_view().construction_mode is ConstructionMode.HOSTED_NATIVE


def test_local_cli_is_representable_and_survives_the_view() -> None:
    team = make_team_view(construction_mode=ConstructionMode.LOCAL_CLI)
    assert team.construction_mode is ConstructionMode.LOCAL_CLI


def test_hosted_native_derives_a_containerized_copaw_worker() -> None:
    """D-17, first half: the hosted worker is a controller-managed copaw body."""
    assert derive_runtime(ConstructionMode.HOSTED_NATIVE) == DerivedRuntime(
        container_managed=True,
        worker_runtime=WorkerRuntime.COPAW,
        decomposition_default=TeamDecompositionMode.SERVER,
    )


def test_local_cli_derives_an_uncontainerized_copaw_worker() -> None:
    """D-17, second half: same runtime, no container — the body is a Bridge.

    ``container_managed`` is the one field the two modes disagree on; the
    controller runtime does not change with the mode, so a Bridge-served and a
    hosted repository never conflict on ``runtime`` when the same principal is
    read back by the projection.
    """
    assert derive_runtime(ConstructionMode.LOCAL_CLI) == DerivedRuntime(
        container_managed=False,
        worker_runtime=WorkerRuntime.COPAW,
        decomposition_default=TeamDecompositionMode.SERVER,
    )


def test_every_mode_derives_something() -> None:
    """Total over the enum: a third mode is a code change, never a KeyError
    at first dispatch."""
    for mode in ConstructionMode:
        assert isinstance(derive_runtime(mode), DerivedRuntime)


def test_the_reader_protocol_asks_one_question_with_two_ids() -> None:
    signature = inspect.signature(TeamConstructionModeReader.construction_mode)
    assert list(signature.parameters) == ["self", "project_id", "repository_id"]


def test_a_memory_reader_satisfies_the_protocol() -> None:
    """The contract's promise to the consumers: developable against a memory
    fake before the persisted reader is wired."""

    class MemoryReader:
        def __init__(self, modes: dict[tuple[UUID, UUID], ConstructionMode]) -> None:
            self._modes = modes

        async def construction_mode(
            self, project_id: UUID, repository_id: UUID
        ) -> ConstructionMode:
            return self._modes.get((project_id, repository_id), ConstructionMode.HOSTED_NATIVE)

    reader: TeamConstructionModeReader = MemoryReader({})
    assert reader is not None
