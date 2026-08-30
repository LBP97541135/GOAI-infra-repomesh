"""Four routes, three guards, one response shape.

The routes are fixed and there are no others (FR-09). A caller reads status,
starts everything, stops everything, or restarts one member it can name only by
agent id. There is no route that takes a path, a command, an interpreter or a
member definition, which is why nothing here validates one.
"""

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import LauncherConfig
from .process import (
    MemberProcess,
    MemberProcessPlane,
    StalePidFileClaimed,
    UnknownMember,
)

__all__ = ["LAUNCHER_OP_HEADER", "LAUNCHER_OP_VALUE", "create_app"]

LAUNCHER_OP_HEADER = "X-RepoMesh-Launcher-Op"
LAUNCHER_OP_VALUE = "1"

STALE_PID_FILE = "stale_pid_file"
"""The one refusal that tells the caller something, because the caller can act on it.

A launcher that answers "500" here is a dead end: the member is down, the
launcher will not start it, and nothing on the page says why or what to do. So
this refusal names the members and the exact files to delete. That is safe to
say because of where it is said -- a loopback body, on the machine that owns
those files, to a page the config's Origin allowlist already vouched for.
"""

REFUSED = "launcher operation refused"
"""What a rejected write is told, and the whole of it.

A refusal answers somebody the launcher has just decided it does not trust, so
it says nothing that caller did not already know: no member, no agent id, no
roster version, no path. Whether this machine runs six members or none is not
something a page from the wrong origin gets to learn from the error.
"""

UNKNOWN_MEMBER = "unknown member"


def create_app(config: LauncherConfig, plane: MemberProcessPlane) -> FastAPI:
    """Assemble the launcher over *plane*."""

    def require_console_write(request: Request) -> None:
        """The two things a write must carry, and why they are these two.

        Loopback is the first guard and it is not in this function: the socket is
        bound to 127.0.0.1, so nothing off this machine reaches any of it. What
        remains is the browser threat -- a page the operator did not open, running
        in the operator's own browser, which *is* on this machine and can reach
        loopback like anything else. Two headers answer it, and neither is a
        secret, because a secret on disk to guard loopback would only be one more
        secret on disk.

        ``Origin`` is checked against the config's allowlist by exact string, so
        the Console's own origin passes and a page served from anywhere else --
        including a neighbouring port, and including the literal ``null`` a
        sandboxed or ``file://`` document sends -- does not. A request with no
        ``Origin`` at all fails the same comparison, which is deliberate: the
        browser attaches it to every cross-site write, so its absence is either
        not a browser or not a write worth honouring here.

        The custom header is the second, and its value is not the point --
        forcing the preflight is. A cross-origin ``POST`` carrying a header the
        browser does not consider simple is not "simple" either, so the browser
        must ask permission with an ``OPTIONS`` before it sends anything. A
        hostile page therefore never gets its request made at all: the preflight
        fails against an allowlist that does not name it, and the machine is
        never asked to start or stop a thing. Origin alone would not do this --
        a simple ``POST`` is *sent*, and its response merely hidden, which is far
        too late for an operation that spawns processes.

        This is a dependency on the three write routes rather than middleware,
        for a reason the preflight makes concrete: the browser does not put
        ``X-RepoMesh-Launcher-Op`` on the ``OPTIONS`` request. A guard that ran
        ahead of :class:`CORSMiddleware` would refuse every preflight and lock
        the Console out of exactly the operations the header exists to protect.
        As a dependency it runs only after a route matches, and the preflight
        never reaches it.

        ``GET /v1/status`` is not guarded here. It starts nothing, its body is
        the same process facts the operator can read off their own PID files, and
        CORS still keeps a foreign page from reading the response.
        """
        if request.headers.get(LAUNCHER_OP_HEADER) != LAUNCHER_OP_VALUE:
            raise HTTPException(status_code=403, detail=REFUSED)
        if request.headers.get("Origin") not in config.allowed_origins:
            raise HTTPException(status_code=403, detail=REFUSED)

    # No docs, no schema: "four routes and no others" is a statement about what
    # this process serves, and three generated pages would make it false.
    app = FastAPI(
        title="RepoMesh Local Launcher",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=[LAUNCHER_OP_HEADER],
    )
    write = [Depends(require_console_write)]

    @app.get("/v1/status")
    def read_status() -> dict[str, object]:
        return _answer(config, plane.status())

    @app.post("/v1/members/start", dependencies=write)
    def start_members() -> dict[str, object]:
        try:
            return _answer(config, plane.start_all())
        except StalePidFileClaimed as blocked:
            raise HTTPException(status_code=409, detail=_blocked(blocked)) from None

    @app.post("/v1/members/stop", dependencies=write)
    def stop_members() -> dict[str, object]:
        return _answer(config, plane.stop_all())

    @app.post("/v1/members/{agent_id}/restart", dependencies=write)
    def restart_member(agent_id: str) -> dict[str, object]:
        try:
            return _answer(config, plane.restart(agent_id))
        except UnknownMember:
            raise HTTPException(status_code=404, detail=UNKNOWN_MEMBER) from None
        except StalePidFileClaimed as blocked:
            raise HTTPException(status_code=409, detail=_blocked(blocked)) from None

    return app


def _blocked(blocked: StalePidFileClaimed) -> dict[str, object]:
    """The 409 body: which members are stuck, and the file to delete for each."""
    return {
        "code": STALE_PID_FILE,
        "message": "Delete the PID file named for each member, then start again.",
        "members": [
            {"displayName": claim.member_name, "pidFile": claim.pid_file}
            for claim in blocked.claims
        ],
    }


def _answer(config: LauncherConfig, members: tuple[MemberProcess, ...]) -> dict[str, object]:
    """The one body every operation returns: process facts and the roster version.

    Written out field by field rather than dumped from the dataclass or the
    config, because that is what keeps FR-09's "no credential env, no token, no
    ``auth.json``, no process environment" from depending on nobody ever adding a
    field somewhere else. The env file's path is configuration this function can
    see and deliberately does not mention. ``rosterVersion`` is here because the
    Console derives its start key from it (FR-10); the launcher never reads it.
    """
    return {
        "rosterVersion": config.roster_version,
        "members": [
            {
                "agentId": member.agent_id,
                "displayName": member.display_name,
                "role": member.role,
                "running": member.running,
                "pid": member.pid,
                "logPath": member.log_path,
            }
            for member in members
        ],
    }
