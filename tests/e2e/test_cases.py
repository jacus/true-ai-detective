import uuid

import httpx
import psycopg
import pytest
from conftest import model_saw, sign_up, unique_email
from conftest import run_agent as run

PASSWORD = "correct horse battery staple"


def test_a_client_cannot_read_or_continue_another_clients_case(
    api: str, database_url: str, fake_openai: str
) -> None:
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    alice_thread = run(api, alice, "My secret: the key is under the blue flowerpot")[0]["threadId"]

    # Bob replays Alice's thread id: the api must ignore it and use Bob's own case.
    events = run(api, bob, "What do you know so far?", thread_id=alice_thread)

    assert "flowerpot" not in model_saw(fake_openai)
    assert events[0]["threadId"] != alice_thread


def test_a_clients_conversation_continues_in_their_case(
    api: str, database_url: str, fake_openai: str
) -> None:
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    first = run(api, alice, "The thief wore red boots")[0]["threadId"]

    # A different browser thread id (e.g. a new tab) still lands in the same case.
    second = run(api, alice, "Anything new?")[0]["threadId"]

    assert first == second
    assert "red boots" in model_saw(fake_openai)


def test_each_client_has_exactly_one_case(api: str, database_url: str) -> None:
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)

    alice_case = httpx.get(f"{api}/cases/current", headers={"cookie": alice}).json()["id"]
    assert httpx.get(f"{api}/cases/current", headers={"cookie": alice}).json()["id"] == alice_case
    assert httpx.get(f"{api}/cases/current", headers={"cookie": bob}).json()["id"] != alice_case
    assert httpx.get(f"{api}/cases/current").status_code == 401


def test_the_database_itself_hides_other_clients_cases(owner_database_url: str, database_url: str) -> None:
    """Row-level security, checked below the app: a query without WHERE still only sees your own case."""
    alice, bob, carol = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    alice_case, bob_case = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(owner_database_url) as owner:
        for user_id in (alice, bob, carol):
            owner.execute(
                "INSERT INTO users (id, email, name, role, password_hash) VALUES (%s, %s, 'x', 'client', '')",
                (user_id, unique_email()),
            )
        owner.execute(
            "INSERT INTO cases (id, client_id) VALUES (%s, %s), (%s, %s)", (alice_case, alice, bob_case, bob)
        )

    with psycopg.connect(database_url, autocommit=True) as app:  # the api's own role
        assert app.execute("SELECT count(*) FROM cases").fetchone() == (0,), "no user set: see nothing"

        with app.transaction():
            app.execute("SELECT set_config('app.user_id', %s, true)", (str(alice),))
            assert app.execute("SELECT id FROM cases").fetchall() == [(alice_case,)]
            assert app.execute("UPDATE cases SET created_at = now() WHERE id = %s", (bob_case,)).rowcount == 0
            assert app.execute("DELETE FROM cases WHERE id = %s", (bob_case,)).rowcount == 0

        with pytest.raises(psycopg.errors.InsufficientPrivilege), app.transaction():
            app.execute("SELECT set_config('app.user_id', %s, true)", (str(alice),))
            app.execute("INSERT INTO cases (id, client_id) VALUES (%s, %s)", (uuid.uuid4(), carol))

    with psycopg.connect(owner_database_url) as owner:
        assert owner.execute("SELECT count(*) FROM cases WHERE id = %s", (bob_case,)).fetchone() == (1,)
