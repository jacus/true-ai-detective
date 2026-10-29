"""Evidence locker over HTTP: files (photos too), links and map pins on a client's case.

Files live in the S3 bucket; downloads are short-lived presigned URLs the api hands out (as a redirect)
only after row-level security found the evidence in the caller's own case.
"""

import base64
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
from conftest import S3_ENV, sign_up, storage, unique_email

PASSWORD = "correct horse battery staple"
# A real 1x1 PNG, so browsers and servers treat it as a photo.
PHOTO = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
EVIDENCE = "/cases/current/evidence"


def upload(api: str, cookie: str, name: str, content: bytes, content_type: str) -> httpx.Response:
    return httpx.post(
        f"{api}{EVIDENCE}/files", files={"file": (name, content, content_type)}, headers={"cookie": cookie}
    )


def listed(api: str, cookie: str) -> list[dict]:
    response = httpx.get(f"{api}{EVIDENCE}", headers={"cookie": cookie})
    assert response.status_code == 200, response.text
    return response.json()


def presigned_url(api: str, cookie: str, evidence_id: str) -> str:
    response = httpx.get(f"{api}{EVIDENCE}/{evidence_id}/file", headers={"cookie": cookie})
    assert response.status_code == 307, response.text
    return response.headers["location"]


def test_a_file_is_uploaded_listed_and_downloaded_via_a_presigned_url(api: str, database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    content = "Receipt: 2 coffees, Café Nero, 09:14\n".encode()

    created = upload(api, cookie, "receipt ż.txt", content, "text/plain")

    assert created.status_code == 201, created.text
    evidence = created.json()
    assert evidence | {"id": None, "created_at": None} == {
        "id": None,
        "kind": "file",
        "label": "receipt ż.txt",
        "content_type": "text/plain",
        "size_bytes": len(content),
        "url": None,
        "latitude": None,
        "longitude": None,
        "created_at": None,
        # Text is read as a document (S9): the fake model quotes its first sentence.
        "facts": [
            {
                "kind": "event",
                "statement": "The document says: Receipt: 2 coffees, Café Nero, 09:14",
                "quote": "Receipt: 2 coffees, Café Nero, 09:14",
                "location": None,
            }
        ],
    }
    assert [e["id"] for e in listed(api, cookie)] == [evidence["id"]]

    url = presigned_url(api, cookie, evidence["id"])
    assert "X-Amz-Signature=" in url and "X-Amz-Expires=300" in url
    download = httpx.get(url)
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-disposition"] == "attachment; filename*=UTF-8''receipt%20%C5%BC.txt"


def test_photos_open_inline_and_other_files_download(api: str, database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    photo = upload(api, cookie, "scene.png", PHOTO, "image/png").json()
    page = upload(api, cookie, "page.html", b"<script>alert(1)</script>", "text/html").json()

    shown = httpx.get(presigned_url(api, cookie, photo["id"]))
    saved = httpx.get(presigned_url(api, cookie, page["id"]))

    assert shown.content == PHOTO
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline;")
    # Anything but a plain photo is never rendered by the browser, only saved.
    assert saved.headers["content-disposition"].startswith("attachment;")


def test_links_and_map_pins_are_kept_with_the_case(api: str, database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)

    link = httpx.post(
        f"{api}{EVIDENCE}/links",
        json={"url": "https://example.com/lost-umbrella"},
        headers={"cookie": cookie},
    )
    pin = httpx.post(
        f"{api}{EVIDENCE}/pins",
        json={"label": "Last seen here", "latitude": 52.2297, "longitude": 21.0122},
        headers={"cookie": cookie},
    )

    assert link.status_code == 201, link.text
    assert pin.status_code == 201, pin.text
    assert [
        (e["kind"], e["label"], e["url"], e["latitude"], e["longitude"]) for e in listed(api, cookie)
    ] == [
        ("link", "https://example.com/lost-umbrella", "https://example.com/lost-umbrella", None, None),
        ("pin", "Last seen here", None, 52.2297, 21.0122),
    ]
    # Only files have something to download.
    no_file = httpx.get(f"{api}{EVIDENCE}/{link.json()['id']}/file", headers={"cookie": cookie})
    assert no_file.status_code == 404


def test_bad_evidence_is_refused(api: str, database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)

    def post(kind: str, body: dict) -> int:
        return httpx.post(f"{api}{EVIDENCE}/{kind}", json=body, headers={"cookie": cookie}).status_code

    assert post("links", {"url": "javascript:alert(1)"}) == 422  # rendered as a link later: http(s) only
    assert post("links", {"url": "file:///etc/passwd"}) == 422
    assert post("pins", {"label": "Nowhere", "latitude": 91, "longitude": 0}) == 422
    assert post("pins", {"label": "Nowhere", "latitude": 0, "longitude": -181}) == 422
    assert post("pins", {"label": "", "latitude": 0, "longitude": 0}) == 422
    too_big = upload(api, cookie, "huge.bin", b"x" * (10 * 1024 * 1024 + 1), "application/octet-stream")
    assert too_big.status_code == 413
    assert upload(api, cookie, "empty.txt", b"", "text/plain").status_code == 422
    assert listed(api, cookie) == []


def test_another_clients_evidence_cannot_be_listed_or_downloaded(
    api: str, database_url: str, s3_endpoint: str
) -> None:
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    secret = upload(api, alice, "alibi.txt", b"Alice's alibi", "text/plain").json()
    bobs = upload(api, bob, "note.txt", b"Bob's note", "text/plain").json()

    assert [e["id"] for e in listed(api, bob)] == [bobs["id"]]
    assert httpx.get(f"{api}{EVIDENCE}/{secret['id']}/file", headers={"cookie": bob}).status_code == 404

    # The bucket is private: guessing the object's address (or reusing Bob's signature for it) gets nothing.
    alices_url = urlsplit(presigned_url(api, alice, secret["id"]))
    bobs_url = urlsplit(presigned_url(api, bob, bobs["id"]))
    assert httpx.get(urlunsplit(alices_url._replace(query=""))).status_code == 403
    assert httpx.get(urlunsplit(bobs_url._replace(path=alices_url.path))).status_code == 403
    # Files are stored under their case, never under a name the client chose.
    keys = [
        o["Key"] for o in storage(s3_endpoint).list_objects_v2(Bucket=S3_ENV["EVIDENCE_BUCKET"])["Contents"]
    ]
    alice_case = httpx.get(f"{api}/cases/current", headers={"cookie": alice}).json()["id"]
    assert f"cases/{alice_case}/{secret['id']}" in keys


def test_the_evidence_locker_needs_a_login(api: str) -> None:
    assert httpx.get(f"{api}{EVIDENCE}").status_code == 401
    assert upload(api, "detective_session=forged", "x.txt", b"x", "text/plain").status_code == 401


def test_the_database_hides_other_clients_evidence(api: str, database_url: str) -> None:
    """RLS on the evidence table, checked directly as the api's database role."""
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    upload(api, alice, "a.txt", b"a", "text/plain")
    upload(api, bob, "b.txt", b"b", "text/plain")
    alice_id = httpx.get(f"{api}/auth/me", headers={"cookie": alice}).json()["id"]
    bob_case = httpx.get(f"{api}/cases/current", headers={"cookie": bob}).json()["id"]

    with psycopg.connect(database_url, autocommit=True) as app:
        assert app.execute("SELECT count(*) FROM evidence").fetchone() == (0,), "no user set: see nothing"
        with app.transaction():
            app.execute("SELECT set_config('app.user_id', %s, true)", (alice_id,))
            assert app.execute("SELECT label FROM evidence").fetchall() == [("a.txt",)]
            try:
                with app.transaction():
                    app.execute(
                        "INSERT INTO evidence (id, case_id, kind, label, url) "
                        "VALUES (gen_random_uuid(), %s, 'link', 'planted', 'https://example.com')",
                        (bob_case,),
                    )
                raise AssertionError("inserting into another client's case must be refused")
            except psycopg.errors.InsufficientPrivilege:
                pass


def test_reset_deletes_the_evidence_and_its_files_for_good(
    api: str, database_url: str, owner_database_url: str, s3_endpoint: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    upload(api, cookie, "scene.png", PHOTO, "image/png")
    httpx.post(
        f"{api}{EVIDENCE}/pins",
        json={"label": "x", "latitude": 1, "longitude": 2},
        headers={"cookie": cookie},
    )
    old_case = httpx.get(f"{api}/cases/current", headers={"cookie": cookie}).json()["id"]

    assert httpx.delete(f"{api}/cases/current", headers={"cookie": cookie}).status_code == 200

    assert listed(api, cookie) == []
    left = storage(s3_endpoint).list_objects_v2(Bucket=S3_ENV["EVIDENCE_BUCKET"], Prefix=f"cases/{old_case}/")
    assert left["KeyCount"] == 0, "the bucket still holds the deleted case's files"
    with psycopg.connect(owner_database_url) as owner:  # below RLS
        assert owner.execute("SELECT count(*) FROM evidence WHERE case_id = %s", (old_case,)).fetchone() == (
            0,
        )
