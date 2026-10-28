"""Database tables. Changes here need a migration: `just migration "<what changed>"`."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Role(enum.StrEnum):
    owner = "owner"
    client = "client"


# Stored as VARCHAR + CHECK constraint (not a native Postgres enum), so adding a role is a simple migration.
RoleColumn = Enum(Role, native_enum=False, create_constraint=True, length=16, validate_strings=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(unique=True)  # always stored lowercased
    name: Mapped[str]
    role: Mapped[Role] = mapped_column(RoleColumn)
    password_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Invite(Base):
    """A one-time link to create an account. Only a hash of the token is stored."""

    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str]
    name: Mapped[str]
    role: Mapped[Role] = mapped_column(RoleColumn)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime]
    redeemed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UserSession(Base):
    """A logged-in browser. Only a hash of the cookie token is stored, so a DB leak can't hijack sessions."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Case(Base):
    """An investigation. One per client for now (client_id is unique); its id is the LangGraph thread id.

    Row-level security: a database session only sees cases whose client_id matches `app.user_id`.
    """

    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
