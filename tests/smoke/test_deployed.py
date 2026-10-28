"""Smoke checks against a deployed environment. Run with `just smoke <base-url>`."""

import os

import httpx


def test_deployed_health_reports_database_and_pgvector() -> None:
    base_url = os.environ["SMOKE_BASE_URL"]

    response = httpx.get(f"{base_url}/health", timeout=15)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "pgvector": "available"}
