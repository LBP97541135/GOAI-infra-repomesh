"""Infrastructure adapters for the observability module.

- ``models``: SQLAlchemy models in the ``observability`` schema.
- ``usage_recorder``: thread-safe usage sink + background flush service.
- ``usage_query``: read-side aggregation for the dashboard endpoints.
"""
