"""Evidence locker: files (photos too), links and map pins attached to the client's case, and what the
detective found researching it (findings, filed by the `research` module with their provenance).

Upload goes through the api (it checks size and stores under the case); download is a redirect to a
short-lived presigned S3 URL, minted only after row-level security found the evidence in the caller's case.
Photos, documents (PDF, text) and links are analysed when added (`evidence_analysis` graph) and stored with
the facts found in them; the facts and the text read are then indexed in the case memory (`memory`, RAG).
"""

import io
import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from detective import memory
from detective.cases import current_case
from detective.db import get_db
from detective.evidence_analysis import analyse, facts_from
from detective.memory import Chunk
from detective.models import Case, Evidence, EvidenceKind, Fact, FactKind
from detective.settings import settings
from detective.storage import INLINE_TYPES, object_key, s3

router = APIRouter(prefix="/cases/current/evidence", tags=["evidence"])


def is_document(content_type: str) -> bool:
    return content_type == "application/pdf" or content_type.startswith("text/")


DOWNLOAD_URL_SECONDS = 300


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: FactKind
    statement: str
    quote: str | None
    location: str | None


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: EvidenceKind
    label: str
    content_type: str | None
    size_bytes: int | None
    url: str | None
    latitude: float | None
    longitude: float | None
    tool: str | None  # a finding's provenance: the research tool, its input, and `url` its source
    tool_input: dict[str, Any] | None
    created_at: datetime
    facts: list[FactOut]


class LinkIn(BaseModel):
    url: HttpUrl  # http(s) only: it is shown as a clickable link


class PinIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


def pin_statement(pin: PinIn) -> str:
    return f'The client marked "{pin.label}" on the map at {pin.latitude:.5f}, {pin.longitude:.5f}'


async def add(db: AsyncSession, evidence: Evidence, chunks: list[Chunk] | None = None) -> EvidenceOut:
    db.add(evidence)
    await db.commit()
    await db.refresh(evidence)  # created_at comes from the database
    # After the commit: the memory rows reference the evidence. If embedding fails now, the evidence is kept
    # but not searchable until `detective reindex`.
    await memory.index(evidence, chunks or [])  # ty: ignore[invalid-argument-type]  (@traceable's ParamSpec)
    return EvidenceOut.model_validate(evidence)


@router.get("")
async def list_evidence(
    case: Annotated[Case, Depends(current_case)], db: Annotated[AsyncSession, Depends(get_db)]
) -> list[EvidenceOut]:
    found = await db.scalars(
        select(Evidence).where(Evidence.case_id == case.id).order_by(Evidence.created_at)
    )
    return [EvidenceOut.model_validate(e) for e in found]


@router.post("/files", status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile,
    case: Annotated[Case, Depends(current_case)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EvidenceOut:
    if not file.size:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty")
    if file.size > settings.evidence_max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The file is too big")
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    photo = content_type in INLINE_TYPES
    if photo:
        try:
            Image.open(io.BytesIO(content)).verify()
        except UnidentifiedImageError, SyntaxError, OSError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That photo can't be read") from None
    evidence = Evidence(
        id=uuid.uuid4(),
        case_id=case.id,
        kind=EvidenceKind.file,
        label=file.filename or "unnamed",
        content_type=content_type,
        size_bytes=file.size,
    )
    # Before anything is stored: if a model call fails, the upload fails as a whole.
    chunks: list[Chunk] = []
    if photo or is_document(content_type):
        analysis = await analyse("photo" if photo else "document", content, content_type)
        evidence.facts = facts_from(case.id, analysis["facts"])
        chunks = analysis["chunks"]
    # boto3 is blocking, so it runs in a worker thread (FastAPI's documented way for sync libraries).
    await run_in_threadpool(
        s3.upload_fileobj,
        io.BytesIO(content),
        settings.evidence_bucket,
        object_key(case.id, evidence.id),
        ExtraArgs={"ContentType": evidence.content_type},
    )
    return await add(db, evidence, chunks)


@router.post("/links", status_code=status.HTTP_201_CREATED)
async def add_link(
    link: LinkIn, case: Annotated[Case, Depends(current_case)], db: Annotated[AsyncSession, Depends(get_db)]
) -> EvidenceOut:
    url = str(link.url)
    evidence = Evidence(case_id=case.id, kind=EvidenceKind.link, label=url, url=url)
    analysis = await analyse("page", url=url)
    evidence.facts = facts_from(case.id, analysis["facts"])
    return await add(db, evidence, analysis["chunks"])


@router.post("/pins", status_code=status.HTTP_201_CREATED)
async def add_pin(
    pin: PinIn, case: Annotated[Case, Depends(current_case)], db: Annotated[AsyncSession, Depends(get_db)]
) -> EvidenceOut:
    evidence = Evidence(case_id=case.id, kind=EvidenceKind.pin, **pin.model_dump())
    # A pin is a fact too, so the detective can recall it and the board can place it.
    evidence.facts = [Fact(case_id=case.id, kind=FactKind.location, statement=pin_statement(pin))]
    return await add(db, evidence)


@router.get("/{evidence_id}/file", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def download_file(
    evidence_id: uuid.UUID,
    case: Annotated[Case, Depends(current_case)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    """Redirect to a presigned URL that works for a few minutes; the bucket itself is private.

    Files and findings have one (a finding's is the text the detective's tool returned)."""
    evidence = await db.scalar(
        select(Evidence).where(
            Evidence.id == evidence_id,
            Evidence.case_id == case.id,
            Evidence.kind.in_([EvidenceKind.file, EvidenceKind.finding]),
        )
    )
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file in your case")
    disposition = "inline" if evidence.content_type in INLINE_TYPES else "attachment"
    url = s3.generate_presigned_url(  # signed locally, no network call
        "get_object",
        Params={
            "Bucket": settings.evidence_bucket,
            "Key": object_key(case.id, evidence.id),
            "ResponseContentDisposition": f"{disposition}; filename*=UTF-8''{quote(evidence.label)}",
        },
        ExpiresIn=DOWNLOAD_URL_SECONDS,
    )
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
