"""Password sign-in and registration against guessing and enumeration
(issue #541).

- WSTG-v42-ATHN-03: login and registration are limited per client address
  (slowapi), and login per account with a backoff that doubles after
  repeated failures; a locked account is refused before its password is
  checked, and a success clears the record.
- WSTG-v42-IDNT-04: registration answers the same for a new and an existing
  email and leaves the existing account alone; login answers the same for an
  unknown email, a password-less account and a wrong password, after the
  same bcrypt work.
"""

import secrets

import auth_tokens
import pytest
import routers.auth as auth_router
from config import settings
from models import User
from passwords import hash_password
from services.platform import login_throttle
from sqlmodel import select

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(login_throttle, "time", clock)
    return clock


def _email():
    return f"limits-{secrets.token_hex(6)}@example.com"


def _user(session, email, password=PASSWORD):
    user = User(email=email, full_name="Limits", password_hash=hash_password(password))
    session.add(user)
    session.commit()
    return user


def _login(client, email, password):
    return client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "token_delivery": "bearer"},
    )


def _register(client, email, password=PASSWORD):
    return client.post(
        "/v1/auth/register",
        json={"email": email, "full_name": "Someone", "password": password},
    )


# -- per client address ----------------------------------------------------


def test_login_is_limited_per_client_address(test_client, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT", "3/minute")
    codes = [_login(test_client, _email(), "wrong").status_code for _ in range(4)]
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429


def test_registration_is_limited_per_client_address(test_client, monkeypatch):
    monkeypatch.setattr(settings, "REGISTER_RATE_LIMIT", "2/minute")
    codes = [_register(test_client, _email()).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# -- per account -----------------------------------------------------------


def test_repeated_failures_lock_the_account_with_a_doubling_backoff(
    test_client, sqlite_session, monkeypatch, clock
):
    monkeypatch.setattr(settings, "LOGIN_FAILURES_BEFORE_BACKOFF", 2)
    email = _email()
    _user(sqlite_session, email)
    assert _login(test_client, email, "wrong").status_code == 401
    assert _login(test_client, email, "wrong").status_code == 401
    assert _login(test_client, email, "wrong").status_code == 401  # locks 2 s

    locked = _login(test_client, email, PASSWORD)
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "2"
    # Locked before the password is checked: the right one does not get in.
    assert "access_token" not in locked.text

    clock.now += 2
    assert _login(test_client, email, "wrong").status_code == 401  # locks 4 s
    assert _login(test_client, email, PASSWORD).headers["retry-after"] == "4"

    clock.now += 4
    assert _login(test_client, email, PASSWORD).status_code == 200
    # The success cleared the record.
    assert login_throttle.retry_after(email) == 0
    assert _login(test_client, email, "wrong").status_code == 401


def test_the_backoff_is_capped(monkeypatch, clock):
    monkeypatch.setattr(settings, "LOGIN_FAILURES_BEFORE_BACKOFF", 1)
    monkeypatch.setattr(settings, "LOGIN_BACKOFF_MAX_SECONDS", 60)
    email = _email()
    locks = [login_throttle.failure(email) for _ in range(12)]
    assert locks[:4] == [0, 2, 4, 8]
    assert max(locks) == 60


def test_failures_age_out_of_the_window(monkeypatch, clock):
    monkeypatch.setattr(settings, "LOGIN_FAILURES_BEFORE_BACKOFF", 2)
    monkeypatch.setattr(settings, "LOGIN_FAILURE_WINDOW_SECONDS", 60)
    email = _email()
    login_throttle.failure(email)
    login_throttle.failure(email)
    clock.now += 61
    assert login_throttle.failure(email) == 0


def test_an_unknown_email_is_locked_like_a_real_one(test_client, monkeypatch, clock):
    """The lock does not tell a registered address from an unknown one."""
    monkeypatch.setattr(settings, "LOGIN_FAILURES_BEFORE_BACKOFF", 1)
    for _ in range(2):
        _login(test_client, "nobody-here@example.com", "wrong")
    response = _login(test_client, "nobody-here@example.com", "wrong")
    assert response.status_code == 429


# -- enumeration -----------------------------------------------------------


def test_registration_answers_the_same_for_a_new_and_an_existing_email(
    test_client, sqlite_session
):
    email = _email()
    first = _register(test_client, email)
    again = _register(test_client, email, password="another-password")
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert "user_id" not in first.json()
    # The existing account is untouched: its password still works, the
    # second one does not.
    users = sqlite_session.exec(select(User).where(User.email == email)).all()
    assert len(users) == 1
    assert _login(test_client, email, PASSWORD).status_code == 200
    assert _login(test_client, email, "another-password").status_code == 401


def test_registration_hashes_the_password_even_for_an_existing_email(
    test_client, sqlite_session, monkeypatch
):
    email = _email()
    _user(sqlite_session, email)
    hashed = []
    monkeypatch.setattr(
        auth_router, "hash_password", lambda value: hashed.append(value) or "x"
    )
    assert _register(test_client, email, "chosen-now-passphrase").status_code == 200
    assert hashed == ["chosen-now-passphrase"]


def test_login_answers_alike_for_unknown_email_no_password_and_wrong_password(
    test_client, sqlite_session
):
    known = _email()
    _user(sqlite_session, known)
    sso_only = _email()
    sqlite_session.add(User(email=sso_only, full_name="SSO", password_hash=""))
    sqlite_session.commit()
    answers = [
        _login(test_client, known, "wrong-password"),
        _login(test_client, _email(), "wrong-password"),
        _login(test_client, sso_only, "wrong-password"),
    ]
    assert {a.status_code for a in answers} == {401}
    assert len({a.text for a in answers}) == 1


def test_an_unknown_or_password_less_account_costs_a_bcrypt_check(
    test_client, sqlite_session, monkeypatch
):
    burned = []
    real = auth_router.burn_verify
    monkeypatch.setattr(
        auth_router, "burn_verify", lambda plain: burned.append(plain) or real(plain)
    )
    sso_only = _email()
    sqlite_session.add(User(email=sso_only, full_name="SSO", password_hash=""))
    sqlite_session.commit()
    _login(test_client, _email(), "guess-1")
    _login(test_client, sso_only, "guess-2")
    assert burned == ["guess-1", "guess-2"]
