"""Smoke checks against a deployed environment. Run with `just smoke <base-url>`.

The agent check makes one real call to the cheap model (fractions of a cent).
"""

import json
import os
import uuid

import httpx

BASE_URL = os.environ["SMOKE_BASE_URL"]


def test_deployed_health_reports_database_and_pgvector() -> None:
    response = httpx.get(f"{BASE_URL}/health", timeout=15)

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
        "POST", f"{BASE_URL}/agent", json=run_input, headers={"accept": "text/event-stream"}, timeout=60
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]

    assert events[-1]["type"] == "RUN_FINISHED"
    reply = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert reply.strip(), "the deployed detective should answer with text"
