"""E2E fixtures: a throwaway Postgres (same image as compose/Railway), a fake OpenAI server,
and the real API server process wired to both."""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from testcontainers.community.postgres import PostgresContainer

PG_IMAGE = "pgvector/pgvector:pg18"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(proc: subprocess.Popen[bytes], url: str, what: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.TransportError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError(f"{what} did not start within 30s")


def _stop(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer(PG_IMAGE, driver=None) as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def fake_openai() -> Iterator[str]:
    port = _free_port()
    script = Path(__file__).with_name("fake_openai.py")
    proc = subprocess.Popen([sys.executable, str(script), str(port)])
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(proc, f"{base_url}/received", "fake OpenAI")
    yield base_url
    _stop(proc)


def start_api(database_url: str, openai_base_url: str) -> tuple[subprocess.Popen[bytes], str]:
    """Boot the API exactly as in production (`fastapi run`), pointed at the given dependencies."""
    port = _free_port()
    proc = subprocess.Popen(
        ["fastapi", "run", "src/detective/main.py", "--host", "127.0.0.1", "--port", str(port)],
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            # OpenAI SDK's own settings: route model calls to the fake server.
            "OPENAI_BASE_URL": f"{openai_base_url}/v1",
            "OPENAI_API_KEY": "sk-fake",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(proc, f"{base_url}/docs", "API")
    return proc, base_url


@pytest.fixture
def api(database_url: str, fake_openai: str) -> Iterator[str]:
    proc, base_url = start_api(database_url, fake_openai)
    yield base_url
    _stop(proc)


@pytest.fixture
def api_without_db(fake_openai: str) -> Iterator[str]:
    proc, base_url = start_api(f"postgresql://nobody:nothing@127.0.0.1:{_free_port()}/none", fake_openai)
    yield base_url
    _stop(proc)
