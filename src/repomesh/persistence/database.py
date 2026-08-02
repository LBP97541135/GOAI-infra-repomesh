from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .base import Base


class Database:
    def __init__(
        self,
        url: str,
        *,
        schema_translate_map: Mapping[str, str | None] | None = None,
        echo: bool = False,
    ) -> None:
        execution_options = (
            {"schema_translate_map": dict(schema_translate_map)} if schema_translate_map else None
        )
        self.engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,
            execution_options=execution_options,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session, session.begin():
            yield session

    async def is_ready(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def create_all_for_tests(self) -> None:
        if not self.engine.url.drivername.startswith("sqlite"):
            raise RuntimeError("create_all_for_tests is restricted to SQLite")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
