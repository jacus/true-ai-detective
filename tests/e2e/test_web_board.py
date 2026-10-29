"""The case board in the browser: CopilotKit shared state, streamed from the detective graph over AG-UI."""

import httpx
from conftest import sign_up, unique_email
from playwright.sync_api import Page, expect
from test_web_chat import PASSWORD, log_in

NOTE = "The document says: The umbrella was green. (from note.txt)"


def test_new_evidence_updates_the_board_without_a_reload(
    page: Page, web: str, api: str, database_url: str
) -> None:
    page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)
    log_in(page, web, email)
    board = page.get_by_role("region", name="Case board")
    timeline = board.get_by_role("list", name="Timeline")
    expect(timeline).to_have_text("Nothing dated yet.")

    # Evidence goes in through the locker (REST); the board then updates via an agent run (AG-UI state).
    locker = page.get_by_role("complementary", name="Evidence locker")
    locker.get_by_label("File or photo").set_input_files(
        files=[{"name": "note.txt", "mimeType": "text/plain", "buffer": b"The umbrella was green."}]
    )
    locker.get_by_role("button", name="Upload").click()

    expect(timeline.get_by_role("listitem")).to_have_text(["as reported " + NOTE[:80]])
    expect(board.get_by_role("list", name="Leads")).to_have_text("Follow up on F1 newest evidence")
    expect(page.get_by_test_id("copilot-assistant-message")).to_have_count(0)  # no chat reply for evidence

    # A map pin is a fact too: it lands on the board the same way.
    locker.get_by_test_id("pin-map").click()
    locker.get_by_label("Pin label").fill("Fence")
    locker.get_by_role("button", name="Add pin").click()
    expect(timeline.get_by_role("listitem")).to_have_count(2)
    expect(timeline.get_by_role("listitem").nth(1)).to_contain_text('The client marked "Fence" on the map at')

    # A chat turn rebuilds it too, and the case keeps it across a reload.
    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("It was taken yesterday.")
    message_box.press("Enter")
    expect(page.get_by_test_id("copilot-assistant-message")).to_have_count(1)
    page.reload()
    expect(timeline.get_by_role("listitem")).to_have_count(2)
    expect(timeline.get_by_role("listitem").first).to_have_text("as reported " + NOTE[:80])

    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Reset case").click()
    expect(timeline).to_have_text("Nothing dated yet.")


def test_the_board_shows_the_detectives_last_steps(
    page: Page, web: str, api: str, database_url: str, fake_openai: str
) -> None:
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)
    log_in(page, web, email)
    httpx.post(f"{fake_openai}/decisions", json=[{"choice": "research"}, {"choice": "conclude"}])
    page.wait_for_load_state("networkidle")  # the chat must discover the agent before we type

    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("Who took my umbrella?")
    message_box.press("Enter")
    expect(page.get_by_test_id("copilot-assistant-message")).to_have_count(1)

    # The loop's trail streams in with the state: researched once, then concluded (the fake is 90% sure).
    last_step = page.get_by_role("region", name="Case board").get_by_role(
        "status", name="Detective's last step"
    )
    expect(last_step).to_have_text("Detective's last step: research → conclude (90%)")
