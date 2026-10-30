"""The research agent (S13c): research is LangChain's `create_agent` with middleware. It plans (write_todos),
calls tools round after round, files what it finds as it goes, stays within its budget, and reports; the
detective decides and answers from the report. The plan reaches the browser while it is written (an AG-UI
CUSTOM event) and stays in the state.

The agent's model calls are scripted (the fake OpenAI server): queued rounds of tool calls, then its report.
"""

import json

import httpx
from conftest import chat_request, run_agent, sign_up, unique_email
from test_evidence import PASSWORD, PHOTO, upload
from test_image_research import examine, looked_at
from test_loop import decision_requests, final_state, queue, queue_tool_calls, received_so_far
from test_memory import add_notes

PLAN = [
    {"content": "Search the case file for the keys", "status": "in_progress"},
    {"content": "Check where the backpack was found", "status": "pending"},
]
DONE = [{**todo, "status": "completed"} for todo in PLAN]
REPORT = {
    "answer": "The keys were left at the library (keys.txt).",
    "confidence": "high",
    "open_questions": ["Who picked them up?"],
}
MODEL_CALLS = 8  # the agent's budget (research_agent.MODEL_CALLS)
PICTURES = 6


def write_todos(todos: list[dict]) -> dict:
    return {"name": "write_todos", "args": {"todos": todos}}


def search(query: str) -> dict:
    return {"name": "search_case_file", "args": {"query": query}}


def agent_requests(fake_openai: str, since: int) -> list[dict]:
    """The research agent's model calls (each offers it the tools)."""
    return [r for r in httpx.get(f"{fake_openai}/received").json()[since:] if r.get("tools")]


def test_the_agent_plans_files_what_it_finds_and_reports(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_notes(api, cookie)
    since = received_so_far(fake_openai)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [write_todos(PLAN), search("keys library")], [write_todos(DONE)])
    httpx.post(f"{fake_openai}/answers/ResearchReport", json=[json.dumps(REPORT)])

    events = run_agent(api, cookie, "Tell me about the backpack under the bench")

    # The plan went to the browser each time it was written, and the last one stays in the state.
    plans = [e["value"] for e in events if e["type"] == "CUSTOM" and e["name"] == "research_plan"]
    assert plans == [PLAN, DONE]
    state = final_state(events)
    assert state["steps"] == ["research", "conclude"]
    assert state["plan"] == DONE and state["report"] == REPORT
    # Three model calls: plan + search, plan again, report; the report is OpenAI structured output.
    assert len(agent_requests(fake_openai, since)) == 3
    # The decision model saw the report before choosing to conclude.
    _, after = decision_requests(fake_openai, since)
    assert f"Research report (confidence: high): {REPORT['answer']}" in after["input"]
    assert "Still open:\n- Who picked them up?" in after["input"]
    # The answer is written from the report. (Filing what the tools find, as it arrives, is the FileFindings
    # middleware's: test_research.py sees the web findings in the locker.)
    system = chat_request(fake_openai)["messages"][0]["content"]
    assert f"Research report (confidence: high): {REPORT['answer']}" in system
    researched = system.split("What you researched for the latest message", 1)[1].split("\n\n", 1)[0]
    assert researched.splitlines()[1:] == ["- search_case_file: keys library"]  # not the plan


def test_the_budget_stops_a_runaway_research_run(api: str, database_url: str, fake_openai: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    since = received_so_far(fake_openai)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(
        fake_openai, [write_todos(PLAN), search("the case")], *[[search(f"lead {n}")] for n in range(12)]
    )
    try:
        events = run_agent(api, cookie, "Find everything about everyone.")
    finally:
        httpx.delete(f"{fake_openai}/tool_calls")  # what the agent never got to

    assert len(agent_requests(fake_openai, since)) == MODEL_CALLS
    state = final_state(events)
    assert state["steps"] == ["research", "conclude"], "the detective still answered"
    # No report from the agent: what is left of its plan is what is still open.
    assert state["report"] == {
        "answer": "The research budget ran out before a report; what it found is in the case file.",
        "confidence": "low",
        "open_questions": [todo["content"] for todo in PLAN],
    }


def test_at_most_six_pictures_per_research_run(api: str, database_url: str, fake_openai: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    upload(api, cookie, "final.png", PHOTO, "image/png")
    since = received_so_far(fake_openai)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [examine("final.png")] * (PICTURES + 1))

    run_agent(api, cookie, "Which car is in final.png?")

    assert len(looked_at(fake_openai, since)) == PICTURES
    # The one over the limit was refused, and the agent was told so.
    *_, report_request = agent_requests(fake_openai, since)
    refused = [m for m in report_request["messages"] if m["role"] == "tool" and "limit" in m["content"]]
    assert len(refused) == 1, [m["content"] for m in report_request["messages"] if m["role"] == "tool"]
