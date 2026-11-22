"""What keeps linking and unlinking an SSO identity whole (issue #512;
``docs/archive/design/SSO.md``, T31, T33): a link or an unlink and its audit row are
one transaction, so a failed audit write leaves neither; one account holds
at most one identity per provider, enforced by the database (migration
0050), so two links racing past the check end with ``identity_conflict``,
not a server error, and the sign-in's race likewise."""

from __future__ import annotations

import pytest
from models import AuditLog, SessionFamily, SsoIdentity
from services.platform import sso_flow, sso_link
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sso_fake_idp import ISSUER, company, connect, member, sign_in
from test_sso_link import _csrf, _identities, _link, _signed_in, browser  # noqa: F401


class _AuditDown:
    """An audit trail that cannot be written."""

    def audit_interaction(self, **_kwargs):
        raise RuntimeError("audit store unavailable")


def _audits(session, action):
    session.expire_all()
    return session.exec(select(AuditLog).where(AuditLog.action == action)).all()


def _linked(session, browser, idp):  # noqa: F811 - the fixture, passed on
    acme = company(session)
    conn = connect(session, acme.id, auto_join=False)
    user = _signed_in(browser, session, acme.id)
    idp.claims = {"sub": "subject-ana"}
    assert _link(browser, idp, acme.id) == {"sso_link": "linked"}
    (identity,) = _identities(session, user.id)
    return acme, conn, user, identity


# One transaction


def test_a_link_whose_audit_fails_links_nothing(
    sqlite_session,
    browser,  # noqa: F811
    fake_idp,
    monkeypatch,
):
    acme = company(sqlite_session)
    connect(sqlite_session, acme.id, auto_join=False)
    _signed_in(browser, sqlite_session, acme.id)
    fake_idp.claims = {"sub": "subject-ana"}
    from test_sso_link import _start

    params = _start(browser, fake_idp, acme.id)
    monkeypatch.setattr(sso_link, "AgentService", _AuditDown)
    response = browser.get(
        "/v1/auth/sso/callback",
        params={"state": params["state"], "code": "c"},
        follow_redirects=False,
    )
    assert response.status_code == 500
    assert not _identities(sqlite_session)


def test_a_self_unlink_whose_audit_fails_unlinks_nothing(
    sqlite_session,
    browser,  # noqa: F811
    fake_idp,
    monkeypatch,
):
    _, _, _, identity = _linked(sqlite_session, browser, fake_idp)
    monkeypatch.setattr(sso_link, "AgentService", _AuditDown)
    response = browser.delete(
        f"/v1/auth/sso/identities/{identity.id}", headers=_csrf(browser)
    )
    assert response.status_code == 500
    assert _identities(sqlite_session)
    assert not _audits(sqlite_session, "sso_identity_unlink")


def test_an_owner_unlink_whose_audit_fails_changes_nothing(
    sqlite_session,
    browser,  # noqa: F811
    fake_idp,
    monkeypatch,
):
    acme, conn, ana, identity = _linked(sqlite_session, browser, fake_idp)
    browser.cookies.clear()
    landed = sign_in(browser, fake_idp, conn.slug)
    assert (
        browser.post("/v1/auth/sso/exchange", json={"code": landed["code"]}).status_code
        == 200
    )
    _, owner_headers = member(sqlite_session, acme.id, "Owner")
    monkeypatch.setattr(sso_link, "AgentService", _AuditDown)
    response = browser.delete(
        f"/v1/companies/{acme.id}/sso/identities/{identity.id}", headers=owner_headers
    )
    assert response.status_code == 500
    assert _identities(sqlite_session)
    families = sqlite_session.exec(
        select(SessionFamily).where(
            SessionFamily.user_id == ana.id, SessionFamily.method == "sso"
        )
    ).all()
    assert families and all(f.revoked_at is None for f in families)
    assert not _audits(sqlite_session, "sso_identity_unlink")


# One identity per provider


def test_the_database_holds_one_identity_per_account_and_provider(
    sqlite_session, fake_idp
):
    user, _ = member(sqlite_session, None, "Viewer")
    sqlite_session.add(SsoIdentity(user_id=user.id, issuer=ISSUER, subject="one"))
    sqlite_session.commit()
    sqlite_session.add(SsoIdentity(user_id=user.id, issuer=ISSUER, subject="two"))
    with pytest.raises(IntegrityError):
        sqlite_session.commit()
    sqlite_session.rollback()
    # Another provider is another identity.
    sqlite_session.add(
        SsoIdentity(user_id=user.id, issuer="https://other.test", subject="two")
    )
    sqlite_session.commit()


def test_two_links_racing_end_with_a_conflict_not_an_error(
    sqlite_session,
    browser,  # noqa: F811
    fake_idp,
    monkeypatch,
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, auto_join=False)
    ana = _signed_in(browser, sqlite_session, acme.id)
    from test_sso_link import _callback, _start

    params = _start(browser, fake_idp, acme.id)
    # The other link won between this one's check and its insert.
    sqlite_session.add(
        SsoIdentity(
            user_id=ana.id, connection_id=conn.id, issuer=ISSUER, subject="first"
        )
    )
    sqlite_session.commit()
    monkeypatch.setattr(sso_flow, "linked_at", lambda *_a, **_k: None)
    fake_idp.claims = {"sub": "second"}
    assert _callback(browser, state=params["state"], code="c") == {
        "sso_link_error": "identity_conflict"
    }
    assert [i.subject for i in _identities(sqlite_session, ana.id)] == ["first"]
    (row,) = _audits(sqlite_session, "sso_identity_link")
    assert row.result["outcome"] == "refused:identity_conflict"


def test_a_sign_in_racing_a_link_ends_with_a_conflict_not_an_error(
    sqlite_session, test_client, fake_idp, monkeypatch
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, auto_join=False)
    invited, _ = member(sqlite_session, acme.id, "Viewer", email="ana@acme.test")
    sqlite_session.add(
        SsoIdentity(
            user_id=invited.id, connection_id=conn.id, issuer=ISSUER, subject="first"
        )
    )
    sqlite_session.commit()
    monkeypatch.setattr(sso_flow, "linked_at", lambda *_a, **_k: None)
    fake_idp.claims = {"email": invited.email, "sub": "second"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "identity_conflict"}
    assert len(_identities(sqlite_session, invited.id)) == 1


def test_the_migration_stops_on_an_account_with_two_identities_at_one_provider(
    tmp_path, monkeypatch
):
    import migrations
    from sqlalchemy import text
    from sqlmodel import create_engine

    from alembic import command

    url = f"sqlite:///{tmp_path / 'identities.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    engine = create_engine(url)
    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0049")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash) "
                "VALUES (7, 'ana@acme.test', 'Ana', '')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sso_identities (user_id, issuer, subject) VALUES "
                "(7, 'https://idp.test', 'a'), (7, 'https://idp.test', 'b'), "
                "(7, 'https://other.test', 'c')"
            )
        )
    with pytest.raises(RuntimeError, match=r"0050 stopped.*user 7.*idp\.test"):
        migrations.upgrade_to_head(engine)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM sso_identities")).scalar()
            == 3
        )
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM sso_identities WHERE subject = 'b'"))
    migrations.upgrade_to_head(engine)
    assert migrations.schema_differences(engine) == []
