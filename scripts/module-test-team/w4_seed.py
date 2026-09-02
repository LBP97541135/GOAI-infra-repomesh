"""Seed the W4 acceptance stack for the module-test-team line (fresh database).

What it writes, and nothing else: the local admin (only when the account table
is empty), two catalog rows, and five agent principals — the Manager, the
business team reused from the M7 walk, and the cross-repo test team. No
topology: materialize builds that through the product path, which is the
point of the walk (spec S-1). Idempotent: rows already on file are verified
and left alone.

Run with the stack's environment (REPOMESH_DATABASE_URL etc.) exported::

    .venv/Scripts/python.exe scripts/module-test-team/w4_seed.py [--admin-password ...]

Ids are pinned because every later step keys on them (roster, enrollment,
codex-home derivation); see output/bridge-team/w4-live/members.w4.json.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from repomesh.bootstrap.app import build_default_container
from repomesh.modules.agent_directory.contracts import AgentPrincipalStatus, AgentRole
from repomesh.modules.agent_directory.domain import AgentPrincipal
from repomesh.modules.repository_intelligence.domain import RepositoryProfile
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope
from repomesh.shared.idempotency import command_fingerprint

ORG = UUID("11111111-0000-4000-8000-000000000001")
MANAGER = UUID("22222222-0000-4000-8000-000000000002")
BUSINESS_REPO = UUID("42cf099f-fadc-4222-95ab-bbd4770f7fdc")
BUSINESS_LEADER = UUID("33333333-0000-4000-8000-000000000003")
BUSINESS_WORKER = UUID("4d1e6f00-0000-4000-8000-000000000004")
TEST_REPO = UUID("55555555-0000-4000-8000-000000000005")
TEST_LEADER = UUID("66666666-0000-4000-8000-000000000006")
TEST_WORKER = UUID("4d1e6f00-0000-4000-8000-0000000000e1")

RECIPE = "python environments/e2e-fixture-joint/run_round.py"

REPOSITORIES = (
    RepositoryProfile(
        id=BUSINESS_REPO,
        name="pricing-fixture",
        url="D:/Project4work/.repomesh-v1-live/fixture-pricing",
        description="M7 business fixture (unchanged): the plan's business repository",
        test_commands=("python scripts/run_tests.py",),
        test_paths=("tests/**",),
    ),
    RepositoryProfile(
        id=TEST_REPO,
        name="repomesh-test-assets",
        url="https://github.com/catbobyman/repomesh-test-assets",
        description="测试资产仓：场景库、环境定义与联调证据的唯一归宿",
        topics=("integration-test",),
        # The single test command every round shares: the combination comes
        # from the mounted task context, never from here.
        test_commands=(RECIPE,),
        # evidence/** is what a round writes; it must sit inside the Runner's
        # allowed paths or the evidence commit is refused as a violation.
        test_paths=("evidence/**",),
        # Not profiled here on purpose: flipping the switch is AC-D1, done in the UI.
    ),
)


def _principal(
    agent_id: UUID,
    role: AgentRole,
    *,
    resource: str,
    leader: UUID | None,
    repository: UUID | None,
    paths: tuple[str, ...],
) -> AgentPrincipal:
    if role is AgentRole.ORGANIZATION_LEADER:
        singleton = f"organization:{ORG}:leader"
    elif role is AgentRole.REPOSITORY_LEADER:
        singleton = f"repository:{repository}:leader"
    else:
        singleton = None
    return AgentPrincipal(
        id=agent_id,
        organization_id=ORG,
        role=role,
        leader_agent_id=leader,
        singleton_key=singleton,
        repository_id=repository,
        responsibility_paths=paths,
        agentteams_resource_name=resource,
        status=AgentPrincipalStatus.ACTIVE,
    )


PRINCIPALS = (
    _principal(MANAGER, AgentRole.ORGANIZATION_LEADER, resource="repomesh-preflight-manager",
               leader=None, repository=None, paths=()),
    _principal(BUSINESS_LEADER, AgentRole.REPOSITORY_LEADER, resource="repomesh-preflight-leader",
               leader=MANAGER, repository=BUSINESS_REPO, paths=("**",)),
    _principal(BUSINESS_WORKER, AgentRole.WORKER, resource="repomesh-preflight-probe",
               leader=BUSINESS_LEADER, repository=BUSINESS_REPO, paths=("src/**", "tests/**")),
    _principal(TEST_LEADER, AgentRole.REPOSITORY_LEADER, resource="repomesh-test-leader",
               leader=MANAGER, repository=TEST_REPO, paths=("**",)),
    _principal(TEST_WORKER, AgentRole.WORKER, resource="repomesh-test-worker",
               leader=TEST_LEADER, repository=TEST_REPO, paths=("evidence/**",)),
)


def _registered(principal: AgentPrincipal) -> EventEnvelope:
    """``CreateAgent``'s envelope, field for field (as scripts/bridge-e1 does)."""

    return EventEnvelope(
        event_type="AgentPrincipalRegistered",
        actor_type=ActorType.SERVICE,
        actor_id="repomesh-module-test-team-w4",
        aggregate_type="AgentPrincipal",
        aggregate_id=principal.id,
        aggregate_version=1,
        correlation_id=new_id(),
        payload={
            "role": principal.role.value,
            "leaderAgentId": str(principal.leader_agent_id) if principal.leader_agent_id else None,
            "repositoryId": str(principal.repository_id) if principal.repository_id else None,
            "agentteamsResourceName": principal.agentteams_resource_name,
        },
    )


async def seed(admin_password: str) -> None:
    container = build_default_container()
    try:
        accounts = container.local_account_service()
        try:
            await accounts.bootstrap_admin("w4admin", admin_password, "W4 Admin")
            print("admin     w4admin bootstrapped")
        except Exception as error:  # noqa: BLE001 - LocalAccountConflict means already done
            print(f"admin     skipped ({type(error).__name__}: {error})")

        catalog = container.repository_catalog
        for profile in REPOSITORIES:
            if await catalog.get(profile.id) is not None:
                print(f"verified  repository {profile.name}")
                continue
            await catalog.add(profile)
            print(f"added     repository {profile.name} ({profile.id})")

        directory = container.agent_directory
        for principal in PRINCIPALS:
            if await directory.get(principal.id) is not None:
                print(f"verified  {principal.role.value:<19} {principal.agentteams_resource_name}")
                continue
            await directory.add(
                principal,
                idempotency_key=f"module-test-team-w4:{principal.agentteams_resource_name}",
                request_fingerprint=command_fingerprint(principal),
                events=(_registered(principal),),
            )
            print(f"added     {principal.role.value:<19} {principal.agentteams_resource_name}")
    finally:
        await container.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--admin-password", default="W4admin-2026!")
    args = parser.parse_args(argv)
    asyncio.run(seed(args.admin_password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
