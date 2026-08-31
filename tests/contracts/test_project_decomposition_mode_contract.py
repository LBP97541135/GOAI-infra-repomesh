"""Wave-0 freeze of the team decomposition-mode contract.

Shared by PR 5.5B (producer: project topology persists the mode) and PR 7
(consumer: task_orchestration's batch assignment forks on it). These tests pin
the vocabulary and the default so neither side can drift before either lands:
the enum is exactly ``server``/``leader``, a team view that says nothing about
decomposition is a ``server`` team, and the narrow reader protocol keeps its
one-question shape.
"""

import inspect
from uuid import UUID

from repomesh.modules.project.contracts import (
    ProjectTeamRuntimeStatus,
    RepositoryTeamView,
    TeamDecompositionMode,
    TeamDecompositionModeReader,
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


def test_the_mode_vocabulary_is_exactly_server_and_leader() -> None:
    assert {mode.value for mode in TeamDecompositionMode} == {"server", "leader"}


def test_a_team_that_says_nothing_is_a_server_team() -> None:
    """Every existing construction site keeps compiling and keeps today's
    behavior: the field's default carries the D-2 adjudication that ``leader``
    is opt-in through the formal adoption path, never the resting state."""
    assert make_team_view().decomposition_mode is TeamDecompositionMode.SERVER


def test_leader_mode_is_representable_and_survives_the_view() -> None:
    team = make_team_view(decomposition_mode=TeamDecompositionMode.LEADER)
    assert team.decomposition_mode is TeamDecompositionMode.LEADER


def test_the_reader_protocol_asks_one_question_with_two_ids() -> None:
    signature = inspect.signature(TeamDecompositionModeReader.decomposition_mode)
    assert list(signature.parameters) == ["self", "project_id", "repository_id"]


def test_a_memory_reader_satisfies_the_protocol() -> None:
    """The contract's promise to PR 7: core behavior is developable against a
    memory fake before PR 5.5B's persistence exists."""

    class MemoryReader:
        def __init__(self, modes: dict[tuple[UUID, UUID], TeamDecompositionMode]) -> None:
            self._modes = modes

        async def decomposition_mode(
            self, project_id: UUID, repository_id: UUID
        ) -> TeamDecompositionMode:
            return self._modes.get((project_id, repository_id), TeamDecompositionMode.SERVER)

    reader: TeamDecompositionModeReader = MemoryReader({})
    assert reader is not None
