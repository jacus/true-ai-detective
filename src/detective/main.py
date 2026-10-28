import psycopg
from fastapi import FastAPI, Response, status

from detective.settings import Settings

settings = Settings()
app = FastAPI(title="True AI Detective")


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
