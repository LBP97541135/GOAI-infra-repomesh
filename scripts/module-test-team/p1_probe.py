"""P-1 probe: run INSIDE the AgentTeams worker image, on agentteams-net.

Asks the questions the acceptance criterion asks, in order: is there a docker
CLI / compose / socket in the worker's world; failing that, what does the
controller's /docker/ passthrough allow — and specifically the calls compose
would have to make (network create, volume create, bind mounts, own naming).
Every step prints command-equivalent, status, and an excerpt — the minimal
evidence set shape.
"""

import contextlib
import json
import os
import shutil
import urllib.error
import urllib.request

BASE = (os.environ.get("CTRL") or os.environ["AGENTTEAMS_CONTROLLER_URL"]) + "/docker"
TOKEN = os.environ.get("TOKEN") or os.environ["AGENTTEAMS_AUTH_TOKEN"]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode(errors="replace")
            return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)


def step(sid, desc, status, excerpt):
    print(f"[{sid}] {desc}\n     status={status} excerpt={excerpt[:160]!r}")


def probe(sid, desc, method, path, body=None):
    status, text = call(method, path, body)
    step(sid, desc, status, text)
    return status, text


ALPINE_SLEEP = {"Image": "alpine:latest", "Cmd": ["sleep", "30"]}

# --- T1/T2: local docker capability ---
step(
    "T1",
    "which docker / docker compose",
    0,
    f"docker={shutil.which('docker')} docker-compose={shutil.which('docker-compose')}",
)
step("T2", "/var/run/docker.sock present?", 0, str(os.path.exists("/var/run/docker.sock")))

# --- T3: proxy reachable ---
probe("T3", "GET /docker/version", "GET", "/version")

# --- T4/T5: what compose needs first ---
probe("T4", "POST /docker/networks/create", "POST", "/networks/create", {"Name": "itest-p1-net"})
probe("T5", "POST /docker/volumes/create", "POST", "/volumes/create", {"Name": "itest-p1-vol"})

# --- T6: own naming convention ---
probe(
    "T6", "create name=itest-p1-svc", "POST", "/containers/create?name=itest-p1-svc", ALPINE_SLEEP
)

# --- T7: bind mount (a worktree would need this) ---
probe(
    "T7",
    "create with Binds",
    "POST",
    "/containers/create?name=agentteams-worker-itest-p1",
    {**ALPINE_SLEEP, "HostConfig": {"Binds": ["/tmp:/work"]}},
)

# --- T8: the degraded path: prefixed, no binds, on agentteams-net ---
_, created = probe(
    "T8a",
    "create prefixed/no-bind",
    "POST",
    "/containers/create?name=agentteams-worker-itest-p1",
    {**ALPINE_SLEEP, "HostConfig": {"NetworkMode": "agentteams-net"}},
)
cid = None
with contextlib.suppress(Exception):
    cid = json.loads(created).get("Id")
if cid:
    probe("T8b", "start", "POST", f"/containers/{cid}/start")
    status, inspected = call("GET", f"/containers/{cid}/json")
    try:
        state = json.loads(inspected)["State"]["Status"]
    except Exception:  # noqa: BLE001
        state = inspected
    step("T8c", "inspect state", status, str(state))
    probe("T8d", "kill", "POST", f"/containers/{cid}/kill")
    probe("T8e", "delete (cleanup)", "DELETE", f"/containers/{cid}")
print("probe done")
