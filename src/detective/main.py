import psycopg
from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint
from fastapi import Depends, FastAPI, Response, status

from detective import auth
from detective.agent import graph
from detective.settings import settings

app = FastAPI(title="True AI Detective")
app.include_router(auth.router)


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


# AG-UI endpoint: POST /agent streams the detective's run as Server-Sent Events. Logged-in users only.
add_langgraph_fastapi_endpoint(
    app,
    LangGraphAgent(name="detective", description="True AI Detective", graph=graph),
    path="/agent",
    dependencies=[Depends(auth.current_user)],
)
