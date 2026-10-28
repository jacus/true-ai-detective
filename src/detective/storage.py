"""The S3 client for evidence files. boto3 takes endpoint, keys and region from its own AWS_* variables."""

import uuid

import boto3
from botocore.config import Config

from detective.settings import settings

# SigV4 for every presigned URL (boto3 would otherwise sign us-east-1 URLs with the legacy SigV2).
s3 = boto3.client(
    "s3", config=Config(signature_version="s3v4", s3={"addressing_style": settings.s3_addressing_style})
)


def case_prefix(case_id: uuid.UUID) -> str:
    return f"cases/{case_id}/"


def object_key(case_id: uuid.UUID, evidence_id: uuid.UUID) -> str:
    """Files are stored under their case and evidence id, never under a name the client chose."""
    return f"{case_prefix(case_id)}{evidence_id}"


def delete_case_files(case_id: uuid.UUID) -> None:
    """Delete every file stored for a case (blocking; run it in a worker thread)."""
    pages = s3.get_paginator("list_objects_v2").paginate(
        Bucket=settings.evidence_bucket, Prefix=case_prefix(case_id)
    )
    for page in pages:
        for stored in page.get("Contents", []):
            s3.delete_object(Bucket=settings.evidence_bucket, Key=stored["Key"])
