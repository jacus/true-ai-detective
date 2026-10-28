"""Invites, passwords and sessions. The api owns identity; the web app only relays the cookie."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from detective.db import get_db
from detective.models import Invite, Role, User, UserSession
from detective.settings import settings

SESSION_COOKIE = "detective_session"
INVITE_TTL = timedelta(days=7)

password_hash = PasswordHash.recommended()  # argon2id
# Verifying against this when the email is unknown keeps login timing the same either way.
_DUMMY_HASH = password_hash.hash("not-a-real-password")

router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[AsyncSession, Depends(get_db)]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


# --- invites ---------------------------------------------------------------------------------


async def create_invite(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    role: Role,
    invited_by: User | None,
    ttl: timedelta = INVITE_TTL,
) -> str:
    """Create an invite and return its raw token (shown once, never stored)."""
    email = email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    token = secrets.token_urlsafe(32)
    db.add(
        Invite(
            token_hash=hash_token(token),
            email=email,
            name=name,
            role=role,
            invited_by_id=invited_by.id if invited_by else None,
            expires_at=now() + ttl,
        )
    )
    await db.commit()
    return token


async def _open_invite(db: AsyncSession, token: str) -> Invite:
    """The invite for this token if it is still usable. Forged, expired and used tokens look the same."""
    invite = await db.scalar(
        select(Invite).where(
            Invite.token_hash == hash_token(token), Invite.redeemed_at.is_(None), Invite.expires_at > now()
        )
    )
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invite link is invalid or has expired")
    return invite


class InviteInfo(BaseModel):
    email: str
    name: str


@router.get("/invites/{token}")
async def get_invite(token: str, db: Db) -> InviteInfo:
    invite = await _open_invite(db, token)
    return InviteInfo(email=invite.email, name=invite.name)


class RedeemRequest(BaseModel):
    token: str
    password: str = Field(min_length=12, max_length=256)


@router.post("/invites/redeem", status_code=status.HTTP_204_NO_CONTENT)
async def redeem_invite(body: RedeemRequest, db: Db, response: Response) -> None:
    invite = await _open_invite(db, body.token)
    if await db.scalar(select(User).where(User.email == invite.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user = User(
        email=invite.email,
        name=invite.name,
        role=invite.role,
        password_hash=password_hash.hash(body.password),
    )
    invite.redeemed_at = now()
    db.add(user)
    await db.flush()
    await _start_session(db, user, response)


# --- sessions --------------------------------------------------------------------------------


async def _start_session(db: AsyncSession, user: User, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=now() + timedelta(days=settings.session_days),
        )
    )
    await db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


async def current_user(db: Db, detective_session: Annotated[str | None, Cookie()] = None) -> User:
    """FastAPI dependency: the logged-in user, or 401."""
    if detective_session:
        user = await db.scalar(
            select(User)
            .join(UserSession, UserSession.user_id == User.id)
            .where(UserSession.token_hash == hash_token(detective_session), UserSession.expires_at > now())
        )
        if user:
            return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")


async def require_owner(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the agency owner can do this")
    return user


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: LoginRequest, db: Db, response: Response) -> None:
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    password_ok = password_hash.verify(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    await _start_session(db, user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    db: Db, response: Response, detective_session: Annotated[str | None, Cookie()] = None
) -> None:
    if detective_session:
        await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(detective_session)))
        await db.commit()
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="lax"
    )


class Me(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: Role


@router.get("/me")
async def me(user: Annotated[User, Depends(current_user)]) -> Me:
    return Me(id=user.id, email=user.email, name=user.name, role=user.role)


# --- owner: invite clients -------------------------------------------------------------------


class NewInvite(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)


class CreatedInvite(BaseModel):
    invite_path: str  # the web app prefixes its own origin


@router.post("/invites", status_code=status.HTTP_201_CREATED)
async def invite_client(
    body: NewInvite, db: Db, owner: Annotated[User, Depends(require_owner)]
) -> CreatedInvite:
    token = await create_invite(db, email=body.email, name=body.name, role=Role.client, invited_by=owner)
    return CreatedInvite(invite_path=f"/invite/{token}")
