import uuid
from collections.abc import AsyncIterator

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, SessionTransaction

from detective.settings import settings

engine = create_async_engine(settings.sqlalchemy_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

SET_USER = text("SELECT set_config('app.user_id', :user_id, true)")


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one DB session per request."""
    async with SessionLocal() as db:
        yield db


async def act_as(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Make row-level security apply to `user_id` for the rest of this DB session.

    The setting is transaction-local (`set_config(..., true)`), so it never leaks to the next request on a
    pooled connection; the `after_begin` hook below re-applies it to every later transaction.
    """
    db.info["user_id"] = str(user_id)
    await db.execute(SET_USER, {"user_id": str(user_id)})


@event.listens_for(Session, "after_begin")
def _apply_user_to_new_transaction(session: Session, _: SessionTransaction, connection: Connection) -> None:
    if user_id := session.info.get("user_id"):
        connection.execute(SET_USER, {"user_id": user_id})
