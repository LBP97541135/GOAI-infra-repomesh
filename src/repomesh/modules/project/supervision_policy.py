"""The supervision-policy rules, in the one place every writer can reach.

A project's supervision policy is three fields: how much of the work runs
unattended, where it must stop for a person, and who those people are. Until
now the only thing that could hold them was a finished
:class:`~repomesh.modules.project.domain.ProjectAgentTopology`, so its
``__post_init__`` checked them alongside rules about repository teams.

A policy is now also *decided before a topology exists* — written as a draft by
the admin face, read back when the topology is materialized — and that writer
has no repository teams to offer. Re-typing the four-mode invariants over there
would create a second implementation, and a second implementation drifts. The
symptom is specific and nasty: a draft that saves clean and then refuses to
materialize, two contradictory verdicts on the same three fields, minutes
apart, with nothing on screen to say which one is the rule.

The seam is therefore cut by what a rule *needs*, not by where it used to
live. Every rule decidable from the three policy fields alone is here; every
rule that needs the repository teams stays on the topology. There is
deliberately no ``repository_teams`` parameter — its absence is the whole
reason a draft can be validated at all.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from repomesh.modules.project.contracts import ProjectCheckpoint, ProjectExecutionMode
from repomesh.modules.project.errors import ProjectTopologyViolation


class SupervisionGrant(Protocol):
    """The two fields a policy rule reads off a human grant.

    Structural instead of importing ``HumanProjectGrant`` so this module stays
    below ``domain``. It is also honest about the dependency: the only policy
    rule that looks at grants asks who the person is and what they were scoped
    to, and a draft's grants can answer that before any topology exists.
    """

    human_principal_id: UUID
    repository_id: UUID | None


def assert_supervision_policy(
    *,
    execution_mode: ProjectExecutionMode,
    required_checkpoints: frozenset[ProjectCheckpoint],
    human_grants: Sequence[SupervisionGrant],
) -> None:
    """Raise ``ProjectTopologyViolation`` for the first rule broken, else return.

    Rule order and message wording are the ones
    ``ProjectAgentTopology.__post_init__`` used before the move, and they have
    to stay that way: each sentence now reaches a person twice — once when the
    draft is saved and once if materialization refuses — and the two occasions
    must produce the same sentence about the same data.
    """

    if execution_mode is ProjectExecutionMode.AUTO and required_checkpoints:
        raise ProjectTopologyViolation("automatic projects cannot require human checkpoints")
    if execution_mode is not ProjectExecutionMode.AUTO and not human_grants:
        raise ProjectTopologyViolation("human-controlled projects require a human grant")
    if execution_mode is not ProjectExecutionMode.AUTO and not required_checkpoints:
        raise ProjectTopologyViolation("human-controlled projects require checkpoints")
    if (
        execution_mode is ProjectExecutionMode.MANUAL_CONTROLLED
        and required_checkpoints != frozenset(ProjectCheckpoint)
    ):
        raise ProjectTopologyViolation(
            "manual-controlled projects require every human checkpoint"
        )
    grant_scopes = [(grant.human_principal_id, grant.repository_id) for grant in human_grants]
    if len(set(grant_scopes)) != len(grant_scopes):
        raise ProjectTopologyViolation("duplicate human grant scope")
