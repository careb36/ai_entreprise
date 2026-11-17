"""One account per address, whatever its case (``services/contracts/emails.py``).

An email is normalised by stripping the spaces around it and lower-casing it,
on registration, sign-in, invitation and every lookup, so ``Ana@x`` and
``ana@x`` are one account. Migration 0045 normalises the rows already there
and stops, naming the ids, when two of them collide (it never merges or
deletes an account), then adds a unique index on ``lower(email)``.
"""

from __future__ import annotations

import secrets

import auth_tokens
import migrations
import pytest
from models import User
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _register(client, email, password=PASSWORD):
    return client.post(
        "/v1/auth/register",
        json={"email": email, "full_name": "Ana", "password": password},
    )


def _login(client, email, password=PASSWORD):
    return client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "token_delivery": "bearer"},
    )


def test_the_rule_is_strip_and_lower_case():
    from services.contracts.emails import normalize_email

    assert normalize_email("  Ana.Perez@Example.COM \t") == "ana.perez@example.com"
    assert normalize_email("ana@example.com") == "ana@example.com"


def test_a_mixed_case_registration_signs_in_with_any_case(test_client, sqlite_session):
    local = f"Ana.{secrets.token_hex(4)}"
    shouted = f"  {local}@Example.COM "
    assert _register(test_client, shouted).status_code == 200
    lower = f"{local.lower()}@example.com"
    stored = sqlite_session.exec(select(User).where(User.email == lower)).all()
    assert len(stored) == 1
    assert _login(test_client, lower).status_code == 200
    assert _login(test_client, lower.upper()).status_code == 200


def test_registering_another_case_of_a_taken_address_adds_no_account(
    test_client, sqlite_session
):
    lower = f"ana-{secrets.token_hex(4)}@example.com"
    assert _register(test_client, lower).status_code == 200
    again = _register(test_client, lower.upper(), password="another-password")
    assert again.status_code == 200
    rows = sqlite_session.exec(select(User)).all()
    assert [u.email for u in rows if u.email.lower() == lower] == [lower]
    assert _login(test_client, lower, "another-password").status_code == 401


# -- the migration -----------------------------------------------------------


def _database_at(tmp_path, monkeypatch, revision):
    from alembic import command

    url = f"sqlite:///{tmp_path / 'emails.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    engine = create_engine(url)
    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
    return engine


def _insert(engine, *emails):
    with engine.begin() as connection:
        for email in emails:
            connection.execute(
                text(
                    "INSERT INTO users (email, full_name, password_hash) "
                    "VALUES (:e, 'x', '')"
                ),
                {"e": email},
            )
        return dict(connection.execute(text("SELECT email, id FROM users")).fetchall())


def test_the_migration_normalises_existing_addresses(tmp_path, monkeypatch):
    engine = _database_at(tmp_path, monkeypatch, "0043")
    _insert(engine, " Ana@Example.com", "bob@example.com")
    migrations.upgrade_to_head(engine)
    with engine.connect() as connection:
        emails = sorted(
            connection.execute(text("SELECT email FROM users")).scalars().all()
        )
    assert emails == ["ana@example.com", "bob@example.com"]
    # The unique index on the normalised address holds from now on.
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (email, full_name, password_hash) "
                "VALUES ('ANA@example.com', 'x', '')"
            )
        )


def test_the_migration_stops_on_colliding_accounts_and_names_them(
    tmp_path, monkeypatch
):
    engine = _database_at(tmp_path, monkeypatch, "0043")
    ids = _insert(engine, "Ana@Example.com", "ana@example.com ", "solo@example.com")
    with pytest.raises(RuntimeError) as refused:
        migrations.upgrade_to_head(engine)
    message = str(refused.value)
    assert "ana@example.com" in message
    assert str(ids["Ana@Example.com"]) in message
    assert str(ids["ana@example.com "]) in message
    # Nothing was merged, deleted or rewritten, and the schema stayed at 0043.
    with engine.connect() as connection:
        emails = sorted(
            connection.execute(text("SELECT email FROM users")).scalars().all()
        )
    assert emails == sorted(["Ana@Example.com", "ana@example.com ", "solo@example.com"])
    assert migrations.current_revision(engine) == "0043"
