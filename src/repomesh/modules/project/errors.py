"""What a project topology refuses, and why the classes live here.

These three were defined in ``domain.py`` until the supervision-policy rules
moved to :mod:`repomesh.modules.project.supervision_policy`. That module has to
raise :class:`ProjectTopologyViolation`, and ``domain`` has to call that module,
so leaving the exceptions in ``domain`` would have made the two import each
other. ``domain`` re-exports all three, so every existing spelling —
``from repomesh.modules.project.domain import ProjectTopologyViolation`` —
still resolves to these exact classes.
"""


class ProjectTopologyError(RuntimeError):
    pass


class ProjectTopologyConflict(ProjectTopologyError):
    pass


class ProjectTopologyViolation(ProjectTopologyError):
    pass
