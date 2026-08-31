"""The E1 roster reader, shared by every script in this directory.

One file describes the six external members (three Repository Leaders, three
Workers) and the endpoints they are provisioned against, and every other script
in ``scripts/bridge-e1`` reads it through here. A second reader would be a
second opinion about which member is a leader, and the whole point of E1 is that
six processes and one control plane agree.

The validation below is deliberately the *server's* rules restated at the
roster, not new ones: a worker's leader must be a leader in this same file, a
worker's repository must match its leader's (``CreateAgent._validate_scope``),
a repository leader never carries a workspace root (``cli._governed_workspace_root``),
and a resource name must satisfy the controller's own name pattern
(``integrations/agentteams/control_plane._RESOURCE_NAME``). Catching those here
costs nothing and turns a 409 an hour later into a typo now.

Run as a script it answers one question for the PowerShell half::

    python e1_config.py --members members.json --codex-homes [--state-dir DIR]

which prints ``{key: {agentId, sessionRoot, codexHome}}`` derived by importing
``session_root`` from the Bridge itself. PowerShell asks rather than re-deriving
so there is exactly one definition of where a member's codex-home lives.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from repomesh_agent_bridge.adapters.coding_session import session_root

__all__ = [
    "Config",
    "Member",
    "ROLES",
    "ROLE_REPOSITORY_LEADER",
    "ROLE_WORKER",
    "codex_home",
    "load_config",
]

ROLE_WORKER = "worker"
ROLE_REPOSITORY_LEADER = "repository_leader"
ROLES = (ROLE_WORKER, ROLE_REPOSITORY_LEADER)

#: The controller's own resource-name rule, copied from
#: ``integrations/agentteams/control_plane._RESOURCE_NAME``. A name this pattern
#: rejects cannot even be turned into a URL path there, so the roster refuses it
#: before anything tries.
RESOURCE_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,251}[a-z0-9])?$")

#: Roster keys become environment variable names (see :meth:`Member.matrix_env`)
#: and file name stems, so they are held to the narrower of the two rules.
MEMBER_KEY = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,62}[a-z0-9])?$")

_MEMBER_FIELDS = frozenset(
    {
        "key",
        "role",
        "agentId",
        "repositoryId",
        "resourceName",
        "responsibilityPaths",
        "leaderKey",
        "workspaceRoot",
        "subsets",
        "displayName",
    }
)

_CONFIG_FIELDS = frozenset(
    {
        "organizationId",
        "organizationLeaderAgentId",
        "repomeshEndpoint",
        "matrixHomeserverUrl",
        "controllerUrl",
        "codingProfile",
        "members",
    }
)


class RosterInvalid(ValueError):
    """The roster does not describe a set of members the platform would accept."""


@dataclass(frozen=True, slots=True)
class Member:
    key: str
    role: str
    agent_id: UUID
    repository_id: UUID
    resource_name: str
    responsibility_paths: tuple[str, ...]
    leader_key: str | None
    workspace_root: str | None
    subsets: frozenset[str]
    display_name: str | None

    @property
    def is_leader(self) -> bool:
        return self.role == ROLE_REPOSITORY_LEADER

    @property
    def env_stem(self) -> str:
        return "E1_" + self.key.upper().replace("-", "_")

    @property
    def matrix_env(self) -> str:
        """Environment variable holding this member's Matrix access token."""

        return f"{self.env_stem}_MATRIX_TOKEN"

    @property
    def repomesh_env(self) -> str:
        """Environment variable holding this member's own RepoMesh member token.

        Never the global runner control token: under adjudication D-6 the
        ``credentialRefs.repomesh`` slot keeps its historical name and holds the
        external *member* token, which RepoMesh scopes to this agent id alone.
        """

        return f"{self.env_stem}_REPOMESH_TOKEN"


@dataclass(frozen=True, slots=True)
class Config:
    organization_id: UUID
    organization_leader_agent_id: UUID
    repomesh_endpoint: str
    matrix_homeserver_url: str
    controller_url: str
    coding_profile: str
    members: tuple[Member, ...]

    def by_key(self, key: str) -> Member:
        for member in self.members:
            if member.key == key:
                return member
        raise KeyError(key)

    def select(self, subset: str | None) -> tuple[Member, ...]:
        """The members tagged ``subset``, or all of them when nothing is asked for.

        An empty selection is an error rather than a quiet no-op: ``--subset m7``
        that matches nothing means the tag was misspelled, and a script that
        cheerfully did nothing would read as success.
        """

        if subset is None:
            return self.members
        chosen = tuple(member for member in self.members if subset in member.subsets)
        if not chosen:
            tags = sorted({tag for member in self.members for tag in member.subsets})
            raise RosterInvalid(
                f"no roster member is tagged {subset!r}; known tags: {', '.join(tags) or 'none'}"
            )
        return chosen


def codex_home(agent_id: UUID, state_dir: Path | None = None) -> Path:
    """Where one member's codex-home lives.

    Derived by calling the Bridge's own ``session_root`` and appending the name
    ``prepare_session_dirs`` gives the directory, so this moves if and only if
    the product moves. The directory is *not* created here and must not be:
    ``prepare_session_dirs`` creates it and puts the Low integrity label on it,
    and a directory made by some other hand would be Medium until the first
    ``ensure_ready`` relabels the tree.
    """

    return session_root(agent_id, state_dir) / "codex-home"


def load_config(path: Path) -> Config:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RosterInvalid(f"{path} must contain a JSON object")
    _reject_unknown(document, _CONFIG_FIELDS, where="roster")
    raw_members = document.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        raise RosterInvalid("roster.members must be a non-empty array")

    members = tuple(_read_member(entry, index) for index, entry in enumerate(raw_members))
    _assert_unique(members)
    _assert_hierarchy(members)

    return Config(
        organization_id=_uuid(document, "organizationId", where="roster"),
        organization_leader_agent_id=_uuid(
            document, "organizationLeaderAgentId", where="roster"
        ),
        repomesh_endpoint=_url(document, "repomeshEndpoint", where="roster"),
        matrix_homeserver_url=_url(document, "matrixHomeserverUrl", where="roster"),
        controller_url=_url(document, "controllerUrl", where="roster"),
        coding_profile=_text(document, "codingProfile", where="roster"),
        members=members,
    )


def _read_member(entry: object, index: int) -> Member:
    where = f"roster.members[{index}]"
    if not isinstance(entry, dict):
        raise RosterInvalid(f"{where} must be an object")
    _reject_unknown(entry, _MEMBER_FIELDS, where=where)

    key = _text(entry, "key", where=where)
    if not MEMBER_KEY.fullmatch(key):
        raise RosterInvalid(f"{where}.key must match {MEMBER_KEY.pattern}: {key!r}")
    where = f"roster.members[{key}]"

    role = _text(entry, "role", where=where)
    if role not in ROLES:
        raise RosterInvalid(f"{where}.role must be one of {', '.join(ROLES)}")

    resource_name = _text(entry, "resourceName", where=where)
    if not RESOURCE_NAME.fullmatch(resource_name):
        raise RosterInvalid(
            f"{where}.resourceName is not a valid AgentTeams resource name: {resource_name!r}"
        )

    paths = entry.get("responsibilityPaths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        # Both roles carry a repository, and the directory refuses either
        # without responsibility paths (``CreateAgent._validate_scope``).
        raise RosterInvalid(f"{where}.responsibilityPaths must be a non-empty array of strings")

    leader_key = entry.get("leaderKey")
    workspace_root = entry.get("workspaceRoot")
    if role == ROLE_REPOSITORY_LEADER:
        if leader_key is not None:
            raise RosterInvalid(
                f"{where}.leaderKey belongs to a worker; a repository leader reports to the "
                "organization leader named at the top of the roster"
            )
        if workspace_root is not None:
            raise RosterInvalid(
                f"{where}.workspaceRoot is refused for a repository leader: a leader decides "
                "and does not code, and the Bridge refuses --workspace-root for one (AC-02)"
            )
    else:
        if not isinstance(leader_key, str) or not leader_key:
            raise RosterInvalid(f"{where}.leaderKey is required for a worker")
        if not isinstance(workspace_root, str) or not workspace_root:
            raise RosterInvalid(
                f"{where}.workspaceRoot is required for a worker: it is what turns governed "
                "execution on, and the Bridge refuses a path that is not an existing directory"
            )

    subsets = entry.get("subsets", [])
    if not isinstance(subsets, list) or not all(isinstance(tag, str) and tag for tag in subsets):
        raise RosterInvalid(f"{where}.subsets must be an array of strings")

    display_name = entry.get("displayName")
    if display_name is not None and (not isinstance(display_name, str) or not display_name):
        raise RosterInvalid(f"{where}.displayName must be a non-empty string when present")

    return Member(
        key=key,
        role=role,
        agent_id=_uuid(entry, "agentId", where=where),
        repository_id=_uuid(entry, "repositoryId", where=where),
        resource_name=resource_name,
        responsibility_paths=tuple(paths),
        leader_key=leader_key,
        workspace_root=workspace_root,
        subsets=frozenset(subsets),
        display_name=display_name,
    )


def _assert_unique(members: tuple[Member, ...]) -> None:
    for field, values in (
        ("key", [member.key for member in members]),
        ("agentId", [str(member.agent_id) for member in members]),
        ("resourceName", [member.resource_name for member in members]),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise RosterInvalid(f"roster.members.{field} must be unique: {', '.join(duplicates)}")


def _assert_hierarchy(members: tuple[Member, ...]) -> None:
    leaders = {member.key: member for member in members if member.is_leader}
    for member in members:
        if member.is_leader:
            continue
        leader = leaders.get(member.leader_key or "")
        if leader is None:
            raise RosterInvalid(
                f"roster.members[{member.key}].leaderKey does not name a repository_leader in "
                f"this roster: {member.leader_key!r}"
            )
        if leader.repository_id != member.repository_id:
            raise RosterInvalid(
                f"roster.members[{member.key}].repositoryId must equal its leader's "
                f"({leader.key}); RepoMesh refuses a worker whose repository differs"
            )


def _reject_unknown(document: dict[str, object], allowed: frozenset[str], *, where: str) -> None:
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise RosterInvalid(f"{where} has unknown fields: {', '.join(unknown)}")


def _text(document: dict[str, object], field: str, *, where: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RosterInvalid(f"{where}.{field} must be a non-empty string")
    return value.strip()


def _url(document: dict[str, object], field: str, *, where: str) -> str:
    value = _text(document, field, where=where)
    if not value.startswith(("http://", "https://")):
        raise RosterInvalid(f"{where}.{field} must be an http(s) URL: {value!r}")
    return value.rstrip("/")


def _uuid(document: dict[str, object], field: str, *, where: str) -> UUID:
    value = _text(document, field, where=where)
    try:
        return UUID(value)
    except ValueError as malformed:
        raise RosterInvalid(f"{where}.{field} must be a UUID: {value!r}") from malformed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="e1_config.py",
        description="Answer path questions about the E1 roster for the PowerShell scripts.",
    )
    parser.add_argument("--members", required=True, type=Path)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="the Bridge's --state-dir, when the run scripts pass one",
    )
    parser.add_argument(
        "--codex-homes",
        action="store_true",
        required=True,
        help="print {key: {agentId, sessionRoot, codexHome}} as JSON",
    )
    arguments = parser.parse_args(argv)
    config = load_config(arguments.members)
    answer = {
        member.key: {
            "agentId": str(member.agent_id),
            "sessionRoot": str(session_root(member.agent_id, arguments.state_dir)),
            "codexHome": str(codex_home(member.agent_id, arguments.state_dir)),
        }
        for member in config.members
    }
    print(json.dumps(answer, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
