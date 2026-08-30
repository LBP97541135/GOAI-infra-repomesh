from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

BUSINESS_SCHEMAS = (
    "agent_directory",
    "capability_management",
    "repository_intelligence",
    "project",
    "specification",
    "task_orchestration",
    "context",
    "agent_runtime",
    "collaboration",
    "review_validation",
    "change_control",
    "delivery",
    "observability",
    "identity_access",
    "recovery_management",
)
PLATFORM_SCHEMA = "platform"
ALL_SCHEMAS = (*BUSINESS_SCHEMAS, PLATFORM_SCHEMA)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
