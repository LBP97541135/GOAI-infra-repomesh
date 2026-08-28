"""R8: read all six members back, from both sides, and print the reconciliation.

Read-only by construction -- two GETs per member and no other verb -- so it is
safe to run at any point, including immediately before a materialize. That is
where it earns its keep: materialize does not *mint* an AgentTeams name, it
reads ``principal.agentteams_resource_name`` out of RepoMesh's directory and
looks the controller up by that exact string
(``integrations/agentteams/runtime_projection.ProjectRuntimeProjection._register``).
A member whose pre-created resource is named anything else is not adopted, it is
created a second time -- and a member whose existing resource differs on
skills or ``containerManaged`` is a 409 no retry clears. Both are cheap to see
here and expensive to discover live.

    GET {repomesh}/api/v1/runtime/v2/external-members/{id}/binding?role={role}
    GET {controller}/api/v1/workers/{resourceName}

A non-2xx answer from either endpoint is recorded as a failed check rather than
raised: the whole output is a verdict on six members, and stopping at the first
one hides the other five. Transport failures are not caught -- an unreachable
control plane is not a finding about a member.

Exit status is 0 only when every check on every member passed.

Usage::

    E1_CONTROLLER_TOKEN=... E1_ALPHA_LEADER_REPOMESH_TOKEN=... \\
      python preflight_bindings.py --members members.json [--subset m7]
"""

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from e1_config import ROLE_REPOSITORY_LEADER, Config, Member, load_config

from repomesh.modules.project.domain import RepositoryTeam

CONTROLLER_TOKEN_ENV = "E1_CONTROLLER_TOKEN"

BINDING_PATH = "/api/v1/runtime/v2/external-members/{member_agent_id}/binding"
WORKER_PATH = "/api/v1/workers/{name}"

BINDING_V2_SCHEMA_VERSION = "repomesh.agent-bridge.binding.v2"

#: Skills per role, as ``integrations/agentteams/runtime_projection._SKILLS``
#: sends them. Checked because ``ensure_worker`` compares an existing worker's
#: list against the one being requested: a Repository Leader pre-created with a
#: worker's ``("coding",)`` answers 409 on skills forever after (W-A1).
EXPECTED_SKILLS = {
    "repository_leader": ("code-review", "planning"),
    "worker": ("coding",),
}

TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class Check:
    member: str
    name: str
    ok: bool
    detail: str


def binding_checks(client: httpx.Client, member: Member) -> tuple[list[Check], dict | None]:
    variable = member.repomesh_env
    token = os.environ.get(variable, "")
    if not token:
        return [Check(member.key, "binding", False, f"{variable} is unset")], None
    response = client.get(
        BINDING_PATH.format(member_agent_id=member.agent_id),
        params={"role": member.role},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if response.status_code != 200:
        return (
            [
                Check(
                    member.key,
                    "binding",
                    False,
                    f"HTTP {response.status_code}: {response.text.strip()[:160]}",
                )
            ],
            None,
        )
    binding = response.json()
    rooms = binding.get("allowedRoomIds") or []
    checks = [
        Check(
            member.key,
            "binding.schemaVersion",
            binding.get("schemaVersion") == BINDING_V2_SCHEMA_VERSION,
            str(binding.get("schemaVersion")),
        ),
        Check(
            member.key,
            "binding.role",
            binding.get("role") == member.role,
            str(binding.get("role")),
        ),
        Check(
            member.key,
            "binding.containerManaged",
            binding.get("containerManaged") is False,
            str(binding.get("containerManaged")),
        ),
        Check(
            member.key,
            "binding.workerName",
            binding.get("workerName") == member.resource_name,
            f"{binding.get('workerName')} (roster: {member.resource_name})",
        ),
        Check(
            member.key,
            "binding.matrixUserId",
            bool(binding.get("matrixUserId")),
            str(binding.get("matrixUserId") or "<empty>"),
        ),
        Check(
            member.key,
            "binding.allowedRoomIds",
            len(rooms) >= 2,
            f"{len(rooms)} room(s): {', '.join(str(room) for room in rooms) or '<empty>'}",
        ),
    ]
    return checks, binding


def controller_checks(
    client: httpx.Client, member: Member, token: str, binding: dict | None
) -> list[Check]:
    response = client.get(
        WORKER_PATH.format(name=quote(member.resource_name, safe="")),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if response.status_code != 200:
        return [
            Check(
                member.key,
                "controller",
                False,
                f"HTTP {response.status_code}: {response.text.strip()[:160]}",
            )
        ]
    worker = response.json()
    identity = worker.get("matrixUserID") or ""
    team = worker.get("team") or ""
    canonical = RepositoryTeam.canonical_agentteams_team_name(member.repository_id)
    checks = [
        Check(
            member.key,
            "controller.name",
            worker.get("name") == member.resource_name,
            f"{worker.get('name')} (roster: {member.resource_name})",
        ),
        Check(
            member.key,
            "controller.containerManaged",
            worker.get("containerManaged") is False,
            str(worker.get("containerManaged")),
        ),
        Check(member.key, "controller.matrixUserID", bool(identity), identity or "<empty>"),
        Check(
            member.key,
            "controller.team",
            bool(team),
            f"{team or '<none>'} (canonical for this repository: {canonical})",
        ),
        Check(member.key, "controller.phase", True, str(worker.get("phase") or "<unknown>")),
    ]
    if binding is not None:
        checks.append(
            Check(
                member.key,
                "matrixUserId agrees",
                identity == binding.get("matrixUserId"),
                f"controller {identity or '<empty>'} vs binding {binding.get('matrixUserId')}",
            )
        )
    expected = EXPECTED_SKILLS[member.role]
    if "skills" in worker:
        observed = tuple(worker.get("skills") or ())
        checks.append(
            Check(
                member.key,
                "controller.skills",
                sorted(observed) == sorted(expected),
                f"{list(observed)} (expected {list(expected)})",
            )
        )
    else:
        # The v1.2.0 controller's GET document omits ``skills`` (verified live
        # 2026-08-13, see ``control_plane._assert_worker_matches``). Absence is
        # recorded as unknown rather than as agreement.
        checks.append(
            Check(member.key, "controller.skills", True, f"not reported; expected {list(expected)}")
        )
    return checks


def run(config: Config, members: tuple[Member, ...]) -> int:
    controller_token = os.environ.get(CONTROLLER_TOKEN_ENV, "")
    if not controller_token:
        raise SystemExit(f"{CONTROLLER_TOKEN_ENV} is unset")
    checks: list[Check] = []
    with (
        httpx.Client(
            base_url=config.repomesh_endpoint, timeout=TIMEOUT_SECONDS, follow_redirects=False
        ) as repomesh,
        httpx.Client(
            base_url=config.controller_url, timeout=TIMEOUT_SECONDS, follow_redirects=False
        ) as controller,
    ):
        for member in members:
            member_checks, binding = binding_checks(repomesh, member)
            checks.extend(member_checks)
            checks.extend(controller_checks(controller, member, controller_token, binding))
            note = (
                " (never given a workspace root)"
                if member.role == ROLE_REPOSITORY_LEADER
                else ""
            )
            checks.append(
                Check(member.key, "role", member.role in EXPECTED_SKILLS, f"{member.role}{note}")
            )
    print_table(checks)
    failed = [check for check in checks if not check.ok]
    print(f"\n{len(checks) - len(failed)} passed, {len(failed)} failed, {len(members)} member(s)")
    return 1 if failed else 0


def print_table(checks: list[Check]) -> None:
    headers = ("member", "check", "result", "observed")
    rows = [
        (check.member, check.name, "ok" if check.ok else "FAIL", check.detail) for check in checks
    ]
    widths = [
        max(len(header), *(len(row[column]) for row in rows)) if rows else len(header)
        for column, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(widths[column]) for column, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[column]) for column, value in enumerate(row)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preflight_bindings.py",
        description="Read-only reconciliation of the six members across RepoMesh and AgentTeams.",
    )
    parser.add_argument("--members", required=True, type=Path)
    parser.add_argument("--subset", default=None, help="only members carrying this tag")
    arguments = parser.parse_args(argv)
    config = load_config(arguments.members)
    return run(config, config.select(arguments.subset))


if __name__ == "__main__":
    sys.exit(main())
