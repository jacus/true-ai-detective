"""The case board in the browser: CopilotKit shared state, streamed from the detective graph over AG-UI."""

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

    # A chat turn rebuilds it too, and the case keeps it across a reload.
    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("It was taken yesterday.")
    message_box.press("Enter")
    expect(page.get_by_test_id("copilot-assistant-message")).to_have_count(1)
    page.reload()
    expect(timeline.get_by_role("listitem")).to_have_text(["as reported " + NOTE[:80]])

    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Reset case").click()
    expect(timeline).to_have_text("Nothing dated yet.")
