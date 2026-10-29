from typing import Annotated

import psycopg
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from detective import auth, cases, evidence
from detective.agent import builder
from detective.db import get_db
from detective.models import Case, Evidence, Fact
from detective.settings import settings

app = FastAPI(title="True AI Detective")
app.include_router(auth.router)
app.include_router(cases.router)
app.include_router(evidence.router)


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    """Liveness plus a real round-trip to Postgres, including whether pgvector can be enabled."""
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url, connect_timeout=3) as conn:
            cursor = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')"
            )
            row = await cursor.fetchone()
    except psycopg.OperationalError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "error", "database": "unreachable"}

    pgvector = "available" if row and row[0] else "missing"
    return {"status": "ok", "database": "ok", "pgvector": pgvector}


@app.post("/agent")
async def run_detective(
    run: RunAgentInput,
    request: Request,
    case: Annotated[Case, Depends(cases.current_case)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """AG-UI endpoint: streams the detective's run as Server-Sent Events. Logged-in users only.

    Same as ag-ui-langgraph's add_langgraph_fastapi_endpoint, except the thread is always the user's own
    case: the browser's threadId is ignored, so nobody can read or continue someone else's conversation.
    """
    run = run.model_copy(update={"thread_id": str(case.id)})
    found = await db.execute(
        select(Fact.statement, Fact.location, Evidence.label)
        .join(Evidence, Fact.evidence_id == Evidence.id)
        .where(Fact.case_id == case.id)
        .order_by(Fact.created_at)
    )
    case_facts = [
        f"{statement} (from {label}{f', {location}' if location else ''})"
        for statement, location, label in found
    ]
    encoder = EventEncoder(accept=request.headers.get("accept", "text/event-stream"))

    async def events():
        # The conversation is stored in Postgres per case; the checkpointer acts as the case's client (RLS).
        async with cases.case_checkpointer(case.client_id) as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)
            # ag-ui-langgraph hands the config's `configurable` to LangGraph as runtime context (CaseContext).
            # Its `graph` parameter is typed without a context schema, hence the ignore.
            agent = LangGraphAgent(
                name="detective",
                description="True AI Detective",
                graph=graph,  # ty: ignore[invalid-argument-type]
                config={"configurable": {"case_facts": case_facts}},
            )
            async for event in agent.run(run):
                yield encoder.encode(event)

    return StreamingResponse(events(), media_type=encoder.get_content_type())
