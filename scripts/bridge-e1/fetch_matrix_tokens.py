"""Mint one Matrix access token per roster member, into a gitignored env file.

An external member has no container, so there is no container environment to
read its Matrix token out of -- the way a managed worker's is obtained. The
appservice token is the key instead: the AgentTeams appservice owns the whole
member namespace on the homeserver, so ``m.login.application_service`` logs in
as any of them (PR 4 handoff section 7.5 step 6).

The identity is taken from the controller's own worker document rather than
guessed from the resource name, because ``matrixUserID`` is the field RepoMesh
itself binds to and a homeserver may namespace localparts however it likes.
Reading it here also means this script works before the AgentTeams Team exists,
which the v2 binding read does not.

Tokens go to ``--out`` and nowhere else: not to stdout, not to a log, not into
an exception message. Existing lines in that file are preserved, so the same
file can hold the six ``*_REPOMESH_TOKEN`` values the operator issues by hand.

Usage::

    E1_CONTROLLER_TOKEN=... E1_APPSERVICE_TOKEN=... \\
      python fetch_matrix_tokens.py --members members.json \\
        --out output/bridge-team/e1-members.env [--subset m7]
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
from e1_config import Config, Member, load_config

CONTROLLER_TOKEN_ENV = "E1_CONTROLLER_TOKEN"
APPSERVICE_TOKEN_ENV = "E1_APPSERVICE_TOKEN"

WORKER_PATH = "/api/v1/workers/{name}"
LOGIN_PATH = "/_matrix/client/v3/login"

TIMEOUT_SECONDS = 30.0


def matrix_user_id(client: httpx.Client, member: Member, token: str) -> str:
    response = client.get(
        WORKER_PATH.format(name=quote(member.resource_name, safe="")),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    response.raise_for_status()
    document = response.json()
    identity = document.get("matrixUserID") or ""
    if not identity:
        raise SystemExit(
            f"{member.key}: AgentTeams worker {member.resource_name} has no Matrix identity "
            "yet; the controller publishes one once the member is Running"
        )
    return str(identity)


def login(client: httpx.Client, user_id: str, appservice_token: str) -> str:
    """Log in as one member through the appservice, returning its access token."""

    localpart = user_id.partition(":")[0].lstrip("@")
    response = client.post(
        LOGIN_PATH,
        headers={
            "Authorization": f"Bearer {appservice_token}",
            "Content-Type": "application/json",
        },
        json={
            "type": "m.login.application_service",
            "identifier": {"type": "m.id.user", "user": localpart},
        },
    )
    response.raise_for_status()
    access_token = response.json().get("access_token") or ""
    if not access_token:
        raise SystemExit(f"the homeserver answered a login for {user_id} with no access_token")
    return str(access_token)


def merge_env(path: Path, updates: dict[str, str]) -> None:
    """Write ``updates`` into ``path``, keeping every other line it already had.

    The file is the operator's, not this script's: it also carries the six
    member RepoMesh tokens, which nothing here issues. Rewriting it wholesale
    would take those with it.
    """

    kept: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name = line.partition("=")[0].strip()
            if name in updates:
                continue
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    lines = kept + [f"{name}={value}" for name, value in sorted(updates.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(config: Config, members: tuple[Member, ...], out: Path) -> None:
    controller_token = _required(CONTROLLER_TOKEN_ENV)
    appservice_token = _required(APPSERVICE_TOKEN_ENV)
    updates: dict[str, str] = {}
    with (
        httpx.Client(
            base_url=config.controller_url, timeout=TIMEOUT_SECONDS, follow_redirects=False
        ) as controller,
        httpx.Client(
            base_url=config.matrix_homeserver_url,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as homeserver,
    ):
        for member in members:
            user_id = matrix_user_id(controller, member, controller_token)
            updates[member.matrix_env] = login(homeserver, user_id, appservice_token)
            print(f"{member.key:<14} {user_id:<48} -> {member.matrix_env}")
    merge_env(out, updates)
    print(f"wrote {len(updates)} access tokens to {out} -- keep this file gitignored")


def _required(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value:
        raise SystemExit(f"{variable} is unset")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fetch_matrix_tokens.py",
        description="Log in as each roster member through the AgentTeams appservice.",
    )
    parser.add_argument("--members", required=True, type=Path)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="env file the tokens are written to; must be gitignored",
    )
    parser.add_argument("--subset", default=None, help="only members carrying this tag")
    arguments = parser.parse_args(argv)
    config = load_config(arguments.members)
    run(config, config.select(arguments.subset), arguments.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
