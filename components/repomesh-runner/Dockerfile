# RepoMesh Runner worker image.
#
# Contract: this image satisfies contracts/runtime/v1/worker-runtime.md. The
# Runner is PID 1 of the container; it long-polls a RunnerTask from the task
# source, executes it through a protocol driver, and posts ordered RunnerEvents
# to the event sink. Configuration arrives only through the environment
# variables that src/repomesh_runner/runtime_env.py consumes -- permissions
# never do: a permission-bearing variable makes the process refuse to start,
# because permissions travel inside the RunnerTask.
#
# Permission boundary: the container, not the agent's flags. The protocol
# permission callbacks (control_request / control_response) are cooperative --
# they work only because the agent asks and honours the answer. The enforceable
# boundary is this image's filesystem, user, and network scope. Nothing here
# grants an agent anything it cannot already reach from inside the container.
#
# No vendor CLI, no credentials, no host state. The only agent installed is the
# validation mock (`repomesh-mock-agent`, driver profile id `mock`), which
# replays a scripted stream-json conversation and never calls a model. Install a
# real CLI in a derived image if you need one.
#
# Build from the repository root (the build context is the repo, because the
# image installs src/repomesh_runner):
#     docker build -f components/repomesh-runner/Dockerfile -t repomesh-runner:dev .
#
# The context filter is Dockerfile.dockerignore next to this file (BuildKit
# prefers it over the root .dockerignore, which excludes all of components/).

FROM python:3.13-slim

# git only: the executor collects changed files with `git status --porcelain`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/repomesh-runner

# pyproject.toml declares the whole RepoMesh application, so the runner's own
# third-party dependencies are named here and the package itself is installed
# with --no-deps, which keeps fastapi, sqlalchemy, alembic, asyncpg and uvicorn
# out of the worker. README.md is copied because pyproject.toml names it as the
# project readme; src/repomesh is deliberately not copied, so the `repomesh`
# console script the metadata declares stays unused here.
#
# The OpenTelemetry pair is not optional, however little tracing this image
# usually does. `executor.py` imports `observer.py` unconditionally and that
# module imports `opentelemetry.trace` at module scope, because `telemetry.py`
# makes tracing opt-in at *runtime* — without REPOMESH_OTLP_ENDPOINT no
# TracerProvider is installed and every span call stays a near-free no-op — so
# that instrumentation sites never need their own enabled/disabled branch. The
# import is therefore always required even when tracing is off, and leaving it
# out made this image exit on ModuleNotFoundError before it read a single
# environment variable. Versions track pyproject's own pins.
COPY pyproject.toml README.md ./
COPY src/repomesh_runner ./src/repomesh_runner
RUN python -m pip install --no-cache-dir \
        "httpx>=0.27,<1" \
        "opentelemetry-sdk>=1.29,<2" \
        "opentelemetry-exporter-otlp-proto-http>=1.29,<2" \
    && python -m pip install --no-cache-dir --no-deps .

# The mock coding agent and its launcher. The launcher name is what the `mock`
# driver profile resolves on PATH (repomesh_runner/profiles.py, binaries).
COPY components/repomesh-runner/mock/mock_coding_agent.py /opt/repomesh-runner/mock/mock_coding_agent.py
RUN printf '#!/bin/sh\nexec python3 /opt/repomesh-runner/mock/mock_coding_agent.py "$@"\n' \
        > /usr/local/bin/repomesh-mock-agent \
    && chmod 0755 /usr/local/bin/repomesh-mock-agent

# Non-root. The workspace root is a directory the platform mounts or prepares;
# it exists here so an unconfigured run fails on a missing task source rather
# than on a missing directory.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin runner \
    && mkdir -p /workspace \
    && chown -R runner:runner /workspace

USER runner

# Repositories are usually mounted from the host and owned by another uid; git
# refuses to read those ("dubious ownership") and the Runner would then silently
# report no changed files. This relaxes ownership checks only, and only for the
# unprivileged runner user inside the container.
RUN git config --global --add safe.directory '*'

# The task ledger is named explicitly because its default is derived from the
# workspace root, and the workspace root is the one directory this image does
# not own: the platform mounts it, and a volume's ownership shadows the chown
# above. A root-owned mount then leaves the unprivileged runner unable to create
# `<workspace_root>/.runner-state` — and it discovers that *after* executing the
# task and posting its events, so the run lands but the key that makes a
# redelivery a no-op never persists. The ledger is this process's own memory
# rather than workspace content, so it lives in the runner's home; a deployment
# that needs it to outlive the container mounts a volume there.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    REPOMESH_RUNNER_WORKSPACE_ROOT=/workspace \
    REPOMESH_RUNNER_STATE_DIR=/home/runner/.runner-state \
    REPOMESH_MOCK_SCENARIO=success \
    REPOMESH_MOCK_STATE_DIR=/home/runner/.mock-agent-sessions

WORKDIR /workspace

ENTRYPOINT ["python", "-m", "repomesh_runner"]
