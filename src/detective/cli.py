"""Admin CLI. Bootstrap the owner account with: `detective invite you@example.com --name You --role owner`."""

import asyncio
from datetime import timedelta
from typing import Annotated

import typer

from detective.auth import create_invite
from detective.db import SessionLocal, engine
from detective.models import Role

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
