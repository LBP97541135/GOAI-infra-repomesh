import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from repomesh.modules.agent_directory.infrastructure.models import AgentPrincipalRecord
from repomesh.modules.collaboration.infrastructure import (
    CollaborationMessageRecord,
    ProcessedMatrixEventRecord,
)
from repomesh.modules.context.infrastructure.models import (
    ContextAccessEventRecord,
    ContextBundleItemRecord,
    ContextBundleRecord,
    ContextDeltaItemRecord,
    ContextDeltaRecord,
    ContextObjectRecord,
    ContextObjectVersionRecord,
    ContextRelationRecord,
)
from repomesh.modules.delivery.infrastructure import (
    ChangeSetRecord,
    ChangeSetRepositoryRecord,
    SCMCommandRecord,
    SCMObservationRecord,
    SCMPollCursorRecord,
)
from repomesh.modules.platform_config.bootstrap_store import BootstrapOperationRecord
from repomesh.modules.platform_config.store import PlatformCredentialRecord
from repomesh.modules.project.infrastructure import (
    ProjectAgentTopologyRecord,
    ProjectRepositoryTeamRecord,
    TopologyPolicyDraftRecord,
)
from repomesh.modules.repository_intelligence.infrastructure.models import (
    PlanSnapshotRecord,
    RepositoryRecord,
)
from repomesh.modules.review_validation.infrastructure import ValidationSnapshotRecord
from repomesh.modules.specification.infrastructure import (
    SpecificationRecord,
    SpecificationVersionRecord,
)
from repomesh.modules.task_orchestration.infrastructure import TaskRecord
from repomesh.persistence.base import Base
from repomesh.persistence.models import (
    AuditEventRecord,
    IdempotencyRecord,
    OutboxEventRecord,
    StateEventRecord,
    TraceLinkRecord,
)
from repomesh.settings import get_settings

_REGISTERED_MODELS = (
    AgentPrincipalRecord,
    CollaborationMessageRecord,
    ProcessedMatrixEventRecord,
    ContextObjectRecord,
    ContextObjectVersionRecord,
    ContextRelationRecord,
    ContextBundleRecord,
    ContextBundleItemRecord,
    ContextDeltaRecord,
    ContextDeltaItemRecord,
    ContextAccessEventRecord,
    ChangeSetRecord,
    ChangeSetRepositoryRecord,
    ProjectAgentTopologyRecord,
    ProjectRepositoryTeamRecord,
    TopologyPolicyDraftRecord,
    RepositoryRecord,
    PlanSnapshotRecord,
    ValidationSnapshotRecord,
    SCMObservationRecord,
    SCMPollCursorRecord,
    SCMCommandRecord,
    SpecificationRecord,
    SpecificationVersionRecord,
    TaskRecord,
    StateEventRecord,
    AuditEventRecord,
    OutboxEventRecord,
    TraceLinkRecord,
    IdempotencyRecord,
    PlatformCredentialRecord,
    BootstrapOperationRecord,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
