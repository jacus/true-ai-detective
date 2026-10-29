"""Cases. One per client for now; the case id is the LangGraph thread id, so a conversation is the case's."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

import psycopg
from ag_ui_langgraph.utils import langchain_messages_to_agui
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import DictRow, dict_row
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from detective.agent import builder
from detective.auth import current_user
from detective.board import EMPTY, Board
from detective.db import get_db
from detective.models import Case, User
from detective.settings import settings
from detective.storage import delete_case_files

router = APIRouter(prefix="/cases", tags=["cases"])


@asynccontextmanager
async def case_checkpointer(user_id: uuid.UUID) -> AsyncGenerator[AsyncPostgresSaver]:
    """LangGraph's Postgres checkpointer on a dedicated connection that acts as `user_id`.

    The connection is opened for this one run and closed after, so the session-wide `app.user_id` can't
    leak to anyone else, and row-level security applies to every query the library makes.
    Connection options are the ones the checkpointer's docs require for a manual connection.
    """
    async with await psycopg.AsyncConnection[DictRow].connect(
        settings.database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        await conn.execute("SELECT set_config('app.user_id', %s, false)", (str(user_id),))
        yield AsyncPostgresSaver(conn)


def thread_of(case: Case) -> RunnableConfig:
    return {"configurable": {"thread_id": str(case.id)}}


async def current_case(
    user: Annotated[User, Depends(current_user)], db: Annotated[AsyncSession, Depends(get_db)]
) -> Case:
    """FastAPI dependency: the logged-in user's case, created on first use. RLS scopes every query."""
    await db.execute(insert(Case).values(id=uuid.uuid4(), client_id=user.id).on_conflict_do_nothing())
    # App check (WHERE) and database check (RLS) both limit this to the user's own case.
    case = await db.scalar(select(Case).where(Case.client_id == user.id))
    await db.commit()
    assert case is not None
    return case


class CaseOut(BaseModel):
    id: uuid.UUID
    created_at: datetime


@router.get("/current")
async def get_current_case(case: Annotated[Case, Depends(current_case)]) -> CaseOut:
    return CaseOut(id=case.id, created_at=case.created_at)


@router.get("/current/messages")
async def get_current_messages(case: Annotated[Case, Depends(current_case)]) -> list[dict[str, Any]]:
    """The case's conversation from the checkpoint, as AG-UI messages (the chat shows them on load)."""
    async with case_checkpointer(case.client_id) as saver:
        state = await builder.compile(checkpointer=saver).aget_state(thread_of(case))
    messages = langchain_messages_to_agui(state.values.get("messages", []))
    return [m.model_dump(mode="json", by_alias=True, exclude_none=True) for m in messages]


@router.get("/current/board")
async def get_current_board(case: Annotated[Case, Depends(current_case)]) -> Board:
    """The case board from the checkpoint (the page shows it on load; runs then stream its updates)."""
    async with case_checkpointer(case.client_id) as saver:
        state = await builder.compile(checkpointer=saver).aget_state(thread_of(case))
    return state.values.get("board", EMPTY)


@router.delete("/current")
async def reset_current_case(
    case: Annotated[Case, Depends(current_case)],
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseOut:
    """Delete the case, its evidence files and its whole conversation for good, then start a fresh case."""
    await run_in_threadpool(delete_case_files, case.id)  # rows go with the case (ON DELETE CASCADE)
    async with case_checkpointer(user.id) as saver:
        await saver.adelete_thread(str(case.id))  # needs the case row, so before deleting it (RLS)
    await db.delete(case)
    await db.commit()
    fresh = await current_case(user, db)
    return CaseOut(id=fresh.id, created_at=fresh.created_at)
