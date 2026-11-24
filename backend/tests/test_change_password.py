"""Changing a password (``docs/design/change-password.md``): the route
``POST /v1/auth/password`` for the signed-in person, and the operator's reset
on the server (``backend/app/reset_password.py``)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import auth_tokens
import pytest
from config import settings
from models import AuditLog, SessionFamily, User
from passwords import verify_password
from services.platform import api_tokens, sessions
from sqlmodel import select
from sso_fake_idp import company, member

OLD = "correct-password"
NEW = "a-brand-new-passphrase"


@pytest.fixture
def browser(test_client, fake_idp):
    """The test client over https, so Secure cookies come back as a browser
    would send them; ``fake_idp`` turns authentication on with a key."""
    test_client.base_url = "https://testserver"
    test_client.cookies.clear()
    return test_client


def _login(client, email, password=OLD):
    return client.post("/v1/auth/login", json={"email": email, "password": password})


def _csrf(client) -> dict[str, str]:
    return {"X-Fabrique-CSRF": client.cookies.get("fabrique_csrf", path="/")}


def _change(client, current=OLD, new=NEW, headers=None):
    return client.post(
        "/v1/auth/password",
        json={"current_password": current, "new_password": new},
        headers=headers if headers is not None else _csrf(client),
    )


def _sid(client) -> str:
    token = client.cookies.get("fabrique_session", path="/")
    return auth_tokens.decode_access_token(token)["sid"]


def _hash(session, user_id) -> str:
    session.expire_all()
    return session.get(User, user_id).password_hash


def _signed_in(sqlite_session, browser, role="Viewer"):
    acme = company(sqlite_session)
    user, bearer = member(sqlite_session, acme.id, role)
    assert _login(browser, user.email).status_code == 200
    return acme, user, bearer


def test_wrong_current_password_is_refused_and_changes_nothing(sqlite_session, browser):
    _, user, _ = _signed_in(sqlite_session, browser)
    before = _hash(sqlite_session, user.id)
    response = _change(browser, current="not-the-password")
    # 403, not 401: a 401 would make the page refresh and end the session.
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "wrong_password"
    assert _hash(sqlite_session, user.id) == before
    assert browser.get("/v1/auth/me").status_code == 200
    assert not sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "password_changed")
    ).all()


def test_a_too_short_new_password_is_refused(sqlite_session, browser, monkeypatch):
    monkeypatch.setattr(settings, "PASSWORD_MIN_LENGTH", 12)
    _, user, _ = _signed_in(sqlite_session, browser)
    before = _hash(sqlite_session, user.id)
    response = _change(browser, new="short-11ch")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "too_short"
    assert _hash(sqlite_session, user.id) == before


def test_the_new_password_must_differ(sqlite_session, browser):
    _, user, _ = _signed_in(sqlite_session, browser)
    response = _change(browser, new=OLD)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "same_password"
    assert not sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "password_changed")
    ).all()
    assert verify_password(OLD, _hash(sqlite_session, user.id))


def test_success_switches_the_password_and_ends_the_other_sessions(
    sqlite_session, browser
):
    _, user, _ = _signed_in(sqlite_session, browser)
    old_sid = _sid(browser)
    elsewhere = sessions.start(sqlite_session, user.id, "password")  # another device

    response = _change(browser)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "changed" and body["sessions_ended"] == 2
    assert response.headers["cache-control"] == "no-store"
    # This browser got a new family, and keeps working with it.
    new_sid = _sid(browser)
    assert (
        new_sid != old_sid and body["csrf_token"] == _csrf(browser)["X-Fabrique-CSRF"]
    )
    assert browser.get("/v1/auth/me").status_code == 200
    sqlite_session.expire_all()
    for public_id in (old_sid, elsewhere.family.public_id):
        family = sqlite_session.exec(
            select(SessionFamily).where(SessionFamily.public_id == public_id)
        ).one()
        assert family.revoked_reason == "password_changed"
    assert sessions.active(sqlite_session, new_sid) is not None

    browser.cookies.clear()
    assert _login(browser, user.email, OLD).status_code == 401
    assert _login(browser, user.email, NEW).status_code == 200


def test_a_bearer_access_token_changes_it_and_gets_no_cookie_session(
    sqlite_session, browser
):
    acme = company(sqlite_session)
    user, bearer = member(sqlite_session, acme.id, "Viewer")
    browser.cookies.clear()
    response = _change(browser, headers=bearer)
    assert response.status_code == 200, response.text
    assert "csrf_token" not in response.json()
    assert "set-cookie" not in response.headers
    assert verify_password(NEW, _hash(sqlite_session, user.id))


def test_an_api_token_is_refused(sqlite_session, browser):
    acme = company(sqlite_session)
    _token, raw = api_tokens.create_token(sqlite_session, acme.id, "ci")
    browser.cookies.clear()
    response = _change(browser, headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "person_only"


def test_anonymous_is_refused(browser):
    browser.cookies.clear()
    assert _change(browser, headers={}).status_code == 401


def test_the_cookie_session_needs_the_csrf_header(sqlite_session, browser):
    _, user, _ = _signed_in(sqlite_session, browser)
    before = _hash(sqlite_session, user.id)
    assert _change(browser, headers={}).status_code == 403
    assert _hash(sqlite_session, user.id) == before


def test_an_sso_only_account_is_told_it_has_no_password(sqlite_session, browser):
    acme = company(sqlite_session)
    user, bearer = member(sqlite_session, acme.id, "Viewer")
    user.password_hash = ""
    sqlite_session.add(user)
    sqlite_session.commit()
    browser.cookies.clear()
    response = _change(browser, headers=bearer)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_password"


def test_the_change_is_audited_without_any_secret(sqlite_session, browser):
    acme, user, _ = _signed_in(sqlite_session, browser)
    other = company(sqlite_session)
    from models import CompanyMember

    sqlite_session.add(
        CompanyMember(company_id=other.id, user_id=user.id, human_role="Viewer")
    )
    sqlite_session.commit()
    assert _change(browser).status_code == 200
    sqlite_session.expire_all()
    rows = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "password_changed")
    ).all()
    # One row in each of the person's companies' chains.
    assert sorted(row.company_id for row in rows) == sorted([acme.id, other.id])
    stored = _hash(sqlite_session, user.id)
    for row in rows:
        assert row.agent_id == f"human:{user.email}"
        assert row.chain_hash and row.decision_signature
        text = json.dumps([row.details, row.result])
        for secret in (OLD, NEW, stored, "$2b$"):
            assert secret not in text
        assert f'"user_id": {user.id}' in text


def test_the_sign_in_backoff_applies(sqlite_session, browser, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_FAILURES_BEFORE_BACKOFF", 1)
    _, user, _ = _signed_in(sqlite_session, browser)
    assert _change(browser, current="guess-1").status_code == 403
    locked = _change(browser, current="guess-2")
    assert locked.status_code == 403  # the second failure starts the lock
    refused = _change(browser)  # the right password, during the lock
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "locked"
    assert int(refused.headers["retry-after"]) >= 1
    assert verify_password(OLD, _hash(sqlite_session, user.id))
    # The lock is the sign-in's: the account's password sign-in waits too.
    browser.cookies.clear()
    assert _login(browser, user.email).status_code == 429


def test_me_says_whether_there_is_a_password_and_its_minimum(sqlite_session, browser):
    _signed_in(sqlite_session, browser)
    me = browser.get("/v1/auth/me").json()
    assert me["has_password"] is True
    assert me["password_min_length"] == settings.PASSWORD_MIN_LENGTH


# ── The operator's reset ─────────────────────────────────────────────────────


def _cli(session, email, first, second=None):
    import reset_password

    asked = iter([first, second if second is not None else first])
    printed: list[str] = []
    code = reset_password.run(
        session,
        ask=lambda _prompt: email,
        secret=lambda _prompt: next(asked),
        out=printed.append,
    )
    return code, "\n".join(printed)


def test_the_operator_reset_changes_the_hash_ends_sessions_and_audits(
    sqlite_session, fake_idp
):
    acme = company(sqlite_session)
    user, _ = member(sqlite_session, acme.id, "Owner")
    before = user.password_hash
    one = sessions.start(sqlite_session, user.id, "password")
    two = sessions.start(sqlite_session, user.id, "sso")

    code, printed = _cli(sqlite_session, user.email.upper(), NEW)

    assert code == 0, printed
    stored = _hash(sqlite_session, user.id)
    assert stored != before and verify_password(NEW, stored)
    for family in (one.family, two.family):
        assert sessions.active(sqlite_session, family.public_id) is None
    rows = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "password_reset_by_operator")
    ).all()
    assert len(rows) == 1 and rows[0].company_id == acme.id
    assert rows[0].chain_hash is not None and rows[0].agent_id == "operator:cli"
    assert rows[0].result["sessions_ended"] == 2
    for secret in (NEW, stored):
        assert secret not in json.dumps([rows[0].details, rows[0].result])
        assert secret not in printed


@pytest.mark.parametrize(
    ("first", "second", "why"),
    [(NEW, NEW + "x", "differ"), ("short", None, "too_short")],
)
def test_the_operator_reset_refuses_and_changes_nothing(
    sqlite_session, fake_idp, first, second, why
):
    acme = company(sqlite_session)
    user, _ = member(sqlite_session, acme.id, "Owner")
    before = user.password_hash
    code, printed = _cli(sqlite_session, user.email, first, second)
    assert code == 1 and why in printed
    assert _hash(sqlite_session, user.id) == before
    assert not sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == "password_reset_by_operator")
    ).all()


def test_the_operator_reset_refuses_an_unknown_or_sso_only_account(sqlite_session):
    code, printed = _cli(sqlite_session, "nobody@acme.test", NEW)
    assert code == 1 and "unknown_user" in printed
    sso = User(email="sso-only@acme.test", full_name="S", password_hash="")
    sqlite_session.add(sso)
    sqlite_session.commit()
    code, printed = _cli(sqlite_session, sso.email, NEW)
    assert code == 1 and "no_password" in printed
    assert _hash(sqlite_session, sso.id) == ""


def test_the_host_wrapper_only_runs_the_reset_in_the_container():
    path = Path(__file__).resolve().parents[2] / "scripts" / "reset_password.py"
    spec = importlib.util.spec_from_file_location("reset_password_wrapper", path)
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    assert wrapper.command("factory-backend") == [
        "docker",
        "exec",
        "-it",
        "factory-backend",
        "python",
        "-m",
        "reset_password",
    ]
    with pytest.raises(ValueError):
        wrapper.command("x; rm -rf /")
