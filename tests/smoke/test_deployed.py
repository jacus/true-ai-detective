"""Smoke checks against a deployed environment. Run with `just smoke <api-url> <web-url>`.

Logs in as a dedicated smoke-test client (SMOKE_EMAIL / SMOKE_PASSWORD from your local .env).
The agent and browser checks make one real call each to the cheap model (fractions of a cent).
"""

import json
import os
import uuid

import httpx
from playwright.sync_api import Page, expect

API_URL = os.environ["SMOKE_API_URL"]
WEB_URL = os.environ["SMOKE_WEB_URL"]
EMAIL = os.environ["SMOKE_EMAIL"]
PASSWORD = os.environ["SMOKE_PASSWORD"]


def run_input(text: str) -> dict:
    return {
        "threadId": f"smoke-{uuid.uuid4()}",
        "runId": str(uuid.uuid4()),
        "state": {},
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": text}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def test_deployed_health_reports_database_and_pgvector() -> None:
    response = httpx.get(f"{API_URL}/health", timeout=15)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "pgvector": "available"}


def test_deployed_detective_refuses_anonymous_runs() -> None:
    response = httpx.post(f"{API_URL}/agent", json=run_input("Free tokens please"), timeout=15)

    assert response.status_code == 401


def log_in() -> str:
    login = httpx.post(f"{API_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert login.status_code == 204
    return login.headers["set-cookie"].split(";", 1)[0]


def test_deployed_case_resets_then_remembers_the_conversation() -> None:
    cookie = log_in()
    old_case = httpx.get(f"{API_URL}/cases/current", headers={"cookie": cookie}, timeout=15).json()["id"]

    # Start from a fresh case each smoke run (also keeps the smoke conversation, and its cost, small).
    reset = httpx.delete(f"{API_URL}/cases/current", headers={"cookie": cookie}, timeout=30)
    assert reset.status_code == 200
    assert reset.json()["id"] != old_case
    assert httpx.get(f"{API_URL}/cases/current/messages", headers={"cookie": cookie}, timeout=15).json() == []

    with httpx.stream(
        "POST",
        f"{API_URL}/agent",
        json=run_input("Smoke test: say hello in five words."),
        headers={"accept": "text/event-stream", "cookie": cookie},
        timeout=60,
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert events[-1]["type"] == "RUN_FINISHED"
    reply = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert reply.strip(), "the deployed detective should answer with text"

    saved = httpx.get(f"{API_URL}/cases/current/messages", headers={"cookie": cookie}, timeout=15).json()
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert saved[0]["content"] == "Smoke test: say hello in five words."


def test_deployed_evidence_file_round_trips_through_the_bucket() -> None:
    cookie = log_in()
    content = f"Smoke evidence {uuid.uuid4()}".encode()

    created = httpx.post(
        f"{API_URL}/cases/current/evidence/files",
        files={"file": ("smoke.txt", content, "text/plain")},
        headers={"cookie": cookie},
        timeout=30,
    )
    assert created.status_code == 201, created.text
    evidence_id = created.json()["id"]
    listed = httpx.get(f"{API_URL}/cases/current/evidence", headers={"cookie": cookie}, timeout=15).json()
    assert evidence_id in [e["id"] for e in listed]

    # The api redirects to a presigned URL on the Railway Bucket; the bucket itself stays private.
    redirect = httpx.get(
        f"{API_URL}/cases/current/evidence/{evidence_id}/file", headers={"cookie": cookie}, timeout=15
    )
    assert redirect.status_code == 307
    signed = redirect.headers["location"]
    download = httpx.get(signed, timeout=30)
    assert download.status_code == 200
    assert download.content == content
    assert httpx.get(signed.split("?", 1)[0], timeout=15).status_code == 403


def test_deployed_web_runtime_refuses_anonymous_replay() -> None:
    case_id = httpx.get(f"{API_URL}/cases/current", headers={"cookie": log_in()}, timeout=15).json()["id"]
    connect = run_input("")
    connect["threadId"] = case_id

    response = httpx.post(f"{WEB_URL}/api/copilotkit/agent/detective/connect", json=connect, timeout=15)

    assert response.status_code != 200


def test_deployed_client_has_a_case() -> None:
    cookie = log_in()

    first = httpx.get(f"{API_URL}/cases/current", headers={"cookie": cookie}, timeout=15)
    second = httpx.get(f"{API_URL}/cases/current", headers={"cookie": cookie}, timeout=15)

    assert first.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_deployed_web_runtime_has_telemetry_off() -> None:
    info = httpx.get(f"{WEB_URL}/api/copilotkit/info", timeout=15).json()

    assert "detective" in info["agents"]
    assert info["telemetryDisabled"] is True


def test_deployed_web_sends_visitors_to_login(page: Page) -> None:
    page.goto(WEB_URL)

    expect(page).to_have_url(f"{WEB_URL}/login")


def test_deployed_web_chat_gets_a_reply_after_login(page: Page) -> None:
    page.goto(f"{WEB_URL}/login")
    page.get_by_label("Email").fill(EMAIL)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()
    expect(page).to_have_url(f"{WEB_URL}/")

    page.wait_for_load_state("networkidle")
    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("Smoke test: my umbrella vanished from the office.")
    message_box.press("Enter")

    # Browser → web (CopilotRuntime + cookie) → api over Railway's private network → real nano model.
    # The case keeps its history, so earlier smoke runs' messages may be on screen: check the newest one.
    chat = page.get_by_test_id("copilot-chat")
    expect(chat).to_have_attribute("data-copilot-running", "true", timeout=30_000)
    expect(chat).to_have_attribute("data-copilot-running", "false", timeout=60_000)
    newest = page.locator(
        '[data-testid="copilot-user-message"], [data-testid="copilot-assistant-message"]'
    ).last
    expect(newest).to_have_attribute("data-testid", "copilot-assistant-message")
    expect(newest).not_to_have_text("")
