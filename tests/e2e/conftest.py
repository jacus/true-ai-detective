"""E2E fixtures: a throwaway Postgres (same image as compose/Railway) and the real API server process."""

import os
import socket
import subprocess
import time
from collections.abc import Iterator

import httpx
import pytest
from testcontainers.community.postgres import PostgresContainer

PG_IMAGE = "pgvector/pgvector:pg18"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer(PG_IMAGE, driver=None) as pg:
        yield pg.get_connection_url()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_api(database_url: str) -> tuple[subprocess.Popen[bytes], str]:
    """Boot the API exactly as in production (`fastapi run`), pointed at the given database."""
    port = _free_port()
    proc = subprocess.Popen(
        ["fastapi", "run", "src/detective/main.py", "--host", "127.0.0.1", "--port", str(port)],
        env={**os.environ, "DATABASE_URL": database_url},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/docs", timeout=1)
            return proc, base_url
        except httpx.TransportError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("API did not start within 30s")


@pytest.fixture
def api(database_url: str) -> Iterator[str]:
    proc, base_url = start_api(database_url)
    yield base_url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def api_without_db() -> Iterator[str]:
    proc, base_url = start_api(f"postgresql://nobody:nothing@127.0.0.1:{_free_port()}/none")
    yield base_url
    proc.terminate()
    proc.wait(timeout=10)
