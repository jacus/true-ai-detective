from typing import Annotated

import psycopg
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import StreamingResponse

from detective import auth, cases, evidence
from detective.agent import builder
from detective.models import Case
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
    run: RunAgentInput, request: Request, case: Annotated[Case, Depends(cases.current_case)]
) -> StreamingResponse:
    """AG-UI endpoint: streams the detective's run as Server-Sent Events. Logged-in users only.

    Same as ag-ui-langgraph's add_langgraph_fastapi_endpoint, except the thread is always the user's own
    case: the browser's threadId is ignored, so nobody can read or continue someone else's conversation.
    """
    run = run.model_copy(update={"thread_id": str(case.id)})
    encoder = EventEncoder(accept=request.headers.get("accept", "text/event-stream"))

    async def events():
        # The conversation is stored in Postgres per case; the checkpointer acts as the case's client (RLS).
        async with cases.case_checkpointer(case.client_id) as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)
            agent = LangGraphAgent(name="detective", description="True AI Detective", graph=graph)
            async for event in agent.run(run):
                yield encoder.encode(event)

    return StreamingResponse(events(), media_type=encoder.get_content_type())
