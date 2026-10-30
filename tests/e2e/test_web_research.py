"""Web research in the browser: what the detective finds shows up in the evidence locker, with its provenance,
as soon as the chat turn ends; a picture it kept shows as a picture (the web is replayed from the research
cassette, see test_research.py and test_image_research.py)."""

import re

from conftest import sign_up, unique_email
from playwright.sync_api import Page, expect
from test_image_research import ARENA, QUESTION, examine
from test_loop import queue, queue_tool_calls
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
