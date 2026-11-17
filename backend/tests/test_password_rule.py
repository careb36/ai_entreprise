"""The password a person chooses, and registration that does not tell
accounts apart.

- WSTG-v42-ATHN-07: a password chosen now has at least
  ``PASSWORD_MIN_LENGTH`` characters (12) and at most bcrypt's 72 bytes
  (``passwords.check_new_password``); sign-in never applies the rule, so an
  account with an older, shorter password is not locked out.
- WSTG-v42-IDNT-04: registering a new address and a taken one do the same
  work (one bcrypt hash, one attempted insert the unique index refuses for
  the taken one) and answer alike.
"""

import secrets

import auth_tokens
import pytest
from config import settings
from models import User
from passwords import hash_password
from sqlmodel import select

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _email():
    return f"rule-{secrets.token_hex(6)}@example.com"


def _register(client, email, password=PASSWORD):
    return client.post(
        "/v1/auth/register",
        json={"email": email, "full_name": "Someone", "password": password},
    )


def _login(client, email, password):
    return client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "token_delivery": "bearer"},
    )


def test_registration_does_the_same_database_work_for_a_new_and_a_taken_email(
    test_client, sqlite_session
):
    """A new address used to cost an insert that a taken one did not: the
    timing told them apart. Both now attempt the insert, and the unique index
    refuses the taken one (the same answer, the same round trips)."""
    from sqlalchemy import event

    engine = sqlite_session.get_bind()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()).upper())

    event.listen(engine, "before_cursor_execute", record)
    try:
        email = _email()
        new = _register(test_client, email)
        new_work = [s for s in statements if "USERS" in s]
        statements.clear()
        taken = _register(test_client, email, password="another-password")
        taken_work = [s for s in statements if "USERS" in s]
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert new.status_code == taken.status_code == 200
    assert new.json() == taken.json()
    assert [s.split(" ")[0] for s in new_work] == [s.split(" ")[0] for s in taken_work]
    assert any(s.startswith("INSERT INTO USERS") for s in taken_work)


def test_registration_refuses_a_password_shorter_than_the_minimum(
    test_client, sqlite_session
):
    email = _email()
    short = "x" * 11
    response = _register(test_client, email, password=short)
    assert response.status_code == 422
    assert "at least 12 characters" in response.text
    assert sqlite_session.exec(select(User).where(User.email == email)).all() == []
    assert _register(test_client, email, password="y" * 12).status_code == 200


def test_the_minimum_is_a_setting(test_client, monkeypatch):
    monkeypatch.setattr(settings, "PASSWORD_MIN_LENGTH", 16)
    assert _register(test_client, _email(), password="z" * 15).status_code == 422
    assert _register(test_client, _email(), password="z" * 16).status_code == 200


def test_the_minimum_defaults_to_twelve():
    from config import Settings

    assert Settings().PASSWORD_MIN_LENGTH == 12


def test_an_existing_short_password_still_signs_in(test_client, sqlite_session):
    """The rule applies when a password is chosen, never at sign-in: an
    account made before it is not locked out."""
    email = _email()
    old = "short-" + "1"
    user = User(email=email, full_name="Old", password_hash=hash_password(old))
    sqlite_session.add(user)
    sqlite_session.commit()
    assert _login(test_client, email, old).status_code == 200


def test_the_bcrypt_byte_limit_still_applies_with_the_minimum(test_client):
    response = _register(test_client, _email(), password="ñ" * 37)  # 74 bytes
    assert response.status_code == 422
    assert "72 bytes" in response.text
