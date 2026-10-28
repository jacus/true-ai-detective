import json
import uuid

import httpx
from fake_openai import reply_for


def run_agent(api: str, user_text: str) -> list[dict]:
    """Send one AG-UI run to the detective and collect the streamed events."""
    run_input = {
        "threadId": str(uuid.uuid4()),
        "runId": str(uuid.uuid4()),
        "state": {},
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": user_text}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    events = []
    with httpx.stream(
        "POST", f"{api}/agent", json=run_input, headers={"accept": "text/event-stream"}, timeout=30
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def test_detective_streams_a_reply_over_ag_ui(api: str) -> None:
    events = run_agent(api, "My bike was stolen from the train station")

    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    chunks = [e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert len(chunks) > 1, "reply should arrive as a stream of chunks"
    assert "".join(chunks) == reply_for("My bike was stolen from the train station")


def test_detective_uses_the_cheap_model_with_its_detective_persona(api: str, fake_openai: str) -> None:
    run_agent(api, "Who took my cat?")

    request = httpx.get(f"{fake_openai}/received").json()[-1]
    assert request["model"] == "gpt-5.4-nano"
    assert request["messages"][0]["role"] == "system"
    assert "detective" in request["messages"][0]["content"].lower()
    assert request["messages"][-1] == {"role": "user", "content": "Who took my cat?"}
