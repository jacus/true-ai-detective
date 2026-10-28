"""Cases. One per client for now; the case id is the LangGraph thread id, so a conversation is the case's."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from detective.auth import current_user
from detective.db import get_db
from detective.models import Case, User

router = APIRouter(prefix="/cases", tags=["cases"])


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
