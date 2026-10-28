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


def test_deployed_detective_answers_a_logged_in_client_over_ag_ui() -> None:
    login = httpx.post(f"{API_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert login.status_code == 204
    cookie = login.headers["set-cookie"].split(";", 1)[0]

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
    reply = page.get_by_test_id("copilot-assistant-message")
    expect(reply).to_have_count(1, timeout=30_000)
    expect(page.get_by_test_id("copilot-chat")).to_have_attribute(
        "data-copilot-running", "false", timeout=60_000
    )
    expect(reply).not_to_have_text("")
