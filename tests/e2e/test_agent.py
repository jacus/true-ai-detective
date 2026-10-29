from conftest import chat_request, run_agent, sign_up, unique_email
from fake_openai import reply_for


def test_detective_streams_a_reply_over_ag_ui(api: str, database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), "correct horse battery staple")
    events = run_agent(api, cookie, "My bike was stolen from the train station")

    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    chunks = [e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert len(chunks) > 1, "reply should arrive as a stream of chunks"
    assert "".join(chunks) == reply_for("My bike was stolen from the train station")


def test_detective_uses_the_cheap_model_with_its_detective_persona(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), "correct horse battery staple")
    run_agent(api, cookie, "Who took my cat?")

    request = chat_request(fake_openai)
    assert request["model"] == "gpt-5.4-nano"
    assert request["messages"][0]["role"] == "system"
    assert "detective" in request["messages"][0]["content"].lower()
    assert request["messages"][-1] == {"role": "user", "content": "Who took my cat?"}
