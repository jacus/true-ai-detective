"""E2E fixtures: a throwaway Postgres (same image as compose/Railway), a fake OpenAI server,
and the real API server process wired to both."""

import os
import socket
import subprocess
import sys
import time
import uuid
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


APP_DB_PASSWORD = "app-role-test-password"


@pytest.fixture(scope="session")
def owner_database_url() -> Iterator[str]:
    """Throwaway Postgres, migrated with the real Alembic migrations as the database owner."""
    with PostgresContainer(PG_IMAGE, driver=None) as pg:
        url = pg.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            env={**os.environ, "MIGRATIONS_DATABASE_URL": url, "APP_DB_PASSWORD": APP_DB_PASSWORD},
            check=True,
        )
        yield url


@pytest.fixture(scope="session")
def database_url(owner_database_url: str) -> str:
    """The same database as the app sees it: the `detective_app` role, subject to row-level security."""
    host = owner_database_url.split("@", 1)[1]
    return f"postgresql://detective_app:{APP_DB_PASSWORD}@{host}"


def invite(
    database_url: str, email: str, name: str = "Ann Client", role: str = "client", ttl_hours: int = 72
) -> str:
    """Create an invite with the real admin CLI and return its token."""
    link = subprocess.run(
        ["detective", "invite", email, "--name", name, "--role", role, "--ttl-hours", str(ttl_hours)],
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return link.rsplit("/", 1)[1]


def unique_email(prefix: str = "client") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def session_cookie(response: httpx.Response) -> str:
    """The `detective_session=...` pair from a response, ready to send back as a Cookie header."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith("detective_session="):
            return header.split(";", 1)[0]
    raise AssertionError(f"no session cookie in {response.headers.get_list('set-cookie')}")


def sign_up(api: str, database_url: str, email: str, password: str, role: str = "client") -> str:
    """Invite + redeem through the real API; returns the session cookie."""
    token = invite(database_url, email, role=role)
    response = httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": password})
    assert response.status_code == 204, response.text
    return session_cookie(response)


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


WEB_DIR = Path(__file__).parents[2] / "web"


@pytest.fixture(scope="session")
def web_build() -> None:
    """Production build of the Next.js app, once per test session."""
    subprocess.run(["npm", "run", "build"], cwd=WEB_DIR, check=True, capture_output=True)


@pytest.fixture
def web(web_build: None, api: str) -> Iterator[str]:
    """The real web app (`next start`), relaying the chat to the real api."""
    port = _free_port()
    proc = subprocess.Popen(
        ["npm", "run", "start", "--", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=WEB_DIR,
        env={**os.environ, "DETECTIVE_API_URL": api},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(proc, base_url, "web")
    yield base_url
    _stop(proc)


@pytest.fixture
def api_without_db(fake_openai: str) -> Iterator[str]:
    proc, base_url = start_api(f"postgresql://nobody:nothing@127.0.0.1:{_free_port()}/none", fake_openai)
    yield base_url
    _stop(proc)
