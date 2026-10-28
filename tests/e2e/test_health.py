import httpx


def test_health_reports_database_and_pgvector(api: str) -> None:
    response = httpx.get(f"{api}/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "pgvector": "available"}


def test_health_fails_loudly_when_database_is_unreachable(api_without_db: str) -> None:
    response = httpx.get(f"{api_without_db}/health", timeout=10)

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}
