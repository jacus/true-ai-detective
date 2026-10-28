"""Smoke checks against a deployed environment. Run with `just smoke <api-url> <web-url>`.

The agent and browser checks make one real call each to the cheap model (fractions of a cent).
"""

import json
import os
import uuid

import httpx
from playwright.sync_api import Page, expect

API_URL = os.environ["SMOKE_API_URL"]
WEB_URL = os.environ["SMOKE_WEB_URL"]


def test_deployed_health_reports_database_and_pgvector() -> None:
    response = httpx.get(f"{API_URL}/health", timeout=15)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "pgvector": "available"}


def test_deployed_detective_answers_over_ag_ui() -> None:
    run_input = {
        "threadId": f"smoke-{uuid.uuid4()}",
        "runId": str(uuid.uuid4()),
        "state": {},
        "messages": [
            {"id": str(uuid.uuid4()), "role": "user", "content": "Smoke test: say hello in five words."}
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    with httpx.stream(
        "POST", f"{API_URL}/agent", json=run_input, headers={"accept": "text/event-stream"}, timeout=60
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


def test_deployed_web_chat_gets_a_reply(page: Page) -> None:
    page.goto(WEB_URL, wait_until="networkidle")
    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("Smoke test: my umbrella vanished from the office.")
    message_box.press("Enter")

    # Browser → web (CopilotRuntime) → api over Railway's private network → real nano model.
    reply = page.get_by_test_id("copilot-assistant-message")
    expect(reply).to_have_count(1, timeout=30_000)
    expect(page.get_by_test_id("copilot-chat")).to_have_attribute(
        "data-copilot-running", "false", timeout=60_000
    )
    expect(reply).not_to_have_text("")
