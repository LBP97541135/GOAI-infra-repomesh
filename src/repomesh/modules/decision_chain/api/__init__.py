"""HTTP surface for the decision chain (contract decision-chain-v0.1 §6).

Phase 3 delivers the trace API the audit walkthrough consumes:
``GET /api/v1/decision-chains/{project_id}``. The router depends only on the
module's exported contracts (views + trace service); it never reaches into
another module's schema.
"""
