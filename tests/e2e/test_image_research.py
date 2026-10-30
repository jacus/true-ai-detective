"""Visual research (S13b): the detective finds pictures on the web, looks at them with a question, and keeps
the ones that show what it is looking for in the evidence locker, as photos with their provenance. It can
also look again, with a question, at a photo the client gave it.

The model's tool calls and what the vision model "sees" are scripted (the fake OpenAI server); the tools are
real code. The image search and the pictures were recorded once from the real web and a real SearXNG
(tests/e2e/cassettes/research.yaml, `just record-research`). Both pictures are from Wikimedia Commons:
"Porsche-Arena during 2022 Porsche Tennis Grand Prix" by Charliechytrak (CC BY-SA 4.0) and
"Ana Ivanović 2010, Stuttgart2" by Henry Weidemann (CC BY 2.0).
"""

import io
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from conftest import _stop, chat_request, run_agent, sign_up, start_api, unique_email
from PIL import Image
from test_evidence import PASSWORD, PHOTO, listed, upload
from test_loop import final_state, queue, queue_tool_calls, received_so_far

QUESTION = "Which make and model is the car on the court?"
SEARCH = {"name": "image_search", "args": {"query": "Porsche Tennis Grand Prix winner car"}}
COMMONS = "https://upload.wikimedia.org/wikipedia/commons/thumb"
ARENA = (  # the prize cars stand beside the court
    f"{COMMONS}/7/7d/Porsche-Arena_during_2022_Porsche_Tennis_Grand_Prix.jpg/"
    "500px-Porsche-Arena_during_2022_Porsche_Tennis_Grand_Prix.jpg"
)
PLAYER = (  # a player, and no car
    f"{COMMONS}/4/4d/Ana_Ivanovi%C4%87_2010%2C_Stuttgart2.jpg/500px-Ana_Ivanovi%C4%87_2010%2C_Stuttgart2.jpg"
)
CARS: dict[str, Any] = {
    "shows_subject": True,
    "answer": "Three Porsche cars, a red Taycan among them, stand beside the court.",
    "clues": [
        {"kind": "object", "statement": "A red four-door electric sports car with a light bar at the back"},
        {"kind": "text", "statement": 'The court boards read "PORSCHE"'},
    ],
}
NO_CAR: dict[str, Any] = {
    "shows_subject": False,
    "answer": "No car is visible: a player hits a forehand.",
    "clues": [],
}
SEEN = "A white Porsche 911 Carrera S Cabriolet stands on the court."  # what the fake sees by default


def examine(image: str) -> dict:
    return {"name": "examine_image", "args": {"image": image, "question": QUESTION}}


def sees(fake_openai: str, *answers: dict) -> None:
    httpx.post(f"{fake_openai}/answers/ImageAnswer", json=[json.dumps(answer) for answer in answers])


def structured(fake_openai: str, since: int, schema: str) -> list[dict]:
    """The model requests since then asking for that schema's answer."""
    received = httpx.get(f"{fake_openai}/received").json()[since:]
    return [r for r in received if r.get("response_format", {}).get("json_schema", {}).get("name") == schema]


def looked_at(fake_openai: str, since: int) -> list[dict]:
    """The vision model's requests for a picture with a question (examine_image)."""
    return structured(fake_openai, since, "ImageAnswer")


def test_the_detective_keeps_web_pictures_that_show_what_it_looks_for(
    recorded_api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(recorded_api, database_url, unique_email(), PASSWORD)
    since = received_so_far(fake_openai)
    # Two rounds: find pictures and look at one, then look at another.
    queue(fake_openai, {"choice": "research"}, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [SEARCH, examine(ARENA)], [examine(PLAYER)])
    sees(fake_openai, CARS, NO_CAR)

    events = run_agent(recorded_api, cookie, "What car did the Porsche Tennis Grand Prix winner get?")

    assert final_state(events)["steps"] == ["research", "research", "conclude"]
    # The second round saw the first one's calls and results as they were: the pictures the search found.
    rounds = [r for r in httpx.get(f"{fake_openai}/received").json()[since:] if r.get("tools")]
    assert len(rounds) == 2 and len(rounds[0]["messages"]) == 2
    earlier = rounds[1]["messages"][2:]
    assert [call["function"]["name"] for call in earlier[0]["tool_calls"]] == [
        "image_search",
        "examine_image",
    ]
    results = [m["content"] for m in earlier if m["role"] == "tool"]
    assert len(results) == 2 and any("\nimage: https://" in r and "\nfound on: " in r for r in results)
    # The vision model saw each picture, with the question.
    requests = looked_at(fake_openai, since)
    assert len(requests) == 2 and requests[0]["model"] == "gpt-5.4-nano"
    question, picture = requests[0]["messages"][1]["content"]
    assert question == {"type": "text", "text": QUESTION}
    assert picture["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # The picture that shows the cars is kept, with its provenance; the other one isn't.
    filed = [item for item in listed(recorded_api, cookie) if item["kind"] == "finding"]
    assert sorted(item["tool"] for item in filed) == ["examine_image", "image_search"]
    [kept] = [item for item in filed if item["tool"] == "examine_image"]
    assert kept["tool_input"] == examine(ARENA)["args"] and kept["url"] == ARENA
    assert kept["content_type"] == "image/jpeg" and kept["created_at"]
    assert [fact["statement"] for fact in kept["facts"]] == [
        CARS["answer"],
        *(clue["statement"] for clue in CARS["clues"]),
    ]
    stored = httpx.get(
        f"{recorded_api}/cases/current/evidence/{kept['id']}/file",
        headers={"cookie": cookie},
        follow_redirects=True,  # to the presigned bucket URL
    )
    assert stored.headers["content-type"] == "image/jpeg" and len(stored.content) == kept["size_bytes"]
    assert Image.open(io.BytesIO(stored.content)).width == 500  # the picture itself, as downloaded
    # The search's results are filed as a finding too: each picture's address and the page it is on.
    [found] = [item for item in filed if item["tool"] == "image_search"]
    assert found["tool_input"] == SEARCH["args"] and found["facts"]
    # The answer is written from what was seen, naming the kept picture; the dropped one is only reported.
    system = chat_request(fake_openai)["messages"][0]["content"]
    assert f"{CARS['answer']} (from examine_image: {ARENA}, {QUESTION})" in system
    assert f"{PLAYER} doesn't show it: {NO_CAR['answer']}" in system
    # The reply knows what research can do and what it did, so it doesn't guess it can't look at pictures.
    assert "looking at a picture (a photo in the case file or one on the web) with a question" in system
    done = system.split("What you researched for the latest message", 1)[1].split("\n\n", 1)[0]
    assert done.splitlines()[1:] == [
        "- image_search: Porsche Tennis Grand Prix winner car",
        f"- examine_image: {ARENA}, {QUESTION}",
        f"- examine_image: {PLAYER}, {QUESTION}",
    ]


def test_something_that_isnt_a_picture_is_reported_and_nothing_is_kept(
    recorded_api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(recorded_api, database_url, unique_email(), PASSWORD)
    since = received_so_far(fake_openai)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [examine("https://example.com/")])  # a web page, recorded

    events = run_agent(recorded_api, cookie, "What car is on example.com?")

    assert final_state(events)["steps"] == ["research", "conclude"]
    assert (
        "Nothing from examine_image: https://example.com/, "
        f"{QUESTION} (https://example.com/ isn't a picture (JPEG, PNG, GIF or WebP))"
    ) in chat_request(fake_openai)["messages"][0]["content"]
    assert looked_at(fake_openai, since) == [] and listed(recorded_api, cookie) == []


def test_the_detective_looks_again_at_the_clients_own_photo_with_the_configured_vision_model(
    database_url: str, fake_openai: str, s3_endpoint: str
) -> None:
    proc, api = start_api(database_url, fake_openai, s3_endpoint, env={"VISION_MODEL": "openai:gpt-5.4-mini"})
    try:
        alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
        bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
        since = received_so_far(fake_openai)
        upload(api, alice, "final.png", PHOTO, "image/png")
        queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
        queue_tool_calls(fake_openai, [examine("final.png")])

        run_agent(api, alice, "Which car is in my photo?")
        alice_saw = chat_request(fake_openai)["messages"][0]["content"]
        queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
        queue_tool_calls(fake_openai, [examine("final.png")])
        run_agent(api, bob, "Which car is in final.png?")
        bob_saw = chat_request(fake_openai)["messages"][0]["content"]

        # Looked at with the question, by the model VISION_MODEL names (the upload's photo analysis too).
        [request] = looked_at(fake_openai, since)
        assert request["model"] == "gpt-5.4-mini"
        assert request["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        [analysed] = structured(fake_openai, since, "PhotoFacts")
        assert analysed["model"] == "gpt-5.4-mini"
        # What was seen serves the answer; the photo is already in the locker, so nothing new is filed.
        assert f"{SEEN} (from final.png, looked at again)" in alice_saw
        assert [item["label"] for item in listed(api, alice)] == ["final.png"]
        # Another client can't have the detective look at it.
        assert "There is no photo called 'final.png' in the case file" in bob_saw
        assert listed(api, bob) == []
    finally:
        _stop(proc)


@pytest.fixture
def busy_site() -> Iterator[tuple[str, list[str]]]:
    """A site that first says "too many requests, retry after a second", then serves the picture; it notes
    each request's user agent."""
    asked: list[str] = []

    class Busy(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append(self.headers["user-agent"])
            if len(asked) == 1:
                self.send_response(429)
                self.send_header("retry-after", "1")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("content-type", "image/png")
            self.end_headers()
            self.wfile.write(PHOTO)

        def log_message(self, format: str, *args: Any) -> None:  # quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Busy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/car.png", asked
    server.shutdown()


def test_a_busy_site_asking_for_a_short_wait_is_waited_out(
    api: str, database_url: str, fake_openai: str, busy_site: tuple[str, list[str]]
) -> None:
    picture, asked = busy_site
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [examine(picture)])

    run_agent(api, cookie, QUESTION)

    assert len(asked) == 2, "asked again after the wait"
    assert asked[0] == "TrueAIDetective/1.0 (https://github.com/jacus/true-ai-detective)"  # who is asking
    [kept] = [item for item in listed(api, cookie) if item["tool"] == "examine_image"]
    assert kept["url"] == picture and kept["content_type"] == "image/png"


def test_an_answer_breaking_the_rules_goes_back_once_with_the_reason(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    upload(api, cookie, "final.png", PHOTO, "image/png")
    since = received_so_far(fake_openai)
    unquoted = {**CARS, "clues": [{"kind": "text", "statement": "The court boards read PORSCHE"}]}
    sees(fake_openai, unquoted, CARS)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [examine("final.png")])

    run_agent(api, cookie, QUESTION)

    first, second = looked_at(fake_openai, since)
    assert len(first["messages"]) == 2
    assert second["messages"][-1]["content"].startswith(
        "Your last answer was rejected: Value error, a text sighting must quote the writing in double quotes"
    )
    assert (
        f"{CARS['answer']} (from final.png, looked at again)"
        in chat_request(fake_openai)["messages"][0]["content"]
    )
