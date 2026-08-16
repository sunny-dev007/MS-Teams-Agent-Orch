from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from agent.config import settings
from agent.core.sqlite_paths import ensure_sqlite_file, to_aiosqlite_url

# Create parent dir before first connection (fixes Azure "unable to open database file").
_db_file = ensure_sqlite_file(settings.database_url)
_db_url = to_aiosqlite_url(_db_file)
engine = create_async_engine(_db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _register_models() -> None:
    """Import ORM modules so Base.metadata knows every table before create_all."""
    from agent.core import session as _session_models  # noqa: F401
    from agent.core import channel_outbox as _outbox_models  # noqa: F401
    from agent.models import task as _task_models  # noqa: F401
    from agent.models import release_event as _release_models  # noqa: F401
    from agent.services import ci_watch as _ci_watch_models  # noqa: F401


async def ensure_db_schema() -> None:
    """Idempotent schema ensure — safe after zip deploy wiped agent.db."""
    _register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def init_db() -> None:
    await ensure_db_schema()


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
