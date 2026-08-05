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

# pyproject.toml declares the whole RepoMesh application, but repomesh_runner
# imports exactly one third-party package (httpx); everything else it uses is
# standard library. So httpx is installed explicitly and the package itself is
# installed with --no-deps, which keeps fastapi, sqlalchemy, alembic, asyncpg
# and uvicorn out of the worker. README.md is copied because pyproject.toml
# names it as the project readme; src/repomesh is deliberately not copied, so
# the `repomesh` console script the metadata declares stays unused here.
COPY pyproject.toml README.md ./
COPY src/repomesh_runner ./src/repomesh_runner
RUN python -m pip install --no-cache-dir "httpx>=0.27,<1" \
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

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    REPOMESH_RUNNER_WORKSPACE_ROOT=/workspace \
    REPOMESH_MOCK_SCENARIO=success \
    REPOMESH_MOCK_STATE_DIR=/home/runner/.mock-agent-sessions

WORKDIR /workspace

ENTRYPOINT ["python", "-m", "repomesh_runner"]
