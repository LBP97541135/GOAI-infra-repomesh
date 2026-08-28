"""Create or verify the six AgentPrincipals the E1 roster describes.

Writes straight through ``AgentDirectory.add`` rather than through
``CreateAgent``, and for exactly one reason: E1 pins each member's agent id in
the roster. The ids are what every later step is keyed on -- the codex-home a
member's CLI session lives in is ``sessions/<agentId>/codex-home``, the
per-member RepoMesh token is issued against the id, and the D-10 auth.json copy
needs all six paths known before anything is seeded. ``CreateAgent`` mints ids
itself, so it cannot be handed one.

Bypassing it does not mean inventing its rules. ``singleton_key`` is derived by
the same formula (``CreateAgent._singleton_key``), the ``AgentPrincipalRegistered``
envelope is the one it emits, and the hierarchy and scope rules it enforces are
checked here before anything is written -- against the roster (``e1_config``) and
against the directory itself for the organization leader.

Idempotent by re-reading rather than by re-writing: a member already on file is
compared field by field and left alone. A member on file that disagrees with the
roster is a refusal, not a repair -- two answers to "what is this agent" is the
condition E1 exists to keep out of the live run.

Usage::

    REPOMESH_DATABASE_URL=... python seed_members.py --members members.json [--dry-run]
"""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

from e1_config import Config, Member, load_config

from repomesh.bootstrap.app import build_default_container
from repomesh.modules.agent_directory.contracts import AgentPrincipalStatus, AgentRole
from repomesh.modules.agent_directory.domain import AgentPrincipal
from repomesh.shared.domain import new_id
from repomesh.shared.events import ActorType, EventEnvelope
from repomesh.shared.idempotency import command_fingerprint

_ROLES = {
    "repository_leader": AgentRole.REPOSITORY_LEADER,
    "worker": AgentRole.WORKER,
}


def build_principal(member: Member, config: Config, leader_agent_id: UUID) -> AgentPrincipal:
    """The principal the roster describes, with ``CreateAgent``'s own singleton key."""

    role = _ROLES[member.role]
    return AgentPrincipal(
        id=member.agent_id,
        organization_id=config.organization_id,
        role=role,
        leader_agent_id=leader_agent_id,
        singleton_key=(
            f"repository:{member.repository_id}:leader"
            if role is AgentRole.REPOSITORY_LEADER
            else None
        ),
        repository_id=member.repository_id,
        responsibility_paths=member.responsibility_paths,
        agentteams_resource_name=member.resource_name,
        status=AgentPrincipalStatus.ACTIVE,
    )


def registered_event(principal: AgentPrincipal) -> EventEnvelope:
    """``CreateAgent``'s envelope, copied field for field.

    A principal that arrived without it would be invisible to everything that
    reads the event stream, and "seeded by a script" is not a distinction the
    rest of the platform should be able to make.
    """

    return EventEnvelope(
        event_type="AgentPrincipalRegistered",
        actor_type=ActorType.SERVICE,
        actor_id="repomesh-bridge-e1",
        aggregate_type="AgentPrincipal",
        aggregate_id=principal.id,
        aggregate_version=1,
        correlation_id=new_id(),
        payload={
            "role": principal.role.value,
            "leaderAgentId": (
                str(principal.leader_agent_id) if principal.leader_agent_id else None
            ),
            "repositoryId": (
                str(principal.repository_id) if principal.repository_id else None
            ),
            "agentteamsResourceName": principal.agentteams_resource_name,
        },
    )


def disagreements(existing: AgentPrincipal, wanted: AgentPrincipal) -> list[str]:
    return [
        f"{field}: on file {getattr(existing, field)!r} != roster {getattr(wanted, field)!r}"
        for field in (
            "organization_id",
            "role",
            "leader_agent_id",
            "singleton_key",
            "repository_id",
            "responsibility_paths",
            "agentteams_resource_name",
            "status",
        )
        if getattr(existing, field) != getattr(wanted, field)
    ]


async def seed(config: Config, *, dry_run: bool) -> None:
    container = build_default_container()
    try:
        directory = container.agent_directory
        leader = await directory.get(config.organization_leader_agent_id)
        if leader is None:
            raise SystemExit(
                "organization leader is not registered: "
                f"{config.organization_leader_agent_id}. Seed it first -- a repository "
                "leader may only report to an ACTIVE organization leader"
            )
        if leader.role is not AgentRole.ORGANIZATION_LEADER:
            raise SystemExit(
                f"agent {leader.id} is a {leader.role.value}, not an organization leader"
            )
        if leader.status is not AgentPrincipalStatus.ACTIVE:
            raise SystemExit(f"organization leader {leader.id} is {leader.status.value}")
        if leader.organization_id != config.organization_id:
            raise SystemExit(
                f"organization leader {leader.id} belongs to organization "
                f"{leader.organization_id}, not to roster organization {config.organization_id}"
            )

        # Leaders first: ``leader_agent_id`` is a self-referencing foreign key, so
        # a worker inserted before its leader has nothing to point at.
        ordered = sorted(config.members, key=lambda member: not member.is_leader)
        for member in ordered:
            parent = (
                config.organization_leader_agent_id
                if member.is_leader
                else config.by_key(member.leader_key or "").agent_id
            )
            wanted = build_principal(member, config, parent)
            existing = await directory.get(member.agent_id)
            if existing is not None:
                drift = disagreements(existing, wanted)
                if drift:
                    raise SystemExit(
                        f"{member.key} ({member.agent_id}) is already on file and disagrees "
                        "with the roster:\n  " + "\n  ".join(drift)
                    )
                print(f"verified  {member.key:<14} {member.role:<19} {member.resource_name}")
                continue
            if dry_run:
                print(f"would add {member.key:<14} {member.role:<19} {member.resource_name}")
                continue
            await directory.add(
                wanted,
                idempotency_key=f"bridge-e1:{member.key}",
                request_fingerprint=command_fingerprint(wanted),
                events=(registered_event(wanted),),
            )
            print(f"added     {member.key:<14} {member.role:<19} {member.resource_name}")
    finally:
        await container.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_members.py",
        description="Create or verify the six E1 AgentPrincipals described by the roster.",
    )
    parser.add_argument("--members", required=True, type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be created without writing; this is the only script here "
        "that writes to the RepoMesh database",
    )
    arguments = parser.parse_args(argv)
    asyncio.run(seed(load_config(arguments.members), dry_run=arguments.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
