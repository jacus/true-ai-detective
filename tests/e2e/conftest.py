"""E2E fixtures: a throwaway Postgres (same image as compose/Railway), a throwaway S3 (RustFS), fake OpenAI
and LangSmith servers, and the real API server process wired to them."""

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import boto3
import httpx
import pytest
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

PG_IMAGE = "pgvector/pgvector:pg18"
S3_IMAGE = "rustfs/rustfs:1.0.4"  # same as compose.yaml; Railway Buckets in prod


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


S3_ENV = {
    # boto3's own settings, as on Railway (where they reference the bucket's credentials).
    "AWS_ACCESS_KEY_ID": "detective-test",
    "AWS_SECRET_ACCESS_KEY": "detective-test-secret",
    "AWS_DEFAULT_REGION": "us-east-1",
    "EVIDENCE_BUCKET": "evidence-test",
}


@pytest.fixture(scope="session")
def s3_endpoint() -> Iterator[str]:
    """Throwaway S3-compatible storage with the evidence bucket created, as Railway creates it in prod."""
    container = (
        DockerContainer(S3_IMAGE)
        .with_env("RUSTFS_ACCESS_KEY", S3_ENV["AWS_ACCESS_KEY_ID"])
        .with_env("RUSTFS_SECRET_KEY", S3_ENV["AWS_SECRET_ACCESS_KEY"])
        .with_exposed_ports(9000)
        .waiting_for(HttpWaitStrategy(9000, "/health"))
    )
    with container:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"
        storage(endpoint).create_bucket(Bucket=S3_ENV["EVIDENCE_BUCKET"])
        yield endpoint


def storage(s3_endpoint: str):  # noqa: ANN201 (boto3 clients are typed by stubs only)
    """A boto3 client on the test S3, for checking what's in the bucket from outside the app."""
    return boto3.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=S3_ENV["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=S3_ENV["AWS_SECRET_ACCESS_KEY"],
        region_name=S3_ENV["AWS_DEFAULT_REGION"],
    )


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


def run_agent(
    api: str, cookie: str, text: str | None, thread_id: str | None = None, state: dict | None = None
) -> list[dict]:
    """One AG-UI run against the api as the given client; returns the streamed events.

    `text=None` sends no new message (what the browser does to refresh the board after new evidence).
    """
    run_input = {
        "threadId": thread_id or str(uuid.uuid4()),
        "runId": str(uuid.uuid4()),
        "state": state or {},
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": text}] if text else [],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    with httpx.stream(
        "POST",
        f"{api}/agent",
        json=run_input,
        headers={"accept": "text/event-stream", "cookie": cookie},
        timeout=30,
    ) as response:
        assert response.status_code == 200
        return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


def chat_request(fake_openai: str) -> dict:
    """The most recent chat request to the (fake) model: the streamed one that answers the client
    (structured-output calls such as the board update are not streamed)."""
    return [r for r in httpx.get(f"{fake_openai}/received").json() if r.get("stream")][-1]


def model_saw(fake_openai: str) -> str:
    """Everything the (fake) model was sent in its most recent chat request."""
    return json.dumps(chat_request(fake_openai)["messages"])


@pytest.fixture(scope="session")
def fake_openai() -> Iterator[str]:
    port = _free_port()
    script = Path(__file__).with_name("fake_openai.py")
    proc = subprocess.Popen([sys.executable, str(script), str(port)])
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(proc, f"{base_url}/received", "fake OpenAI")
    yield base_url
    _stop(proc)


@pytest.fixture(scope="session")
def fake_langsmith() -> Iterator[str]:
    port = _free_port()
    script = Path(__file__).with_name("fake_langsmith.py")
    proc = subprocess.Popen([sys.executable, str(script), str(port)])
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(proc, f"{base_url}/received", "fake LangSmith")
    yield base_url
    _stop(proc)


@pytest.fixture(scope="session")
def web_pages() -> Iterator[str]:
    """A local web server for tests/e2e/pages/, standing in for pages a client links to."""
    port = _free_port()
    pages = Path(__file__).with_name("pages")
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(pages)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(proc, base_url, "web pages")
    yield base_url
    _stop(proc)


def app_env(
    database_url: str, openai_base_url: str, s3_endpoint: str, langsmith: dict[str, str] | None = None
) -> dict[str, str]:
    """The environment the app (api or CLI) runs with in tests, pointed at the given dependencies.

    LangSmith settings from your shell or `.env` are never passed on (tests must not trace with your real
    key); `langsmith` gives the SDK's own `LANGSMITH_*` variables for a test that traces to the fake.
    """
    inherited = {name: value for name, value in os.environ.items() if not name.startswith("LANGSMITH_")}
    return {
        **inherited,
        **(langsmith or {}),
        "DATABASE_URL": database_url,
        # OpenAI SDK's own settings: route model calls to the fake server.
        "OPENAI_BASE_URL": f"{openai_base_url}/v1",
        "OPENAI_API_KEY": "sk-fake",
        # The decision model (JEV via AI Gateway in prod) goes to the fake's OpenAI Decisions API too.
        "DECISIONS_BASE_URL": f"{openai_base_url}/v1",
        "AI_GATEWAY_API_KEY": "gw-fake",
        **S3_ENV,
        "AWS_ENDPOINT_URL": s3_endpoint,
        # A dead proxy for all but this machine: tests never reach the internet (e.g. link fetching).
        # httpx, boto3 and the OpenAI/LangSmith SDKs all honour these standard variables.
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }


def start_api(
    database_url: str, openai_base_url: str, s3_endpoint: str, langsmith: dict[str, str] | None = None
) -> tuple[subprocess.Popen[bytes], str]:
    """Boot the API exactly as in production (`fastapi run`), pointed at the given dependencies."""
    port = _free_port()
    proc = subprocess.Popen(
        ["fastapi", "run", "src/detective/main.py", "--host", "127.0.0.1", "--port", str(port)],
        env=app_env(database_url, openai_base_url, s3_endpoint, langsmith),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(proc, f"{base_url}/docs", "API")
    return proc, base_url


@pytest.fixture
def api(database_url: str, fake_openai: str, s3_endpoint: str) -> Iterator[str]:
    proc, base_url = start_api(database_url, fake_openai, s3_endpoint)
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
    # The Next binary itself, not `npm run start`: npm would leave next-server orphaned on terminate.
    proc = subprocess.Popen(
        ["node_modules/.bin/next", "start", "--hostname", "127.0.0.1", "--port", str(port)],
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
def api_without_db(fake_openai: str, s3_endpoint: str) -> Iterator[str]:
    proc, base_url = start_api(
        f"postgresql://nobody:nothing@127.0.0.1:{_free_port()}/none", fake_openai, s3_endpoint
    )
    yield base_url
    _stop(proc)
