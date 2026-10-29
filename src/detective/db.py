import uuid
from collections.abc import AsyncIterator
from contextvars import ContextVar

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from detective.settings import settings

engine = create_async_engine(settings.sqlalchemy_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

SET_USER = text("SELECT set_config('app.user_id', :user_id, true)")
# Who the current request acts as. A ContextVar is per request (per asyncio task, copied into the tasks it
# starts), so concurrent requests never see each other's user.
acting_user: ContextVar[str | None] = ContextVar("acting_user", default=None)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one DB session per request."""
    async with SessionLocal() as db:
        yield db


async def act_as(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Make row-level security apply to `user_id` for the rest of this request, on every connection.

    The setting is transaction-local (`set_config(..., true)`), so it never leaks to the next request on a
    pooled connection; the engine's `begin` hook below re-applies it to every later transaction, whether it
    is our session's or a library's using our engine (e.g. langchain-postgres's vector store).
    """
    acting_user.set(str(user_id))
    await db.execute(SET_USER, {"user_id": str(user_id)})


@event.listens_for(engine.sync_engine, "begin")
def _apply_user_to_new_transaction(connection: Connection) -> None:
    if user_id := acting_user.get():
        connection.execute(SET_USER, {"user_id": user_id})
