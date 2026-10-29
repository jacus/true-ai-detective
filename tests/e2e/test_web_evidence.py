"""The evidence locker in the browser: real page → Next.js relay → api → S3 (presigned) and back."""

import re
from pathlib import Path

import httpx
from conftest import sign_up, unique_email
from playwright.sync_api import Page, expect
from test_evidence import PHOTO
from test_photo_analysis import BIKE, SIGN
from test_web_chat import PASSWORD, log_in


def test_client_attaches_a_photo_a_file_a_link_and_a_pin(
    page: Page, web: str, api: str, database_url: str
) -> None:
    page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())  # no real map tiles in tests
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)
    log_in(page, web, email)
    locker = page.get_by_role("complementary", name="Evidence locker")
    expect(locker.get_by_text("Nothing yet.")).to_be_visible()

    # A photo: shown as a picture the browser fetched from the bucket via a presigned URL.
    locker.get_by_label("File or photo").set_input_files(
        files=[{"name": "scene.png", "mimeType": "image/png", "buffer": PHOTO}]
    )
    locker.get_by_role("button", name="Upload").click()
    photo = locker.get_by_role("img", name="scene.png")
    assert photo.evaluate("img => img.decode().then(() => img.naturalWidth)") == 1
    # ...listed with what the (fake) vision model saw in it.
    facts = locker.get_by_role("list", name="Facts from scene.png").get_by_role("listitem")
    expect(facts).to_have_text([SIGN, BIKE])

    # Any other file: downloaded under its own name, same bytes.
    locker.get_by_label("File or photo").set_input_files(
        files=[{"name": "notes.txt", "mimeType": "text/plain", "buffer": b"Seen at 9pm by the bakery"}]
    )
    locker.get_by_role("button", name="Upload").click()
    with page.expect_download() as download:
        locker.get_by_role("link", name="notes.txt").click()
    assert download.value.suggested_filename == "notes.txt"
    assert Path(download.value.path()).read_bytes() == b"Seen at 9pm by the bakery"
    # ...and read as a document: the (fake) model's fact, with the quote it is based on.
    expect(locker.get_by_role("list", name="Facts from notes.txt")).to_have_text(
        "The document says: Seen at 9pm by the bakery Seen at 9pm by the bakery"
    )

    # A link.
    locker.get_by_label("Link").fill("https://example.com/lost-umbrella")
    locker.get_by_role("button", name="Add link").click()
    expect(locker.get_by_role("link", name="example.com/lost-umbrella")).to_be_visible()

    # A pin: click the map (it starts centred on 50°N 15°E), name the spot, add it.
    expect(locker.get_by_role("button", name="Add pin")).to_be_disabled()
    expect(locker.locator(".leaflet-container")).to_be_visible()
    locker.get_by_test_id("pin-map").click()
    locker.get_by_label("Pin label").fill("Last seen here")
    locker.get_by_role("button", name="Add pin").click()
    pinned = re.compile(r"📍 Last seen here \((49|50)\.\d{4}, (14|15)\.\d{4}\)")
    expect(locker.get_by_text(pinned)).to_be_visible()

    # Everything is kept with the case.
    page.reload()
    expect(locker.get_by_test_id("evidence-item")).to_have_count(4)
    expect(facts).to_have_text([SIGN, BIKE])
    assert locker.get_by_role("img", name="scene.png").evaluate("img => img.decode().then(() => 1)") == 1

    # Reset wipes the locker too.
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Reset case").click()
    expect(locker.get_by_text("Nothing yet.")).to_be_visible()
    expect(locker.get_by_test_id("evidence-item")).to_have_count(0)


def test_visitors_cannot_fetch_evidence_through_the_web(web: str) -> None:
    listing = httpx.get(f"{web}/api/cases/current/evidence")
    upload = httpx.post(f"{web}/api/cases/current/evidence/files", files={"file": ("x.txt", b"x")})

    assert listing.status_code == 401
    assert upload.status_code == 401
