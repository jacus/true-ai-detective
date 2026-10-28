"""Evidence locker: files (photos too), links and map pins attached to the client's case.

Upload goes through the api (it checks size and stores under the case); download is a redirect to a
short-lived presigned S3 URL, minted only after row-level security found the evidence in the caller's case.
"""

import uuid
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from detective.cases import current_case
from detective.db import get_db
from detective.models import Case, Evidence, EvidenceKind
from detective.settings import settings
from detective.storage import object_key, s3

router = APIRouter(prefix="/cases/current/evidence", tags=["evidence"])

# Shown in the browser; every other type (HTML, SVG, PDF…) is only ever downloaded.
INLINE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
DOWNLOAD_URL_SECONDS = 300


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
    created_at: datetime


class LinkIn(BaseModel):
    url: HttpUrl  # http(s) only: it is shown as a clickable link


class PinIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


async def add(db: AsyncSession, evidence: Evidence) -> EvidenceOut:
    db.add(evidence)
    await db.commit()
    await db.refresh(evidence)  # created_at comes from the database
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
    evidence = Evidence(
        id=uuid.uuid4(),
        case_id=case.id,
        kind=EvidenceKind.file,
        label=file.filename or "unnamed",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=file.size,
    )
    # boto3 is blocking, so it runs in a worker thread (FastAPI's documented way for sync libraries).
    await run_in_threadpool(
        s3.upload_fileobj,
        file.file,
        settings.evidence_bucket,
        object_key(case.id, evidence.id),
        ExtraArgs={"ContentType": evidence.content_type},
    )
    return await add(db, evidence)


@router.post("/links", status_code=status.HTTP_201_CREATED)
async def add_link(
    link: LinkIn, case: Annotated[Case, Depends(current_case)], db: Annotated[AsyncSession, Depends(get_db)]
) -> EvidenceOut:
    url = str(link.url)
    return await add(db, Evidence(case_id=case.id, kind=EvidenceKind.link, label=url, url=url))


@router.post("/pins", status_code=status.HTTP_201_CREATED)
async def add_pin(
    pin: PinIn, case: Annotated[Case, Depends(current_case)], db: Annotated[AsyncSession, Depends(get_db)]
) -> EvidenceOut:
    return await add(db, Evidence(case_id=case.id, kind=EvidenceKind.pin, **pin.model_dump()))


@router.get("/{evidence_id}/file", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def download_file(
    evidence_id: uuid.UUID,
    case: Annotated[Case, Depends(current_case)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    """Redirect to a presigned URL that works for a few minutes; the bucket itself is private."""
    evidence = await db.scalar(
        select(Evidence).where(
            Evidence.id == evidence_id, Evidence.case_id == case.id, Evidence.kind == EvidenceKind.file
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
