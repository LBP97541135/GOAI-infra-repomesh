"""Provision the roster as external members, then read back each binding.

Two calls per member against RepoMesh's runtime v2 routes, and nothing against
the AgentTeams controller directly -- that is ADR 0004 decision 5, and it is why
this script needs no controller credential at all:

    PUT  /api/v1/runtime/v2/external-members/{memberAgentId}
    GET  /api/v1/runtime/v2/external-members/{memberAgentId}/binding?role={role}

The PUT is guarded by a local administrator session (``Authorization: Bearer
<access_token>`` from ``POST /api/v1/auth/login``, or the ``repomesh_session``
cookie) and carries no body: every fact about the resulting resource belongs to
somebody who is not the caller, the *role* included -- it is read from RepoMesh's
agent directory, which is what makes the preflight's role check meaningful
rather than circular.

The GET is authenticated as the member itself, with the token named by
``<KEY>_REPOMESH_TOKEN``. Deliberately not the global runner control token,
which would read any member's binding and prove nothing: exercising the
member-scoped credential here is how E1 finds a mis-issued ``REPOMESH_RUNNER_WORKER_TOKENS``
entry now instead of at the first lease.

Each binding answer is written to ``--out`` verbatim. ``make_enrollments.py``
reads those files and takes ``allowedRoomIds`` from them, so this is the only
place the authoritative room list enters the workflow.

The two calls are separable with ``--stage`` because they are ordered around
something neither of them does: the binding read refuses a member that belongs
to no AgentTeams Team (``ResolveExternalMemberBinding``), and a Team cannot be
created until every one of its members exists as a worker resource. So the
sequence for a fresh environment is ``--stage provision`` for all six, then the
Team (materialize, or ``agt create team``), then ``--stage binding``. The PUT is
idempotent -- one controller side effect per agent, whichever path provisions it
-- so re-running it costs nothing.

Usage::

    E1_REPOMESH_ADMIN_TOKEN=... E1_ALPHA_LEADER_REPOMESH_TOKEN=... \\
      python provision_members.py --members members.json --out <gitignored dir> [--subset m7]
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from e1_config import Config, Member, load_config

BINDING_V2_SCHEMA_VERSION = "repomesh.agent-bridge.binding.v2"

PROVISION_PATH = "/api/v1/runtime/v2/external-members/{member_agent_id}"
BINDING_PATH = "/api/v1/runtime/v2/external-members/{member_agent_id}/binding"

ADMIN_TOKEN_ENV = "E1_REPOMESH_ADMIN_TOKEN"

TIMEOUT_SECONDS = 30.0


def provision(client: httpx.Client, member: Member, admin_token: str) -> dict[str, object]:
    """PUT the member, and hold the receipt to the three facts it asserts."""

    response = client.put(
        PROVISION_PATH.format(member_agent_id=member.agent_id),
        headers={"Authorization": f"Bearer {admin_token}", "Accept": "application/json"},
    )
    response.raise_for_status()
    receipt = response.json()
    _assert(receipt["workerAgentId"] == str(member.agent_id), member, "workerAgentId", receipt)
    _assert(receipt["workerName"] == member.resource_name, member, "workerName", receipt)
    _assert(receipt["role"] == member.role, member, "role", receipt)
    _assert(receipt["containerManaged"] is False, member, "containerManaged", receipt)
    return receipt


def fetch_binding(client: httpx.Client, member: Member) -> dict[str, object]:
    """GET the binding as the member itself, and hold it to what the Bridge will."""

    variable = member.repomesh_env
    token = os.environ.get(variable, "")
    if not token:
        raise SystemExit(
            f"{variable} is unset: the binding read is authenticated as the member "
            "(adjudication D-6), not as the global runner control token"
        )
    response = client.get(
        BINDING_PATH.format(member_agent_id=member.agent_id),
        params={"role": member.role},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    response.raise_for_status()
    binding = response.json()
    _assert(
        binding["schemaVersion"] == BINDING_V2_SCHEMA_VERSION, member, "schemaVersion", binding
    )
    _assert(binding["role"] == member.role, member, "role", binding)
    _assert(binding["workerAgentId"] == str(member.agent_id), member, "workerAgentId", binding)
    _assert(binding["workerName"] == member.resource_name, member, "workerName", binding)
    _assert(binding["containerManaged"] is False, member, "containerManaged", binding)
    _assert(bool(binding["allowedRoomIds"]), member, "allowedRoomIds", binding)
    return binding


def _assert(condition: bool, member: Member, field: str, document: dict[str, object]) -> None:
    if not condition:
        raise SystemExit(
            f"{member.key}: RepoMesh answered an unusable {field}: {document.get(field)!r}"
        )


def run(
    config: Config, members: tuple[Member, ...], out: Path, *, stage: str, dry_run: bool
) -> None:
    provisioning = stage in ("provision", "both")
    reading = stage in ("binding", "both")
    if dry_run:
        for member in members:
            if provisioning:
                path = PROVISION_PATH.format(member_agent_id=member.agent_id)
                print(f"PUT  {config.repomesh_endpoint}{path}  (as {ADMIN_TOKEN_ENV})")
            if reading:
                path = BINDING_PATH.format(member_agent_id=member.agent_id)
                print(
                    f"GET  {config.repomesh_endpoint}{path}?role={member.role}"
                    f"  (as {member.repomesh_env})"
                )
        return

    admin_token = ""
    if provisioning:
        admin_token = os.environ.get(ADMIN_TOKEN_ENV, "")
        if not admin_token:
            raise SystemExit(
                f"{ADMIN_TOKEN_ENV} is unset: provisioning is guarded by a local administrator "
                "session; POST /api/v1/auth/login and export its access_token"
            )
    if reading:
        out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        base_url=config.repomesh_endpoint,
        timeout=TIMEOUT_SECONDS,
        # A redirect would send a credential somewhere the roster never named.
        follow_redirects=False,
    ) as client:
        for member in members:
            reported = [member.key.ljust(14), member.role.ljust(19)]
            if provisioning:
                receipt = provision(client, member, admin_token)
                reported.append(f"phase={receipt['phase']} containerManaged=false")
            if reading:
                binding = fetch_binding(client, member)
                path = out / f"binding.{member.key}.json"
                path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
                rooms = ", ".join(str(room) for room in binding["allowedRoomIds"])
                reported.append(f"rooms=[{rooms}] -> {path}")
            print(" ".join(reported))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="provision_members.py",
        description="PUT each roster member as an external member and store its v2 binding.",
    )
    parser.add_argument("--members", required=True, type=Path)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="directory the binding answers are written to; keep it gitignored",
    )
    parser.add_argument("--subset", default=None, help="only members carrying this tag")
    parser.add_argument(
        "--stage",
        choices=("provision", "binding", "both"),
        default="both",
        help="the two calls are separable because the binding read needs the AgentTeams Team "
        "to exist and the Team cannot be created until every member is provisioned: run "
        "--stage provision for all six, create the Teams, then --stage binding",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the requests per member and exit; this script is the only one here "
        "that changes the control plane",
    )
    arguments = parser.parse_args(argv)
    config = load_config(arguments.members)
    run(
        config,
        config.select(arguments.subset),
        arguments.out,
        stage=arguments.stage,
        dry_run=arguments.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
