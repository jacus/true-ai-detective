"""Web research in the browser: what the detective finds shows up in the evidence locker, with its provenance,
as soon as the chat turn ends; a picture it kept shows as a picture (the web is replayed from the research
cassette, see test_research.py and test_image_research.py)."""

import re
import time

import httpx
from conftest import sign_up, unique_email
from playwright.sync_api import Page, expect
from test_evidence import PHOTO, upload
from test_image_research import ARENA, QUESTION, examine, looked_at
from test_loop import queue, queue_tool_calls, received_so_far
from test_research_agent import DONE, PLAN, search, write_todos
from test_web_chat import PASSWORD, log_in


def test_findings_appear_in_the_locker_with_their_provenance(
    page: Page, recorded_web: str, recorded_api: str, database_url: str, fake_openai: str
) -> None:
    page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
    email = unique_email()
    sign_up(recorded_api, database_url, email, PASSWORD)
    log_in(page, recorded_web, email)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [{"name": "domain_registration", "args": {"domain": "example.com"}}])
    page.wait_for_load_state("networkidle")  # the chat must discover the agent before we type

    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("Who registered example.com?")
    message_box.press("Enter")
    expect(page.get_by_test_id("copilot-assistant-message")).to_have_count(1)

    # No reload: the locker lists itself again when a run that researched ends.
    finding = page.get_by_role("complementary", name="Evidence locker").get_by_test_id("evidence-item")
    expect(finding).to_have_count(1)
    expect(finding.get_by_role("link", name="🔎 domain_registration: example.com")).to_be_visible()
    expect(finding).to_contain_text(
        re.compile(r"Found by the detective \(domain_registration\), \d{4}-\d\d-\d\d \d\d:\d\d UTC")
    )
    source = finding.get_by_role("link", name="source")
    expect(source).to_have_attribute("href", "https://rdap.verisign.com/com/v1/domain/example.com")
    expect(finding.get_by_role("list", name="Facts from domain_registration: example.com")).to_be_visible()


def test_a_picture_the_detective_kept_shows_in_the_locker(
    page: Page, recorded_web: str, recorded_api: str, database_url: str, fake_openai: str
) -> None:
    page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
    email = unique_email()
    sign_up(recorded_api, database_url, email, PASSWORD)
    log_in(page, recorded_web, email)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    queue_tool_calls(fake_openai, [examine(ARENA)])  # the fake sees what the question is about
    page.wait_for_load_state("networkidle")

    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill(QUESTION)
    message_box.press("Enter")
    expect(page.get_by_test_id("copilot-assistant-message")).to_have_count(1)

    kept = page.get_by_role("complementary", name="Evidence locker").get_by_test_id("evidence-item")
    expect(kept).to_have_count(1)
    label = f"examine_image: {ARENA}, {QUESTION}"
    picture = kept.get_by_role("img", name=label)
    assert picture.evaluate("img => img.decode().then(() => img.naturalWidth)") == 500  # the picture itself
    expect(kept).to_contain_text(f"🔎 {label}")
    expect(kept).to_contain_text(re.compile(r"Found by the detective \(examine_image\), \d{4}-\d\d-\d\d"))
    expect(kept.get_by_role("link", name="source")).to_have_attribute("href", ARENA)


def test_the_research_plan_shows_while_the_detective_researches(
    page: Page, web: str, api: str, database_url: str, fake_openai: str
) -> None:
    page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
    email = unique_email()
    cookie = sign_up(api, database_url, email, PASSWORD)
    upload(api, cookie, "final.png", PHOTO, "image/png")
    log_in(page, web, email)
    queue(fake_openai, {"choice": "research"}, {"choice": "conclude"})
    # Plan and search, tick the plan off, then look at the photo: its vision call is held for a while.
    queue_tool_calls(
        fake_openai,
        [write_todos(PLAN), search("keys")],
        [write_todos(DONE)],
        [examine("final.png")],
    )
    httpx.post(f"{fake_openai}/delay/ImageAnswer", json=8)
    since = received_so_far(fake_openai)
    page.wait_for_load_state("networkidle")

    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("Where are my keys?")
    message_box.press("Enter")

    # While the photo is being looked at (after the detective's own state went out again), the plan is
    # there, ticked off, and there is no answer yet (only the chat's in-progress bubble).
    deadline = time.monotonic() + 30
    while not looked_at(fake_openai, since):
        assert time.monotonic() < deadline, "the photo was never looked at"
        page.wait_for_timeout(200)
    plan = page.get_by_role("list", name="Research plan")
    expect(plan.get_by_role("listitem")).to_have_text([f"✓ {todo['content']}" for todo in DONE])
    expect(page.get_by_text("Detective on the case.")).to_have_count(0)  # the fake's reply: not yet
    # When the run ends it stays: it is in the detective's state now.
    expect(page.get_by_text("Detective on the case.")).to_have_count(1, timeout=30_000)
    expect(plan.get_by_role("listitem")).to_have_text([f"✓ {todo['content']}" for todo in DONE])
