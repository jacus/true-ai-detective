"""Web research (S13): the detective calls tools, and what it finds on the web is filed in the evidence locker
with its provenance, read into cited facts, recalled later and placed on the board. A search's list of
results isn't filed (S13d): the pages read are.

The model's tool calls are scripted (the fake OpenAI server); the tools are real code making real HTTP
requests, answered from responses recorded once from the real web and a real SearXNG
(tests/e2e/cassettes/research.yaml, `just record-research`), or by a fake SearXNG whose engines didn't answer.
"""

import json
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from conftest import _stop, app_env, chat_request, run_agent, sign_up, start_api, unique_email
from test_evidence import PASSWORD, listed
from test_loop import final_state, queue, queue_tool_calls, received_so_far
from test_memory import case_of, memory_rows, recalled

TOOLS = [
    "search_case_file",
    "web_search",
    "read_web_page",
    "read_archived_page",
    "domain_registration",
    "dns_records",
    "image_search",
    "examine_image",
]
# One research round: the model asks for all five at once, and they run in parallel.
EXAMPLE_COM: list[dict[str, Any]] = [
    {"name": "web_search", "args": {"query": "example.com domain owner"}},
    {"name": "read_web_page", "args": {"url": "https://example.com/"}},
    {"name": "read_archived_page", "args": {"url": "https://example.com/", "around": "2015-01-01"}},
    {"name": "domain_registration", "args": {"domain": "example.com"}},
    {"name": "dns_records", "args": {"name": "example.com"}},
]
REGISTRAR = "RESERVED-Internet Assigned Numbers Authority"  # in the recorded RDAP answer
IN_2015 = "established to be used for illustrative examples"  # the archived 2015 page; today's says otherwise
MISSING = "https://example.com/no-such-page"  # recorded answering 404 Not Found


def research_example_com(api: str, cookie: str, fake_openai: str) -> list[dict]:
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, EXAMPLE_COM)
    return run_agent(api, cookie, "Who owns example.com, and what did its page say back in 2015?")


def findings(api: str, cookie: str) -> dict[str, dict]:
    return {item["tool"]: item for item in listed(api, cookie) if item["kind"] == "finding"}


def test_the_detective_researches_the_web_and_files_what_it_found(
    recorded_api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(recorded_api, database_url, unique_email(), PASSWORD)
    since = received_so_far(fake_openai)

    events = research_example_com(recorded_api, cookie, fake_openai)

    assert final_state(events)["steps"] == ["research", "conclude"]
    # The research agent: the model is offered its plan tool and every research tool, picks freely, and is
    # asked for a structured report when it is done (OpenAI's JSON schema, alongside the tools).
    asked = [r for r in httpx.get(f"{fake_openai}/received").json()[since:] if r.get("tools")]
    assert [t["function"]["name"] for t in asked[0]["tools"]] == ["write_todos", *TOOLS]
    assert "tool_choice" not in asked[0]
    assert asked[0]["response_format"]["json_schema"]["name"] == "ResearchReport"
    assert len(asked) == 2, "one round of tools, then the report"
    # Each web result is filed with its provenance: the tool, what it was asked, its source, when. The
    # search's list of results isn't: it is what to read next, and the agent had it.
    filed = findings(recorded_api, cookie)
    read = [call for call in EXAMPLE_COM if call["name"] != "web_search"]
    assert sorted(filed) == sorted(call["name"] for call in read)
    for call in read:
        finding = filed[call["name"]]
        assert finding["tool_input"] == call["args"]
        assert finding["created_at"] and finding["facts"], finding  # read into facts like any document
    assert filed["read_web_page"]["url"] == "https://example.com/"
    assert filed["read_archived_page"]["url"].startswith("https://web.archive.org/web/2015")
    assert filed["domain_registration"]["url"] == "https://rdap.verisign.com/com/v1/domain/example.com"
    [calls] = [m["tool_calls"] for m in asked[1]["messages"] if m.get("tool_calls")]
    [search] = [call["id"] for call in calls if call["function"]["name"] == "web_search"]
    [results] = [m["content"] for m in asked[1]["messages"] if m.get("tool_call_id") == search]
    assert "\nhttps://" in results, "the agent saw the search's results"
    # The answer is written from what was found, each line naming the finding it came from.
    system = chat_request(fake_openai)["messages"][0]["content"]
    assert REGISTRAR in system and IN_2015 in system and '"v=spf1 -all"' in system
    assert "(from read_archived_page: https://example.com/, 2015-01-01)" in system


def test_a_finding_keeps_exactly_what_the_tool_returned(
    recorded_api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(recorded_api, database_url, unique_email(), PASSWORD)
    research_example_com(recorded_api, cookie, fake_openai)
    finding = findings(recorded_api, cookie)["domain_registration"]

    stored = httpx.get(
        f"{recorded_api}/cases/current/evidence/{finding['id']}/file",
        headers={"cookie": cookie},
        follow_redirects=True,  # to the presigned bucket URL
    )

    assert stored.status_code == 200 and stored.headers["content-type"].startswith("text/plain")
    assert stored.text.startswith("Domain: EXAMPLE.COM\n") and f"registrar: {REGISTRAR}" in stored.text
    assert len(stored.content) == finding["size_bytes"]


def test_what_was_found_is_recalled_later_and_on_the_board(
    recorded_api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(recorded_api, database_url, unique_email(), PASSWORD)
    events = research_example_com(recorded_api, cookie, fake_openai)

    run_agent(recorded_api, cookie, "Which registrar holds example.com?")  # the fake decides "ask"

    assert any("(from domain_registration: example.com)" in line for line in recalled(fake_openai))
    # The board was rebuilt at the end of the research run, from the facts filed during it.
    sources = [moment["source"] for moment in final_state(events)["board"]["timeline"]]
    assert any("(from domain_registration: example.com)" in source for source in sources), sources


def test_a_failing_tool_is_reported_and_nothing_is_filed(
    recorded_api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(recorded_api, database_url, unique_email(), PASSWORD)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [{"name": "read_web_page", "args": {"url": MISSING}}])

    events = run_agent(recorded_api, cookie, "What is on example.com/no-such-page?")

    assert final_state(events)["steps"] == ["research", "conclude"]  # the run went on
    assert (
        f"Nothing from read_web_page: {MISSING} (Couldn't get {MISSING}: Client error '404 Not Found'"
        in chat_request(fake_openai)["messages"][0]["content"]
    )
    assert any(e["type"] == "TEXT_MESSAGE_CONTENT" for e in events), "the detective still answered"
    assert findings(recorded_api, cookie) == {}


def test_findings_stay_in_the_clients_own_case(
    recorded_api: str, database_url: str, fake_openai: str, owner_database_url: str
) -> None:
    alice = sign_up(recorded_api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(recorded_api, database_url, unique_email("bob"), PASSWORD)
    research_example_com(recorded_api, alice, fake_openai)

    run_agent(recorded_api, bob, "Which registrar holds example.com?")

    assert listed(recorded_api, bob) == []
    assert recalled(fake_openai) == []
    with psycopg.connect(owner_database_url) as owner:
        cases = owner.execute(
            "SELECT DISTINCT case_id::text FROM evidence WHERE kind = 'finding' AND case_id IN (%s, %s)",
            (case_of(recorded_api, alice), case_of(recorded_api, bob)),
        )
        assert cases.fetchall() == [(case_of(recorded_api, alice),)]


def test_reindex_reads_findings_again(
    recorded_api: str,
    database_url: str,
    owner_database_url: str,
    fake_openai: str,
    s3_endpoint: str,
) -> None:
    cookie = sign_up(recorded_api, database_url, unique_email(), PASSWORD)
    research_example_com(recorded_api, cookie, fake_openai)
    case_id = case_of(recorded_api, cookie)
    indexed = memory_rows(owner_database_url, case_id)
    with psycopg.connect(owner_database_url) as owner:  # as if the memory were lost
        owner.execute("DELETE FROM case_memory WHERE case_id = %s", (case_id,))

    env = app_env(database_url, fake_openai, s3_endpoint) | {"MIGRATIONS_DATABASE_URL": owner_database_url}
    subprocess.run(["detective", "reindex"], env=env, check=True, capture_output=True)

    assert indexed > 5 and memory_rows(owner_database_url, case_id) == indexed  # facts and text, back again


@pytest.fixture
def blocked_searxng() -> Iterator[tuple[str, list[dict[str, list[str]]]]]:
    """A SearXNG whose engines all failed, as from Railway's shared addresses: no results, and which engines
    didn't answer and why. It notes each query."""
    asked: list[dict[str, list[str]]] = []

    class Blocked(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append(parse_qs(urlparse(self.path).query))
            down = [["brave", "too many requests"], ["duckduckgo", "CAPTCHA"]]
            body = json.dumps({"query": "", "results": [], "unresponsive_engines": down}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Blocked)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", asked
    server.shutdown()


def test_a_search_says_when_the_engines_didnt_answer(
    database_url: str,
    fake_openai: str,
    s3_endpoint: str,
    blocked_searxng: tuple[str, list[dict[str, list[str]]]],
) -> None:
    searxng, asked = blocked_searxng
    proc, api = start_api(database_url, fake_openai, s3_endpoint, env={"SEARX_URL": searxng})
    try:
        cookie = sign_up(api, database_url, unique_email(), PASSWORD)
        queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
        queue_tool_calls(
            fake_openai,
            [
                {"name": "web_search", "args": {"query": "Wuhan Open car"}},
                {"name": "image_search", "args": {"query": "Wuhan Open car"}},
            ],
        )

        run_agent(api, cookie, "Which car was on court at the Wuhan Open?")
        filed = findings(api, cookie)
    finally:
        _stop(proc)

    # Web search asks the news engines too (they answer from Railway when the general ones are blocked).
    assert sorted(query["categories"][0] for query in asked) == ["general,news", "images"]
    # Nothing found, and why: rewording won't help when the engines didn't answer.
    system = chat_request(fake_openai)["messages"][0]["content"]
    down = (
        "the search engines didn't answer (brave (too many requests), duckduckgo (CAPTCHA)). "
        "Rewording won't help"
    )
    assert f"Nothing from web_search: Wuhan Open car (No web results for 'Wuhan Open car': {down}" in system
    assert f"Nothing from image_search: Wuhan Open car (No pictures for 'Wuhan Open car': {down}" in system
    assert filed == {}
