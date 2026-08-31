"""Turn the stored v2 bindings into six enrollment documents.

Every identity field is copied from the binding, never from the roster. The
binding is RepoMesh's own answer about who this member is and which rooms it
owns, and the Bridge refuses at startup when the two disagree -- so a generator
that filled the enrollment from the roster would be writing down the very
mismatch preflight exists to catch. ``allowedRoomIds`` in particular is taken
verbatim: a leader's DM room has to be in *both* documents or stage 2 refuses to
start, and typing it by hand is exactly how it ends up in only one.

``credentialRefs`` are ``env:NAME`` locators, which is the one scheme the
Bridge's resolver understands (``application.resolve_env_credential``); the
values live in the gitignored env file and never in these documents.
``repomesh`` is the member's own external-member token, not the global runner
control token (adjudication D-6).

Each document is validated twice before it is written: by ``read_enrollment``,
the reader the Bridge itself uses, and against the declarations in
``contracts/agent-bridge/v2/external-member-enrollment.schema.json`` -- required
keys present, no undeclared ones. There is no ``jsonschema`` in this
environment; the schema's own honesty is covered by
``pytest tests/contracts/test_agent_bridge_v2_contract.py``, which is the
repository's referee for these files.

Usage::

    python make_enrollments.py --members members.json \\
      --bindings <dir provision_members.py wrote> --out <gitignored dir> [--subset m7]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from e1_config import Config, Member, load_config

from repomesh_agent_bridge.contracts import read_enrollment

ENROLLMENT_V2_SCHEMA_VERSION = "repomesh.agent-bridge.enrollment.v2"

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "agent-bridge"
    / "v2"
    / "external-member-enrollment.schema.json"
)


def build_enrollment(member: Member, config: Config, binding: dict[str, Any]) -> dict[str, Any]:
    """The v2 enrollment for one member, with the binding as the authority."""

    if binding["schemaVersion"] != "repomesh.agent-bridge.binding.v2":
        raise SystemExit(f"{member.key}: {binding['schemaVersion']!r} is not a v2 binding")
    if binding["role"] != member.role:
        raise SystemExit(
            f"{member.key}: RepoMesh has this member on file as a {binding['role']}, "
            f"but the roster says {member.role}"
        )
    if binding["workerAgentId"] != str(member.agent_id):
        raise SystemExit(
            f"{member.key}: the stored binding is for agent {binding['workerAgentId']}, "
            f"not {member.agent_id}"
        )
    document: dict[str, Any] = {
        "schemaVersion": ENROLLMENT_V2_SCHEMA_VERSION,
        "role": binding["role"],
        "organizationId": binding["organizationId"],
        "workerAgentId": binding["workerAgentId"],
        "workerName": binding["workerName"],
        "teamName": binding["teamName"],
        "matrixUserId": binding["matrixUserId"],
        "matrixHomeserverUrl": config.matrix_homeserver_url,
        "allowedRoomIds": list(binding["allowedRoomIds"]),
        "repomeshEndpoint": config.repomesh_endpoint,
        "codingProfile": config.coding_profile,
        "credentialRefs": {
            "matrix": f"env:{member.matrix_env}",
            "repomesh": f"env:{member.repomesh_env}",
        },
    }
    if member.display_name is not None:
        document["displayName"] = member.display_name
    return document


def assert_declared(schema: dict[str, Any], document: dict[str, Any]) -> None:
    """Required keys present, no undeclared keys.

    The same two assertions ``tests/contracts/test_agent_bridge_v2_contract.py``
    makes of the frozen fixtures, driven by the same schema file, so a field
    added to the contract shows up here as a failure rather than as silence.
    """

    if schema["additionalProperties"] is not False:
        raise SystemExit(f"{SCHEMA_PATH} no longer forbids additional properties")
    missing = sorted(set(schema["required"]) - set(document))
    if missing:
        raise SystemExit(f"generated enrollment is missing required fields: {', '.join(missing)}")
    undeclared = sorted(set(document) - set(schema["properties"]))
    if undeclared:
        raise SystemExit(f"generated enrollment has undeclared fields: {', '.join(undeclared)}")


def run(config: Config, members: tuple[Member, ...], bindings: Path, out: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    for member in members:
        source = bindings / f"binding.{member.key}.json"
        binding = json.loads(source.read_text(encoding="utf-8"))
        document = build_enrollment(member, config, binding)
        assert_declared(schema, document)
        # The Bridge's own reader, which is stricter than the two checks above:
        # it pins the version, holds every room id and the Matrix user id to
        # their patterns, and refuses a repository_leader at a v1 version.
        enrollment = read_enrollment(document)
        path = out / f"enrollment.{member.key}.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        rooms = len(enrollment.allowed_room_ids)
        print(
            f"{member.key:<14} {enrollment.role:<19} {enrollment.worker_name:<28} "
            f"rooms={rooms} -> {path}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="make_enrollments.py",
        description="Generate v2 enrollment documents from the stored v2 bindings.",
    )
    parser.add_argument("--members", required=True, type=Path)
    parser.add_argument(
        "--bindings",
        required=True,
        type=Path,
        help="directory provision_members.py wrote binding.<key>.json into",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="directory the enrollment documents are written to; keep it gitignored",
    )
    parser.add_argument("--subset", default=None, help="only members carrying this tag")
    arguments = parser.parse_args(argv)
    config = load_config(arguments.members)
    run(config, config.select(arguments.subset), arguments.bindings, arguments.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
