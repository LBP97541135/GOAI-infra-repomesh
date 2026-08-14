"""The five supervision-policy rules, and the one sentence each of them says.

``assert_supervision_policy`` exists so that a policy is judged by one
implementation whoever is asking — the draft an admin saves and the topology
materialization builds out of it. What makes that worth having is not that both
paths *reject* the same data, it is that both produce the *same sentence* about
it; a draft that saves clean and then refuses to materialize with different
wording is the failure this module was cut out to prevent.

So the assertions here are on message text, not on exception type. A test that
only asserted ``pytest.raises(ProjectTopologyViolation)`` would still pass after
someone copied the rules back into ``domain.py`` and reworded one of them, which
is exactly the regression there is nothing else to catch.
"""

from uuid import UUID, uuid4

import pytest

from repomesh.modules.project.contracts import (
    CodeAccessLevel,
    HumanControlAction,
    HumanProjectRole,
    ProjectCheckpoint,
    ProjectExecutionMode,
)
from repomesh.modules.project.domain import (
    HumanProjectGrant,
    ProjectAgentTopology,
    RepositoryTeam,
)
from repomesh.modules.project.errors import ProjectTopologyViolation
from repomesh.modules.project.supervision_policy import assert_supervision_policy

EVERY_CHECKPOINT = frozenset(ProjectCheckpoint)


def _grant(
    *,
    human_principal_id: UUID | None = None,
    repository_id: UUID | None = None,
) -> HumanProjectGrant:
    """A grant that satisfies its own four rules, so only policy rules can fire."""

    return HumanProjectGrant(
        human_principal_id=human_principal_id or uuid4(),
        role=(
            HumanProjectRole.REPOSITORY_SUPERVISOR
            if repository_id is not None
            else HumanProjectRole.PROJECT_SUPERVISOR
        ),
        code_access=CodeAccessLevel.READ,
        control_actions=frozenset({HumanControlAction.APPROVE_CHECKPOINT}),
        repository_id=repository_id,
    )


# ---------------------------------------------------------------------------
# One counterexample and one accepted example per rule
# ---------------------------------------------------------------------------


def test_automatic_project_refuses_any_checkpoint() -> None:
    with pytest.raises(ProjectTopologyViolation) as refused:
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.AUTO,
            required_checkpoints=frozenset({ProjectCheckpoint.DELIVERY}),
            human_grants=(),
        )
    assert str(refused.value) == "automatic projects cannot require human checkpoints"

    assert (
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.AUTO,
            required_checkpoints=frozenset(),
            human_grants=(),
        )
        is None
    )


def test_human_controlled_project_requires_a_human_grant() -> None:
    with pytest.raises(ProjectTopologyViolation) as refused:
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=frozenset({ProjectCheckpoint.DELIVERY}),
            human_grants=(),
        )
    assert str(refused.value) == "human-controlled projects require a human grant"

    assert (
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=frozenset({ProjectCheckpoint.DELIVERY}),
            human_grants=(_grant(),),
        )
        is None
    )


def test_human_controlled_project_requires_checkpoints() -> None:
    with pytest.raises(ProjectTopologyViolation) as refused:
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=frozenset(),
            human_grants=(_grant(),),
        )
    assert str(refused.value) == "human-controlled projects require checkpoints"

    assert (
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=frozenset(
                {ProjectCheckpoint.REPOSITORY_SCOPE, ProjectCheckpoint.DELIVERY}
            ),
            human_grants=(_grant(),),
        )
        is None
    )


def test_manual_controlled_project_requires_every_checkpoint() -> None:
    """A near-miss, not an empty set: the missing-checkpoints rule is a different one.

    Five of six passes the two rules above and still has to be refused, which is
    the only way to tell "manual means all of them" from "non-auto means at
    least one".
    """

    five_of_six = EVERY_CHECKPOINT - {ProjectCheckpoint.SPECIFICATION}
    with pytest.raises(ProjectTopologyViolation) as refused:
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.MANUAL_CONTROLLED,
            required_checkpoints=five_of_six,
            human_grants=(_grant(),),
        )
    assert str(refused.value) == "manual-controlled projects require every human checkpoint"

    assert (
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.MANUAL_CONTROLLED,
            required_checkpoints=EVERY_CHECKPOINT,
            human_grants=(_grant(),),
        )
        is None
    )


def test_duplicate_human_and_repository_pair_is_refused() -> None:
    """The pair is the key, not the person: one person may hold two scopes.

    The accepted half is the load-bearing one — a rule keyed on the principal
    alone would pass the refusal above and quietly forbid the ordinary case of
    one supervisor scoped to a repository *and* to the project as a whole.
    """

    human_principal_id = uuid4()
    with pytest.raises(ProjectTopologyViolation) as refused:
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=frozenset({ProjectCheckpoint.DELIVERY}),
            human_grants=(
                _grant(human_principal_id=human_principal_id),
                _grant(human_principal_id=human_principal_id),
            ),
        )
    assert str(refused.value) == "duplicate human grant scope"

    assert (
        assert_supervision_policy(
            execution_mode=ProjectExecutionMode.SUPERVISED,
            required_checkpoints=frozenset({ProjectCheckpoint.DELIVERY}),
            human_grants=(
                _grant(human_principal_id=human_principal_id),
                _grant(human_principal_id=human_principal_id, repository_id=uuid4()),
            ),
        )
        is None
    )


# ---------------------------------------------------------------------------
# The design itself: one implementation, reachable two ways
# ---------------------------------------------------------------------------


def _policy_violations() -> tuple[tuple[str, dict], ...]:
    human_principal_id = uuid4()
    return (
        (
            "auto-with-checkpoint",
            {
                "execution_mode": ProjectExecutionMode.AUTO,
                "required_checkpoints": frozenset({ProjectCheckpoint.DELIVERY}),
                "human_grants": (),
            },
        ),
        (
            "supervised-without-grant",
            {
                "execution_mode": ProjectExecutionMode.SUPERVISED,
                "required_checkpoints": frozenset({ProjectCheckpoint.DELIVERY}),
                "human_grants": (),
            },
        ),
        (
            "supervised-without-checkpoint",
            {
                "execution_mode": ProjectExecutionMode.SUPERVISED,
                "required_checkpoints": frozenset(),
                "human_grants": (_grant(),),
            },
        ),
        (
            "manual-missing-one-checkpoint",
            {
                "execution_mode": ProjectExecutionMode.MANUAL_CONTROLLED,
                "required_checkpoints": EVERY_CHECKPOINT - {ProjectCheckpoint.SPECIFICATION},
                "human_grants": (_grant(),),
            },
        ),
        (
            "duplicate-grant-scope",
            {
                "execution_mode": ProjectExecutionMode.SUPERVISED,
                "required_checkpoints": frozenset({ProjectCheckpoint.DELIVERY}),
                "human_grants": (
                    _grant(human_principal_id=human_principal_id),
                    _grant(human_principal_id=human_principal_id),
                ),
            },
        ),
    )


@pytest.mark.parametrize(
    "policy",
    [policy for _, policy in _policy_violations()],
    ids=[label for label, _ in _policy_violations()],
)
def test_topology_says_exactly_what_the_shared_rule_says(policy: dict) -> None:
    """Same three fields, two entry points, one sentence — verbatim.

    This is the test that fails the day the rules are copied back into
    ``ProjectAgentTopology.__post_init__``. A copy can stay behaviourally
    correct for a long time and still drift a word, and a word is enough: the
    draft endpoint shows one sentence to the admin who is choosing the policy,
    and materialization shows the other to whoever presses the button minutes
    later.
    """

    project_id = uuid4()
    team = RepositoryTeam(
        project_id=project_id,
        repository_id=uuid4(),
        leader_agent_id=uuid4(),
        worker_agent_ids=(uuid4(),),
    )

    with pytest.raises(ProjectTopologyViolation) as direct:
        assert_supervision_policy(**policy)
    with pytest.raises(ProjectTopologyViolation) as through_topology:
        ProjectAgentTopology(
            organization_id=uuid4(),
            project_id=project_id,
            organization_leader_id=uuid4(),
            repository_teams=(team,),
            **policy,
        )

    assert str(through_topology.value) == str(direct.value)


def test_topology_keeps_the_one_rule_the_shared_module_cannot_run() -> None:
    """The seam is "does it need the teams", and this is what stayed behind.

    A grant scoped to a repository the plan does not contain is legal in a
    draft — the plan may not exist yet — and illegal in a topology. If this ever
    starts passing, the rule followed the others out of ``domain`` and drafts
    began refusing policies they have no way to judge.
    """

    project_id = uuid4()
    unknown_repository_id = uuid4()
    policy = {
        "execution_mode": ProjectExecutionMode.SUPERVISED,
        "required_checkpoints": frozenset({ProjectCheckpoint.DELIVERY}),
        "human_grants": (_grant(repository_id=unknown_repository_id),),
    }

    assert assert_supervision_policy(**policy) is None

    with pytest.raises(ProjectTopologyViolation) as refused:
        ProjectAgentTopology(
            organization_id=uuid4(),
            project_id=project_id,
            organization_leader_id=uuid4(),
            repository_teams=(
                RepositoryTeam(
                    project_id=project_id,
                    repository_id=uuid4(),
                    leader_agent_id=uuid4(),
                    worker_agent_ids=(uuid4(),),
                ),
            ),
            **policy,
        )
    assert str(refused.value) == "human grant references an unknown project repository"
