import re

import httpx
from conftest import chat_request, invite, sign_up, unique_email
from fake_openai import reply_for
from playwright.sync_api import Browser, Page, expect

PASSWORD = "correct horse battery staple"


def log_in(page: Page, web: str, email: str) -> None:
    page.goto(f"{web}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()
    expect(page).to_have_url(f"{web}/")


def chat(page: Page, message: str) -> None:
    # networkidle: the chat has discovered the detective agent via the runtime before we type.
    page.wait_for_load_state("networkidle")
    box = page.get_by_placeholder("Describe what happened…")
    box.fill(message)
    box.press("Enter")
    # Browser → Next.js CopilotRuntime (+cookie) → FastAPI /agent → LangGraph → (fake) model, and back.
    expect(page.get_by_text(reply_for(message))).to_be_visible(timeout=15_000)


def test_invited_client_sets_a_password_and_chats(page: Page, web: str, database_url: str) -> None:
    token = invite(database_url, unique_email(), name="Ann Client")

    page.goto(f"{web}/invite/{token}")
    expect(page.get_by_role("heading", name="Welcome, Ann Client")).to_be_visible()
    page.get_by_label("Password (12+ characters)").fill(PASSWORD)
    page.get_by_label("Repeat password").fill(PASSWORD)
    page.get_by_role("button", name="Start").click()

    expect(page).to_have_url(f"{web}/")
    expect(page.get_by_text("Signed in as Ann Client")).to_be_visible()
    chat(page, "My bike was stolen from the train station")


def test_visitors_without_a_session_are_sent_to_login(page: Page, web: str) -> None:
    page.goto(web)

    expect(page).to_have_url(f"{web}/login")
    assert httpx.post(f"{web}/api/copilotkit/agent/detective/run", json={}).status_code != 200


def test_used_or_forged_invite_links_show_an_error(page: Page, web: str, api: str, database_url: str) -> None:
    token = invite(database_url, unique_email())
    httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": PASSWORD})

    for link in (f"{web}/invite/{token}", f"{web}/invite/forged-token"):
        page.goto(link)
        expect(page.get_by_text("This invite link is invalid or has expired.")).to_be_visible()


def test_returning_client_logs_in_and_out(page: Page, web: str, api: str, database_url: str) -> None:
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)

    log_in(page, web, email)
    chat(page, "I'm back with a new clue")
    page.get_by_role("button", name="Log out").click()

    expect(page).to_have_url(f"{web}/login")
    page.goto(web)
    expect(page).to_have_url(f"{web}/login")


def test_wrong_password_is_refused(page: Page, web: str, api: str, database_url: str) -> None:
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)

    page.goto(f"{web}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill("not my password")
    page.get_by_role("button", name="Log in").click()

    expect(page.get_by_text("Wrong email or password.")).to_be_visible()
    expect(page).to_have_url(f"{web}/login")


def test_owner_invites_a_client_who_then_chats(
    page: Page, browser: Browser, web: str, api: str, database_url: str
) -> None:
    owner_email = unique_email("owner")
    sign_up(api, database_url, owner_email, PASSWORD, role="owner")
    log_in(page, web, owner_email)

    page.get_by_role("link", name="Invite a client").click()
    page.get_by_label("Client's name").fill("Carol Client")
    page.get_by_label("Client's email").fill(unique_email())
    page.get_by_role("button", name="Create invite link").click()
    link = page.get_by_test_id("invite-link")
    expect(link).to_have_value(re.compile(rf"^{re.escape(web)}/invite/.+"))

    # The client opens the link in their own browser (no shared cookies with the owner).
    client_page = browser.new_context().new_page()
    client_page.goto(link.input_value())
    client_page.get_by_label("Password (12+ characters)").fill(PASSWORD)
    client_page.get_by_label("Repeat password").fill(PASSWORD)
    client_page.get_by_role("button", name="Start").click()
    expect(client_page.get_by_text("Signed in as Carol Client")).to_be_visible()
    chat(client_page, "Someone keeps moving my garden gnome")


def test_clients_cannot_open_the_owner_page(page: Page, web: str, api: str, database_url: str) -> None:
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)
    log_in(page, web, email)

    expect(page.get_by_role("link", name="Invite a client")).to_have_count(0)
    page.goto(f"{web}/owner")

    # The page streams (Partial Prerendering), so the 404 arrives as the not-found page, not a status code.
    # The api refuses non-owners with 403 regardless (test_auth.py).
    expect(page.get_by_text("This page could not be found.")).to_be_visible()
    expect(page.get_by_role("button", name="Create invite link")).to_have_count(0)


def test_web_runtime_exposes_the_detective_with_telemetry_off(web: str) -> None:
    info = httpx.get(f"{web}/api/copilotkit/info").json()

    assert "detective" in info["agents"]
    assert info["telemetryDisabled"] is True


def test_two_clients_chatting_never_see_each_others_case(
    browser: Browser, web: str, api: str, database_url: str, fake_openai: str
) -> None:
    alice_email, bob_email = unique_email("alice"), unique_email("bob")
    sign_up(api, database_url, alice_email, PASSWORD)
    sign_up(api, database_url, bob_email, PASSWORD)
    alice, bob = browser.new_context().new_page(), browser.new_context().new_page()
    log_in(alice, web, alice_email)
    log_in(bob, web, bob_email)

    chat(alice, "The safe code is 7-3-1")
    chat(bob, "Hello detective")

    expect(bob.get_by_text("7-3-1")).to_have_count(0)
    last_request = chat_request(fake_openai)["messages"]
    assert "7-3-1" not in str(last_request), "Bob's model call must not contain Alice's case"


def test_anonymous_callers_cannot_replay_a_case_from_the_web_runtime(
    page: Page, web: str, api: str, database_url: str
) -> None:
    email = unique_email("alice")
    cookie = sign_up(api, database_url, email, PASSWORD)
    log_in(page, web, email)
    chat(page, "The safe code is 7-3-1")
    case_id = httpx.get(f"{api}/cases/current", headers={"cookie": cookie}).json()["id"]

    # No cookie: ask the web's CopilotRuntime to replay Alice's thread.
    connect = {
        "threadId": case_id,
        "runId": "x",
        "state": {},
        "messages": [],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    response = httpx.post(f"{web}/api/copilotkit/agent/detective/connect", json=connect, timeout=15)

    assert response.status_code != 200
    assert "7-3-1" not in response.text


def test_the_conversation_is_still_there_after_a_reload(
    page: Page, web: str, api: str, database_url: str
) -> None:
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)
    log_in(page, web, email)
    chat(page, "The thief wore red boots")

    page.reload()

    # Before typing anything: the saved conversation comes back from the api.
    expect(page.get_by_text("The thief wore red boots", exact=True)).to_be_visible(timeout=15_000)
    expect(page.get_by_text(reply_for("The thief wore red boots"))).to_be_visible()


def test_reset_case_asks_first_then_wipes_the_conversation(
    page: Page, web: str, api: str, database_url: str
) -> None:
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)
    log_in(page, web, email)
    chat(page, "The safe code is 7-3-1")

    page.once("dialog", lambda dialog: dialog.dismiss())  # changed my mind
    page.get_by_role("button", name="Reset case").click()
    expect(page.get_by_text("The safe code is 7-3-1", exact=True)).to_be_visible()

    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Reset case").click()
    expect(page.get_by_text("The safe code is 7-3-1", exact=True)).to_have_count(0)

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("The safe code is 7-3-1", exact=True)).to_have_count(0)
    chat(page, "Starting over")
