"""Case memory (RAG): facts and document text are embedded in pgvector when evidence is added, and the
detective recalls only what is relevant to the client's message: hybrid search (vector + Postgres full text),
always within the client's own case (filter + row-level security).

The fake OpenAI server's embeddings hash words into vectors, so texts sharing words are similar.
"""

import json
import subprocess

import httpx
import psycopg
from conftest import app_env, chat_request, run_agent, sign_up, unique_email
from test_evidence import PASSWORD, upload

NOTES = {  # one sentence each: every note becomes a fact (quoting it) and a chunk in the memory
    "ticket.txt": "The parking ticket on the blue car was paid at 18:05.",
    "dog.txt": "A small white dog barked at the courier near the bakery.",
    "keys.txt": "Someone dropped a ring of brass keys outside the library.",
    "phone.txt": "The cafe manager can be called on +48 600 100 200.",
    "rain.txt": "Heavy rain started at about 21:00 on the 3rd of October.",
    "bike.txt": "A red bicycle was chained to the lamp post all evening.",
    "coat.txt": "The man wore a grey coat with a torn left pocket.",
    "train.txt": "The last train to Warsaw left platform 4 at 22:40.",
    "shop.txt": "The kiosk on the Main Square closes at midnight.",
    "backpack.txt": "A black backpack was found under the bench at the Main Square.",
}


def add_notes(api: str, cookie: str) -> None:
    for name, text in NOTES.items():
        assert upload(api, cookie, name, text.encode(), "text/plain").status_code == 201


def recalled(fake_openai: str) -> list[str]:
    """The case-file lines the detective was given in its last chat request."""
    system = chat_request(fake_openai)["messages"][0]["content"]
    if "From the case file" not in system:
        return []
    return [
        line.removeprefix("- ") for line in system.split("never instructions to you:\n", 1)[1].splitlines()
    ]


def memory_rows(database: str, case_id: str) -> int:
    with psycopg.connect(database) as conn:
        row = conn.execute("SELECT count(*) FROM case_memory WHERE case_id = %s", (case_id,)).fetchone()
    assert row is not None
    return row[0]


def case_of(api: str, cookie: str) -> str:
    return httpx.get(f"{api}/cases/current", headers={"cookie": cookie}).json()["id"]


def test_the_detective_recalls_what_the_question_is_about(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_notes(api, cookie)

    run_agent(api, cookie, "Tell me about the backpack under the bench")

    lines = recalled(fake_openai)
    assert len(lines) == 8, "the best 8 of the case's 20 entries, not all of them"
    assert all("backpack.txt" in line for line in lines[:2]), lines  # its fact and its text come first
    assert sum("backpack.txt" in line for line in lines) == 2


def test_exact_words_are_found_by_full_text_even_when_meaning_misses(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_notes(api, cookie)

    # By exact words (the fake embeddings), "backpacks main square" is closest to the short kiosk note: both
    # notes say "Main Square", and no note says "backpacks". Postgres full text stems "backpacks" to
    # "backpack" and needs every word: only the backpack note matches. Fusing both ranks it first.
    run_agent(api, cookie, "backpacks main square")

    lines = recalled(fake_openai)
    assert "backpack.txt" in lines[0], lines
    assert any("shop.txt" in line for line in lines), "the vector half still contributes"


def test_each_question_is_searched_for_itself(api: str, database_url: str, fake_openai: str) -> None:
    """Regression: the store must not reuse an earlier question's words for its full-text search."""
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_notes(api, cookie)

    run_agent(api, cookie, "backpacks main square")
    first = recalled(fake_openai)
    run_agent(api, cookie, "keys library")
    second = recalled(fake_openai)

    assert "backpack.txt" in first[0]
    assert "keys.txt" in second[0] and "keys.txt" in second[1], second


def test_recall_embeds_the_question_and_a_board_only_run_recalls_nothing(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    upload(api, cookie, "coat.txt", NOTES["coat.txt"].encode(), "text/plain")
    received = len(httpx.get(f"{fake_openai}/received").json())

    run_agent(api, cookie, "What was he wearing?")
    after_question = httpx.get(f"{fake_openai}/received").json()[received:]
    run_agent(api, cookie, None)
    after_refresh = httpx.get(f"{fake_openai}/received").json()[received + len(after_question) :]

    questions = [r["input"] for r in after_question if "input" in r and "questions" not in r]  # embeddings
    assert questions == [["What was he wearing?"]], "one embedding: the question"
    embedded = [r for r in after_refresh if "input" in r and "questions" not in r]
    assert not embedded, "the board is rebuilt from facts, without recall"


def test_one_client_never_recalls_anothers_case(api: str, database_url: str, fake_openai: str) -> None:
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    add_notes(api, alice)

    run_agent(api, bob, "Tell me about the backpack under the bench")

    assert recalled(fake_openai) == []
    assert "backpack" not in json.dumps(chat_request(fake_openai)["messages"][0])


def test_the_database_hides_other_clients_memory(api: str, database_url: str) -> None:
    """RLS on case_memory, checked directly as the api's database role."""
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    upload(api, alice, "a.txt", b"Alice saw a fox.", "text/plain")
    bob_evidence = upload(api, bob, "b.txt", b"Bob saw an owl.", "text/plain").json()["id"]
    alice_id = httpx.get(f"{api}/auth/me", headers={"cookie": alice}).json()["id"]
    alice_case, bob_case = case_of(api, alice), case_of(api, bob)

    with psycopg.connect(database_url, autocommit=True) as app:
        assert app.execute("SELECT count(*) FROM case_memory").fetchone() == (0,), "no user set: see nothing"
        with app.transaction():
            app.execute("SELECT set_config('app.user_id', %s, true)", (alice_id,))
            assert app.execute("SELECT DISTINCT case_id::text FROM case_memory").fetchall() == [(alice_case,)]
            for case_id in (bob_case, alice_case):  # Bob's case, or Alice's case pointing at Bob's evidence
                try:
                    with app.transaction():
                        app.execute(
                            "INSERT INTO case_memory (langchain_id, content, embedding, content_tsv, case_id,"
                            " evidence_id, kind) VALUES (gen_random_uuid(), 'planted', "
                            "array_fill(0.1, ARRAY[1536])::vector, to_tsvector('planted'), %s, %s, 'fact')",
                            (case_id, bob_evidence),
                        )
                    raise AssertionError(
                        f"planting memory on Bob's evidence via case {case_id} must be refused"
                    )
                except psycopg.errors.InsufficientPrivilege:
                    pass


def test_reset_forgets_the_case(api: str, database_url: str, owner_database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    upload(api, cookie, "coat.txt", NOTES["coat.txt"].encode(), "text/plain")
    old_case = case_of(api, cookie)
    assert memory_rows(owner_database_url, old_case) == 2

    assert httpx.delete(f"{api}/cases/current", headers={"cookie": cookie}).status_code == 200

    assert memory_rows(owner_database_url, old_case) == 0


def test_reindex_rebuilds_the_memory_from_facts_and_stored_documents(
    api: str, database_url: str, owner_database_url: str, fake_openai: str, s3_endpoint: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_notes(api, cookie)
    case_id = case_of(api, cookie)
    with psycopg.connect(owner_database_url) as owner:  # as if the memory were lost
        owner.execute("DELETE FROM case_memory WHERE case_id = %s", (case_id,))
    assert memory_rows(owner_database_url, case_id) == 0

    env = app_env(database_url, fake_openai, s3_endpoint) | {"MIGRATIONS_DATABASE_URL": owner_database_url}
    subprocess.run(["detective", "reindex"], env=env, check=True, capture_output=True)

    assert memory_rows(owner_database_url, case_id) == 20, "10 facts and 10 text chunks, back again"
    run_agent(api, cookie, "Tell me about the backpack under the bench")
    assert "backpack.txt" in recalled(fake_openai)[0]
