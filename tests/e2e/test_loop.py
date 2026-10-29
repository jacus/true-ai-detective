"""The loop: each client message, the detective decides its next step with a decision model (JEV in prod,
the fake's Decisions API here) and walks the graph accordingly. A scripted conversation walks every branch.

The fake decides "ask" unless a test queues decisions; research is the fake's tool calls (a search of the
case file unless a test queues its own).
"""

import httpx
from conftest import chat_request, run_agent, sign_up, unique_email
from test_evidence import PASSWORD
from test_memory import add_notes, recalled

ASK = "Ask the client one or two sharp questions"
CONSULT = "State the assumption you are making"
CONCLUDE = "Answer the client from what you found, naming the source"


def queue(fake_openai: str, *decisions: dict) -> None:
    httpx.post(f"{fake_openai}/decisions", json=list(decisions))


def queue_tool_calls(fake_openai: str, *rounds: list[dict]) -> None:
    httpx.post(f"{fake_openai}/tool_calls", json=list(rounds))


def decision_requests(fake_openai: str, since: int) -> list[dict]:
    return [r for r in httpx.get(f"{fake_openai}/received").json()[since:] if "questions" in r]


def embedded(fake_openai: str, since: int) -> list[str]:
    received = httpx.get(f"{fake_openai}/received").json()[since:]
    return [text for r in received if "input" in r and "questions" not in r for text in r["input"]]


def received_so_far(fake_openai: str) -> int:
    return len(httpx.get(f"{fake_openai}/received").json())


def final_state(events: list[dict]) -> dict:
    return [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"][-1]


def test_research_then_conclude(api: str, database_url: str, fake_openai: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_notes(api, cookie)
    since = received_so_far(fake_openai)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [{"name": "search_case_file", "args": {"query": "keys library"}}])

    events = run_agent(api, cookie, "Tell me about the backpack under the bench")

    state = final_state(events)
    assert state["steps"] == ["research", "conclude"]
    assert state["decision"]["action"] == "conclude"
    # The case file was searched twice: with the client's words, then with the query the model wrote.
    assert embedded(fake_openai, since) == ["Tell me about the backpack under the bench", "keys library"]
    first, second = decision_requests(fake_openai, since)
    assert "done for the latest message: 0 of 2" in first["input"]
    assert "done for the latest message: 1 of 2" in second["input"] and "keys.txt" in second["input"]
    # The answer is the conclude node's, from both searches' findings.
    assert CONCLUDE in chat_request(fake_openai)["messages"][0]["content"]
    lines = recalled(fake_openai)
    assert any("backpack.txt" in line for line in lines) and any("keys.txt" in line for line in lines)


def test_ask_and_consult_reply_with_their_own_instruction(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)

    queue(fake_openai, {"choice": "ask"})
    asked = final_state(run_agent(api, cookie, "Someone stole something from me."))
    ask_prompt = chat_request(fake_openai)["messages"][0]["content"]
    queue(fake_openai, {"choice": "consult"})
    consulted = final_state(run_agent(api, cookie, "It was my umbrella, I think at the cafe."))
    consult_prompt = chat_request(fake_openai)["messages"][0]["content"]

    assert asked["steps"] == ["ask"] and ASK in ask_prompt
    assert consulted["steps"] == ["consult"] and CONSULT in consult_prompt


def test_the_loop_stops_after_two_searches(api: str, database_url: str, fake_openai: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_notes(api, cookie)
    since = received_so_far(fake_openai)
    keen = {"research": 0.7, "ask": 0.05, "consult": 0.2, "conclude": 0.05}
    queue(fake_openai, *[{"choice": "research", "probabilities": keen}] * 3)

    events = run_agent(api, cookie, "Was the man in the grey coat seen anywhere else?")

    # Two searches, then research is off the table: the most probable other step (consult) is taken.
    assert final_state(events)["steps"] == ["research", "research", "consult"]
    assert len(decision_requests(fake_openai, since)) == 3
    assert len(embedded(fake_openai, since)) == 3, "the client's words, then two written queries"
    assert CONSULT in chat_request(fake_openai)["messages"][0]["content"]


def test_a_refused_decision_falls_back_to_asking(api: str, database_url: str, fake_openai: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    queue(fake_openai, {"refusal": True})

    state = final_state(run_agent(api, cookie, "Help."))

    assert state["steps"] == ["ask"] and state["decision"] == {"action": "ask", "probabilities": {}}


def test_the_decision_model_is_asked_one_typed_question(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    since = received_so_far(fake_openai)

    run_agent(api, cookie, "My bike was stolen from the train station")

    [request] = decision_requests(fake_openai, since)
    assert request["model"] == "typesafe-ai/jev"
    [question] = request["questions"]
    assert question["type"] == "choice" and question["name"] == "next_step"
    assert [c["value"] for c in question["choices"]] == ["research", "ask", "consult", "conclude"]
    assert "Client: My bike was stolen from the train station" in request["input"]
