import httpx
import psycopg
from conftest import _stop, model_saw, run_agent, sign_up, start_api, unique_email
from fake_openai import reply_for

PASSWORD = "correct horse battery staple"


def history(api: str, cookie: str) -> list[tuple[str, str]]:
    messages = httpx.get(f"{api}/cases/current/messages", headers={"cookie": cookie}).json()
    return [(m["role"], m["content"]) for m in messages]


def test_the_conversation_survives_an_api_restart(database_url: str, fake_openai: str) -> None:
    proc, api = start_api(database_url, fake_openai)
    try:
        cookie = sign_up(api, database_url, unique_email(), PASSWORD)
        run_agent(api, cookie, "The thief wore red boots")
    finally:
        _stop(proc)

    proc, api = start_api(database_url, fake_openai)  # a fresh process: nothing in memory
    try:
        assert history(api, cookie) == [
            ("user", "The thief wore red boots"),
            ("assistant", reply_for("The thief wore red boots")),
        ]
        run_agent(api, cookie, "Anything new?")
        assert "red boots" in model_saw(fake_openai), "the detective remembers the case"
    finally:
        _stop(proc)


def test_reset_deletes_the_case_for_good(
    api: str, database_url: str, owner_database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    run_agent(api, cookie, "The safe code is 7-3-1")
    old_case = httpx.get(f"{api}/cases/current", headers={"cookie": cookie}).json()["id"]

    reset = httpx.delete(f"{api}/cases/current", headers={"cookie": cookie})

    assert reset.status_code == 200
    assert reset.json()["id"] != old_case
    assert history(api, cookie) == []
    with psycopg.connect(owner_database_url) as owner:  # below RLS: is anything left anywhere?
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            count = owner.execute(
                f"SELECT count(*) FROM {table} WHERE thread_id = %s", (old_case,)
            ).fetchone()
            assert count == (0,), f"{table} still holds the deleted case"
        assert owner.execute("SELECT count(*) FROM cases WHERE id = %s", (old_case,)).fetchone() == (0,)
    run_agent(api, cookie, "Hello again")
    assert "7-3-1" not in model_saw(fake_openai)


def test_history_and_reset_need_a_login(api: str) -> None:
    assert httpx.get(f"{api}/cases/current/messages").status_code == 401
    assert httpx.delete(f"{api}/cases/current").status_code == 401


def test_the_database_hides_other_clients_checkpoints(api: str, database_url: str) -> None:
    """RLS on LangGraph's own checkpoint tables, checked directly as the api's database role."""
    alice = sign_up(api, database_url, unique_email("alice"), PASSWORD)
    bob = sign_up(api, database_url, unique_email("bob"), PASSWORD)
    run_agent(api, alice, "Alice's secret lead")
    run_agent(api, bob, "Bob's own lead")
    alice_me = httpx.get(f"{api}/auth/me", headers={"cookie": alice}).json()
    alice_case = httpx.get(f"{api}/cases/current", headers={"cookie": alice}).json()["id"]
    bob_case = httpx.get(f"{api}/cases/current", headers={"cookie": bob}).json()["id"]
    assert ("user", "Alice's secret lead") not in history(api, bob)

    with psycopg.connect(database_url, autocommit=True) as app:
        assert app.execute("SELECT count(*) FROM checkpoints").fetchone() == (0,), "no user set: see nothing"
        with app.transaction():
            app.execute("SELECT set_config('app.user_id', %s, true)", (alice_me["id"],))
            threads = app.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
            assert threads == [(alice_case,)]
            deleted = app.execute("DELETE FROM checkpoints WHERE thread_id = %s", (bob_case,)).rowcount
            assert deleted == 0
    assert ("user", "Bob's own lead") in history(api, bob)
