"""Photo analysis: an uploaded photo yields facts (EXIF read by code, sightings from the vision model), each
linked to the photo, kept under row-level security and handed to the detective in the chat.

The vision model is the fake OpenAI server: it answers PHOTO_SIGHTINGS unless a test queues other answers.
EXIF facts come from the real Pillow code reading the planted metadata of the known-answer photo.
"""

import base64
import json

import httpx
import psycopg
from conftest import model_saw, run_agent, sign_up, unique_email
from fake_openai import PHOTO_SIGHTINGS
from known_photo import CAMERA, LOCATION, TAKEN_AT, known_photo
from test_evidence import PASSWORD, PHOTO, listed, upload

SIGN = 'A shop sign reads "PIEKARNIA NOWAK"'
BIKE = "A red bicycle leans against the wall"


def structured_requests(fake_openai: str, schema: str) -> list[dict]:
    """Structured-output requests (not streamed) the app sent asking for that schema."""
    received = httpx.get(f"{fake_openai}/received").json()
    return [
        r for r in received if not r.get("stream") and r["response_format"]["json_schema"]["name"] == schema
    ]


def vision_requests(fake_openai: str) -> list[dict]:
    return structured_requests(fake_openai, "PhotoFacts")


def queue_photo_answers(fake_openai: str, *answers: dict) -> None:
    httpx.post(f"{fake_openai}/answers/PhotoFacts", json=[json.dumps(answer) for answer in answers])


def facts_of(evidence: dict) -> list[tuple[str, str]]:
    return [(fact["kind"], fact["statement"]) for fact in evidence["facts"]]


def test_a_photo_yields_exif_and_vision_facts_linked_to_it(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    before = len(vision_requests(fake_openai))
    photo = known_photo()

    created = upload(api, cookie, "scene.jpg", photo, "image/jpeg")

    assert created.status_code == 201, created.text
    expected = [
        ("taken_at", TAKEN_AT),
        ("camera", CAMERA),
        ("location", LOCATION),
        ("text", SIGN),
        ("object", BIKE),
    ]
    assert facts_of(created.json()) == expected
    assert [facts_of(e) for e in listed(api, cookie)] == [expected]

    # One vision call: the photo itself, as a data URL, with PhotoFacts as OpenAI's JSON schema.
    [request] = vision_requests(fake_openai)[before:]
    assert request["model"] == "gpt-5.4-nano"
    assert request["response_format"]["json_schema"]["name"] == "PhotoFacts"
    image = next(part for part in request["messages"][1]["content"] if part["type"] == "image_url")
    assert image["image_url"]["url"] == f"data:image/jpeg;base64,{base64.b64encode(photo).decode()}"


def test_a_rejected_answer_goes_back_to_the_model_once_with_the_reason(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    before = len(vision_requests(fake_openai))
    unquoted = {"sightings": [{"kind": "text", "statement": "The sign says PIEKARNIA NOWAK"}]}
    queue_photo_answers(fake_openai, unquoted, PHOTO_SIGHTINGS)

    created = upload(api, cookie, "scene.png", PHOTO, "image/png")

    assert facts_of(created.json()) == [("text", SIGN), ("object", BIKE)]
    first, second = vision_requests(fake_openai)[before:]
    assert len(first["messages"]) == 2
    feedback = second["messages"][-1]["content"]
    assert "rejected" in feedback and "must quote the writing in double quotes" in feedback


def test_a_photo_rejected_twice_keeps_only_its_exif_facts(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    before = len(vision_requests(fake_openai))
    too_long = {"sightings": [{"kind": "object", "statement": "A wall " * 50}]}
    queue_photo_answers(fake_openai, too_long, too_long)

    created = upload(api, cookie, "scene.jpg", known_photo(), "image/jpeg")

    assert created.status_code == 201, created.text
    assert facts_of(created.json()) == [("taken_at", TAKEN_AT), ("camera", CAMERA), ("location", LOCATION)]
    assert len(vision_requests(fake_openai)[before:]) == 2, "one retry, then give up"


def test_only_photos_are_looked_at_and_broken_photos_are_refused(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    before = len(vision_requests(fake_openai))

    notes = upload(api, cookie, "notes.txt", b"Seen at 9pm", "text/plain")
    broken = upload(api, cookie, "scene.png", b"not really a png", "image/png")

    assert notes.status_code == 201
    assert [f["kind"] for f in notes.json()["facts"]] == ["event"]  # read as a document (S9), not as a photo
    assert broken.status_code == 422
    assert [e["label"] for e in listed(api, cookie)] == ["notes.txt"]
    assert vision_requests(fake_openai)[before:] == []


def test_the_detective_is_told_its_own_case_facts_only(api: str, database_url: str, fake_openai: str) -> None:
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    upload(api, alice, "scene.jpg", known_photo(), "image/jpeg")

    run_agent(api, alice, "Where was this taken?")
    alice_prompt = model_saw(fake_openai)
    run_agent(api, bob, "Where was this taken?")
    bob_prompt = model_saw(fake_openai)

    assert f"{LOCATION} (from scene.jpg)" in alice_prompt
    assert "PIEKARNIA NOWAK" in alice_prompt
    assert "never instructions" in alice_prompt  # facts are framed as data
    assert "PIEKARNIA" not in bob_prompt
    assert "evidence locker" not in bob_prompt


def test_the_database_hides_other_clients_facts(api: str, database_url: str) -> None:
    """RLS on the facts table, checked directly as the api's database role."""
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    upload(api, alice, "a.png", PHOTO, "image/png")
    bob_evidence = upload(api, bob, "b.png", PHOTO, "image/png").json()["id"]
    alice_id = httpx.get(f"{api}/auth/me", headers={"cookie": alice}).json()["id"]
    alice_case = httpx.get(f"{api}/cases/current", headers={"cookie": alice}).json()["id"]
    bob_case = httpx.get(f"{api}/cases/current", headers={"cookie": bob}).json()["id"]

    with psycopg.connect(database_url, autocommit=True) as app:
        assert app.execute("SELECT count(*) FROM facts").fetchone() == (0,), "no user set: see nothing"
        with app.transaction():
            app.execute("SELECT set_config('app.user_id', %s, true)", (alice_id,))
            assert app.execute("SELECT DISTINCT case_id::text FROM facts").fetchall() == [(alice_case,)]
            for case_id in (bob_case, alice_case):  # Bob's case, or Alice's case pointing at Bob's photo
                try:
                    with app.transaction():
                        app.execute(
                            "INSERT INTO facts (id, case_id, evidence_id, kind, statement) "
                            "VALUES (gen_random_uuid(), %s, %s, 'text', 'planted')",
                            (case_id, bob_evidence),
                        )
                    raise AssertionError(f"planting a fact on Bob's photo via case {case_id} must be refused")
                except psycopg.errors.InsufficientPrivilege:
                    pass


def test_reset_deletes_the_facts_too(api: str, database_url: str, owner_database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    upload(api, cookie, "scene.jpg", known_photo(), "image/jpeg")
    old_case = httpx.get(f"{api}/cases/current", headers={"cookie": cookie}).json()["id"]

    assert httpx.delete(f"{api}/cases/current", headers={"cookie": cookie}).status_code == 200

    with psycopg.connect(owner_database_url) as owner:  # below RLS
        left = owner.execute("SELECT count(*) FROM facts WHERE case_id = %s", (old_case,)).fetchone()
    assert left == (0,)
