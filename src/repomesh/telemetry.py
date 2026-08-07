"""Product-plane facade over the shared telemetry contract.

Business modules may not import ``repomesh_runner`` (see
``tests/architecture/test_component_boundaries.py``), yet both planes must
agree on the ``repomesh.*`` span-attribute names. This facade is the seam:
modules import from here, the single source of truth stays in
``repomesh_runner.telemetry``, and if the planes ever need true decoupling the
constants can be inlined here without touching any module.
"""

from repomesh_runner.telemetry import SpanAttributes, setup_tracing, traced, tracing_enabled

__all__ = ["SpanAttributes", "setup_tracing", "traced", "tracing_enabled"]
