"""Admin CLI. Bootstrap the owner account with: `detective invite you@example.com --name You --role owner`."""

import asyncio
from datetime import timedelta
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from detective.auth import create_invite
from detective.db import SessionLocal, act_as, engine
from detective.models import Case, Evidence, EvidenceKind, Role
from detective.settings import settings
from detective.storage import object_key, s3

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """True AI Detective admin commands."""


@app.command()
def invite(
    email: str,
    name: Annotated[str, typer.Option(help="Name shown to the invited person")],
    role: Annotated[Role, typer.Option()] = Role.client,
    base_url: Annotated[str, typer.Option(envvar="WEB_BASE_URL", help="Public URL of the web app")] = (
        "http://localhost:3000"
    ),
    ttl_hours: Annotated[int, typer.Option(help="Hours until the link expires")] = 7 * 24,
) -> None:
    """Create a one-time invite link and print it."""

    async def run() -> str:
        async with SessionLocal() as db:
            token = await create_invite(
                db, email=email, name=name, role=role, invited_by=None, ttl=timedelta(hours=ttl_hours)
            )
        await engine.dispose()
        return token

    token = asyncio.run(run())
    typer.echo(f"{base_url.rstrip('/')}/invite/{token}")


@app.command()
def reindex() -> None:
    """Rebuild every case's memory (RAG): its facts, and the text of its documents read again from the bucket.

    No model calls, only embeddings. Links are re-indexed by their facts (their pages aren't fetched again).
    """
    # Imported here: these modules create their OpenAI clients on import, which `invite` doesn't need.
    from detective import evidence_analysis, memory
    from detective.evidence import is_document

    async def chunks_of(evidence: Evidence) -> list[memory.Chunk]:
        if evidence.kind != EvidenceKind.file or not is_document(evidence.content_type or ""):
            return []
        stored = await asyncio.to_thread(
            s3.get_object, Bucket=settings.evidence_bucket, Key=object_key(evidence.case_id, evidence.id)
        )
        content = await asyncio.to_thread(stored["Body"].read)
        read = evidence_analysis.read_document(
            {
                "kind": "document",
                "content": content,
                "content_type": evidence.content_type or "",
                "url": None,
                "chunks": [],
                "facts": [],
            }
        )
        return [{"text": c.page_content, "location": c.metadata.get("location")} for c in read["chunks"]]

    async def run() -> int:
        owner = create_async_engine(settings.migrations_sqlalchemy_url)  # lists every case, below RLS
        async with owner.connect() as conn:
            cases = (await conn.execute(select(Case.id, Case.client_id))).all()
        await owner.dispose()
        indexed = 0
        for case_id, client_id in cases:
            async with SessionLocal() as db:
                await act_as(db, client_id)  # from here on, as that client: RLS applies, also to the store
                for evidence in await db.scalars(select(Evidence).where(Evidence.case_id == case_id)):
                    await memory.forget(evidence)
                    await memory.index(evidence, await chunks_of(evidence))
                    indexed += 1
        await engine.dispose()
        return indexed

    typer.echo(f"Re-indexed {asyncio.run(run())} pieces of evidence")
