import httpx
from fake_openai import reply_for
from playwright.sync_api import Page, expect


def test_visitor_chats_with_the_detective_in_the_browser(page: Page, web: str) -> None:
    # networkidle: the chat has discovered the detective agent via the runtime before we type.
    page.goto(web, wait_until="networkidle")
    expect(page.get_by_text("True AI Detective. Tell me about your case.")).to_be_visible()

    message_box = page.get_by_placeholder("Describe what happened…")
    message_box.fill("My bike was stolen from the train station")
    message_box.press("Enter")

    # Browser → Next.js CopilotRuntime → FastAPI /agent (AG-UI) → LangGraph → (fake) model, and back.
    expect(page.get_by_text(reply_for("My bike was stolen from the train station"))).to_be_visible(
        timeout=15_000
    )


def test_web_runtime_exposes_the_detective_with_telemetry_off(web: str) -> None:
    info = httpx.get(f"{web}/api/copilotkit/info").json()

    assert "detective" in info["agents"]
    assert info["telemetryDisabled"] is True
