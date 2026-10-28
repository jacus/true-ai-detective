import secrets

import httpx
from conftest import invite, session_cookie, sign_up, unique_email

PASSWORD = "correct horse battery staple"


def test_invited_client_sets_a_password_and_is_logged_in(api: str, database_url: str) -> None:
    email = unique_email()
    token = invite(database_url, email, name="Ann Client")

    assert httpx.get(f"{api}/auth/invites/{token}").json() == {"email": email, "name": "Ann Client"}
    response = httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": PASSWORD})

    assert response.status_code == 204
    cookie_header = next(
        h for h in response.headers.get_list("set-cookie") if h.startswith("detective_session=")
    )
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "SameSite=lax" in cookie_header
    me = httpx.get(f"{api}/auth/me", headers={"cookie": session_cookie(response)}).json()
    assert (me["email"], me["name"], me["role"]) == (email, "Ann Client", "client")


def test_invite_link_works_only_once(api: str, database_url: str) -> None:
    token = invite(database_url, unique_email())
    first = httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": PASSWORD})
    second = httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": "another password!"})

    assert first.status_code == 204
    assert second.status_code == 404
    assert httpx.get(f"{api}/auth/invites/{token}").status_code == 404


def test_expired_invite_is_rejected(api: str, database_url: str) -> None:
    token = invite(database_url, unique_email(), ttl_hours=0)

    assert httpx.get(f"{api}/auth/invites/{token}").status_code == 404
    response = httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": PASSWORD})
    assert response.status_code == 404


def test_forged_invite_is_rejected(api: str) -> None:
    forged = secrets.token_urlsafe(32)

    assert httpx.get(f"{api}/auth/invites/{forged}").status_code == 404
    response = httpx.post(f"{api}/auth/invites/redeem", json={"token": forged, "password": PASSWORD})
    assert response.status_code == 404


def test_short_passwords_are_refused(api: str, database_url: str) -> None:
    token = invite(database_url, unique_email())

    response = httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": "short"})

    assert response.status_code == 422
    assert httpx.get(f"{api}/auth/invites/{token}").status_code == 200, (
        "a refused attempt must not burn the invite"
    )


def test_login_and_logout(api: str, database_url: str) -> None:
    email = unique_email()
    sign_up(api, database_url, email, PASSWORD)

    wrong = httpx.post(f"{api}/auth/login", json={"email": email, "password": "wrong password!!"})
    unknown = httpx.post(f"{api}/auth/login", json={"email": unique_email(), "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401

    login = httpx.post(f"{api}/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert login.status_code == 204
    cookie = session_cookie(login)
    assert httpx.get(f"{api}/auth/me", headers={"cookie": cookie}).status_code == 200

    assert httpx.post(f"{api}/auth/logout", headers={"cookie": cookie}).status_code == 204
    assert httpx.get(f"{api}/auth/me", headers={"cookie": cookie}).status_code == 401


def test_detective_requires_login(api: str) -> None:
    response = httpx.post(
        f"{api}/agent",
        json={
            "threadId": "t",
            "runId": "r",
            "state": {},
            "messages": [],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
    )

    assert response.status_code == 401
    assert (
        httpx.post(f"{api}/agent", headers={"cookie": "detective_session=forged"}, json={}).status_code == 401
    )


def test_only_the_owner_can_invite_clients(api: str, database_url: str) -> None:
    client = sign_up(api, database_url, unique_email(), PASSWORD)
    owner = sign_up(api, database_url, unique_email("owner"), PASSWORD, role="owner")
    new_client_email = unique_email()
    body = {"email": new_client_email, "name": "Bob Client"}

    assert httpx.post(f"{api}/auth/invites", json=body, headers={"cookie": client}).status_code == 403
    assert httpx.post(f"{api}/auth/invites", json=body).status_code == 401
    created = httpx.post(f"{api}/auth/invites", json=body, headers={"cookie": owner})
    assert created.status_code == 201

    token = created.json()["invite_path"].removeprefix("/invite/")
    redeemed = httpx.post(f"{api}/auth/invites/redeem", json={"token": token, "password": PASSWORD})
    me = httpx.get(f"{api}/auth/me", headers={"cookie": session_cookie(redeemed)}).json()
    assert (me["email"], me["role"]) == (new_client_email, "client"), "owner-made invites only create clients"
