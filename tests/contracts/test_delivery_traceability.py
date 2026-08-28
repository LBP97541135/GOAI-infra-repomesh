"""Ruling D-9: one traceability chain, one generator, two delivery paths.

The acceptance evidence asks a reviewer to walk from a delivered pull request
back to the Issue, ChangeSet, plan, Task, Run, Worker and candidate commit. That
only works if the labels mean the same thing wherever the pull request came
from, so what is pinned here is the *shared* rendering rather than each path's
prose.
"""

from datetime import UTC, datetime
from uuid import uuid4

from repomesh.integrations.scm.delivery import ChangeSetSCMCoordinator
from repomesh.integrations.scm.plan_delivery import PlanDeliveryFinalizer, _WorkerProvenance
from repomesh.modules.delivery.contracts import (
    ChangeSetStatus,
    ChangeSetView,
    DeliveryTraceability,
    RepositoryDeliveryStatus,
    RepositoryDeliveryView,
    render_delivery_pull_request_body,
)
from repomesh.modules.task_orchestration.contracts import (
    ExecutionPlanStatus,
    ExecutionPlanView,
    PlannedRepositoryTaskView,
)


def _bullets(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith("- ")]


def _labels(body: str) -> list[str]:
    return [line.split(":", 1)[0] for line in _bullets(body)]


def test_a_complete_chain_renders_every_id_as_its_own_line() -> None:
    traceability = DeliveryTraceability(
        issue_id=uuid4(),
        change_set_id=uuid4(),
        repository_id=uuid4(),
        task_id=uuid4(),
        branch_name="repomesh/a762abba/9dfa78f2",
        commit_sha="a" * 40,
        plan_id=uuid4(),
        run_id=uuid4(),
        worker_agent_id=uuid4(),
    )

    body = render_delivery_pull_request_body(
        traceability,
        headline="Automated RepoMesh delivery.",
        context=("execution order: batch 2",),
        notes=("Verified.",),
    )

    assert body.splitlines()[0] == "Automated RepoMesh delivery."
    assert _bullets(body) == [
        f"- issue: `{traceability.issue_id}`",
        f"- change_set: `{traceability.change_set_id}`",
        f"- plan: `{traceability.plan_id}`",
        f"- repository: `{traceability.repository_id}`",
        f"- task: `{traceability.task_id}`",
        f"- run: `{traceability.run_id}`",
        f"- worker_agent: `{traceability.worker_agent_id}`",
        "- branch: `repomesh/a762abba/9dfa78f2`",
        f"- commit: `{'a' * 40}`",
        "- execution order: batch 2",
    ]
    assert body.endswith("\nVerified.")


def test_an_absent_optional_id_leaves_no_line_behind() -> None:
    """No placeholder. A rendered ``- run: unknown`` would read as a recorded fact."""

    body = render_delivery_pull_request_body(
        DeliveryTraceability(
            issue_id=uuid4(),
            change_set_id=uuid4(),
            repository_id=uuid4(),
            task_id=uuid4(),
            branch_name="repomesh/a762abba/9dfa78f2",
            commit_sha="a" * 40,
        ),
        headline="Automated RepoMesh delivery.",
    )

    assert _labels(body) == [
        "- issue",
        "- change_set",
        "- repository",
        "- task",
        "- branch",
        "- commit",
    ]
    assert "None" not in body
    assert "unknown" not in body


def test_both_delivery_paths_render_the_same_traceability_lines() -> None:
    """The finalizer knows more; every fact both know is rendered identically."""

    issue_id = uuid4()
    repository_id = uuid4()
    task_id = uuid4()
    worker_agent_id = uuid4()
    run_id = uuid4()
    candidate = RepositoryDeliveryView(
        repository_id=repository_id,
        task_id=task_id,
        commit_sha="a" * 40,
        base_sha="b" * 40,
        branch_name="repomesh/a762abba/9dfa78f2",
        depends_on=(),
        merge_order=0,
        status=RepositoryDeliveryStatus.PENDING,
        pull_request_number=None,
        pull_request_url=None,
        ci_check_run_id=None,
        ci_summary=None,
        merge_sha=None,
        required_checks=(),
        ci_checks=(),
        required_approvals=0,
        reviews=(),
    )
    now = datetime.now(UTC)
    change_set = ChangeSetView(
        id=uuid4(),
        organization_id=uuid4(),
        project_id=issue_id,
        created_by_agent_id=uuid4(),
        title="RepoMesh delivery",
        validation_snapshot_id=None,
        status=ChangeSetStatus.DRAFT,
        version=1,
        merge_cursor=0,
        repositories=(candidate,),
        recovery_plans=(),
        governance_decisions=(),
        candidate_revisions=(),
        created_at=now,
        updated_at=now,
    )
    plan = ExecutionPlanView(
        id=uuid4(),
        organization_id=change_set.organization_id,
        project_id=issue_id,
        created_by_agent_id=change_set.created_by_agent_id,
        status=ExecutionPlanStatus.IN_PROGRESS,
        current_batch_index=0,
        batches=(
            (PlannedRepositoryTaskView(repository_id, "api", "implement", (), uuid4()),),
        ),
    )

    primary = PlanDeliveryFinalizer._pull_request_body(
        plan,
        change_set.id,
        candidate,
        {repository_id: _WorkerProvenance(run_id=run_id, worker_agent_id=worker_agent_id)},
        batch_index=0,
    )
    reconciled = ChangeSetSCMCoordinator._reconciled_pull_request_body(change_set, candidate)

    assert _labels(reconciled) == [
        "- issue",
        "- change_set",
        "- repository",
        "- task",
        "- branch",
        "- commit",
    ]
    assert _labels(primary) == [
        "- issue",
        "- change_set",
        "- plan",
        "- repository",
        "- task",
        "- run",
        "- worker_agent",
        "- branch",
        "- commit",
        "- execution order",
    ]
    # Same ids, byte-identical lines: the two paths cannot drift apart while
    # they share the generator, and this fails the moment one stops.
    assert set(_bullets(reconciled)) < set(_bullets(primary))
    assert "completed by reconciliation" in reconciled
    assert "plan, run and worker ids" in reconciled
