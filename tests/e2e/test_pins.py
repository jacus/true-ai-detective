"""Map pins are evidence the detective can use: each pin is a location fact, recalled from the case memory and
placed on the board like any other fact."""

import os
import subprocess

import httpx
from conftest import APP_DB_PASSWORD, run_agent, sign_up, unique_email
from test_evidence import EVIDENCE, PASSWORD, listed
from test_memory import recalled

CEREMONY = 'The client marked "Ceremony" on the map at 50.06143, 19.93658'


def add_pin(api: str, cookie: str, label: str = "Ceremony") -> httpx.Response:
    return httpx.post(
        f"{api}{EVIDENCE}/pins",
        json={"label": label, "latitude": 50.06143, "longitude": 19.93658},
        headers={"cookie": cookie},
    )


def test_a_pin_is_a_location_fact(api: str, database_url: str) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)

    pin = add_pin(api, cookie)

    assert pin.status_code == 201, pin.text
    assert pin.json()["facts"] == [
        {"kind": "location", "statement": CEREMONY, "quote": None, "location": None}
    ]


def test_the_detective_recalls_a_pin_and_the_board_places_it(
    api: str, database_url: str, fake_openai: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_pin(api, cookie)

    run_agent(api, cookie, "Where was the ceremony?")

    assert recalled(fake_openai) == [f"{CEREMONY} (from Ceremony)"]
    board = httpx.get(f"{api}/cases/current/board", headers={"cookie": cookie}).json()
    assert [moment["source"] for moment in board["timeline"]] == [f"{CEREMONY} (from Ceremony)"]


def test_pins_added_before_get_their_fact_from_the_migration(
    api: str, database_url: str, owner_database_url: str
) -> None:
    cookie = sign_up(api, database_url, unique_email(), PASSWORD)
    add_pin(api, cookie)
    env = {**os.environ, "MIGRATIONS_DATABASE_URL": owner_database_url, "APP_DB_PASSWORD": APP_DB_PASSWORD}

    # Down removes every pin's fact (as if the pin predated this change); up restores it in the api's words.
    subprocess.run(["alembic", "downgrade", "-1"], env=env, check=True, capture_output=True)
    assert listed(api, cookie)[0]["facts"] == []
    subprocess.run(["alembic", "upgrade", "head"], env=env, check=True, capture_output=True)

    assert [f["statement"] for f in listed(api, cookie)[0]["facts"]] == [CEREMONY]
