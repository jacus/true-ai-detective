"""Database tables. Changes here need a migration: `just migration "<what changed>"`."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Identity, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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


class EvidenceKind(enum.StrEnum):
    file = "file"  # photos are files too (image/*)
    link = "link"
    pin = "pin"
    finding = "finding"  # found by the detective with a research tool (S13), never by the client


class Evidence(Base):
    """Something in a case's locker: a file, link or map pin the client added, or a detective's finding.

    A file's bytes, and a finding's text, live in the S3 bucket at `storage.object_key(case_id, id)`; this
    row describes them. A finding records its provenance: the tool, what it was asked, and the source address
    (`url`, when the tool read one); `created_at` is when it was found.
    Row-level security: visible only if its case is (and `cases` is itself RLS-filtered).
    """

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'file' OR (content_type IS NOT NULL AND size_bytes IS NOT NULL)", "file_details"
        ),
        CheckConstraint("kind <> 'link' OR url IS NOT NULL", "link_details"),
        CheckConstraint(
            "kind <> 'pin' OR (latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            "pin_details",
        ),
        CheckConstraint(
            "kind <> 'finding' OR (tool IS NOT NULL AND tool_input IS NOT NULL AND content_type IS NOT NULL "
            "AND size_bytes IS NOT NULL)",
            "finding_details",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[EvidenceKind] = mapped_column(
        Enum(EvidenceKind, native_enum=False, create_constraint=True, length=16, validate_strings=True)
    )
    label: Mapped[str]  # file name, link address, pin label or what the detective looked up
    content_type: Mapped[str | None]
    size_bytes: Mapped[int | None]
    url: Mapped[str | None]
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    tool: Mapped[str | None]  # a finding's research tool, e.g. "web_search"
    tool_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # the tool call's arguments
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    facts: Mapped[list[Fact]] = relationship(lazy="selectin", order_by="Fact.seq")


class FactKind(enum.StrEnum):
    # From the photo's EXIF metadata (read by code)
    taken_at = "taken_at"
    camera = "camera"
    location = "location"
    # Seen in the photo by the vision model
    text = "text"
    object = "object"
    place = "place"  # also read in documents
    # Read in documents and web pages, each with a quote
    person = "person"
    organisation = "organisation"
    time = "time"
    event = "event"
    contact = "contact"
    # Said by the app itself, e.g. that only part of a long document was read
    note = "note"


class Fact(Base):
    """One thing learned from a piece of evidence, always linked to it (its source).

    Facts read in documents and pages also carry their citation: the exact `quote` from the source text
    (checked to be really there) and its `location` in it, e.g. "page 3" or "part 2 of 5".

    Row-level security: visible only if its case is, like `evidence`. Deleted with its evidence or case.
    """

    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The order facts were found in. Not created_at: within one transaction now() is the same for every row.
    seq: Mapped[int] = mapped_column(Identity(always=True))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), index=True)
    kind: Mapped[FactKind] = mapped_column(
        Enum(FactKind, native_enum=False, create_constraint=True, length=16, validate_strings=True)
    )
    statement: Mapped[str]
    quote: Mapped[str | None]
    location: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
