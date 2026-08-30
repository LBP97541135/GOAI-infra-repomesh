"""The operator's machine, exposed to the Console as four fixed operations.

This package is a sibling of ``repomesh_agent_bridge`` and ``repomesh_runner``
rather than a module inside either, and the reason is the same reason it exists
at all: it runs on the operator's host, outside every process the platform
manages, and it is the only thing in this repository whose job is to make a
local Windows process appear. It imports nothing from ``repomesh`` and nothing
from ``repomesh_agent_bridge`` -- it starts Bridges, it does not contain one --
so a machine that only launches members needs no control plane on it.

What it is *not* is a process manager. Starting and stopping is already written,
already reviewed and already run live in ``scripts/start-local-cli.ps1`` and
``scripts/bridge-e1/stop_members.ps1``, including the parts nothing here would
get right on the second try: loading credentials from a gitignored env file
without echoing them, creating a hidden window, and writing the PID file that is
the only record of which process serves which member. This package shells to
those scripts. What it adds is a safe way for a web page to ask for them.

"Safe" is three things and no more (FR-09). The socket is bound to loopback, so
nothing off this machine can reach it. Write operations must carry an ``Origin``
the config names, so a page the operator did not open cannot spend their
machine. And they must carry a fixed custom header, which is what forces the
browser into a CORS preflight and therefore into asking permission before the
request is ever made. There is no token and no TLS, because there is no network
here to protect and a secret stored to guard loopback would be one more secret
on the disk.

It never sees a credential. The env file's *path* is configuration; its contents
are read by PowerShell, in the child's environment, and no response body this
package writes has anywhere to put them.

Unlike its two siblings this module re-exports nothing. They have importers --
the container, a CLI, other packages' tests -- and a public surface is what those
importers are given. Nothing imports this one: it is a process the operator
starts, its only entry point is ``__main__``, and a curated surface here would be
an interface for nobody.
"""
