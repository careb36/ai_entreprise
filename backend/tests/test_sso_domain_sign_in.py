"""What an unverified domain may do at sign-in (issue #512; docs/design/SSO.md,
T16, T21): only a domain verified by DNS lets SSO create or link an account;
an identity already linked keeps signing in, so a lost TXT record locks
nobody out; the domains of connections that existed before verification
(``legacy``) keep working during the grace period the operator controls,
unless another company has verified the domain; the login page's domain
lookup prefers the connection that may use the domain. Migration 0049 gives
every existing domain a legacy record."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from config import settings
from models import Company, SsoDomain, SsoIdentity, User
from services.platform import sso_domains
from sqlmodel import select
from sso_fake_dns import FakeDns
from sso_fake_idp import company, connect, member, sign_in, start, verify_domains


@pytest.fixture
def fake_dns(monkeypatch, fake_idp):
    return FakeDns().install(monkeypatch)


def _domain_row(session, company_id, domain="acme.test") -> SsoDomain:
    session.expire_all()
    return session.exec(
        select(SsoDomain).where(
            SsoDomain.company_id == company_id, SsoDomain.domain == domain
        )
    ).one()


def _unverified(session, **overrides):
    acme = company(session)
    conn = connect(session, acme.id, verified=False, **overrides)
    sso_domains.sync(session, acme.id, list(conn.allowed_domains))
    session.commit()
    return acme, conn


def test_an_unverified_domain_creates_no_account(
    sqlite_session, test_client, fake_idp, fake_dns
):
    acme, conn = _unverified(sqlite_session, auto_join=True)
    fake_idp.claims = {"email": "new@acme.test", "sub": "subject-new"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "domain_unverified"}
    assert (
        sqlite_session.exec(select(User).where(User.email == "new@acme.test")).first()
        is None
    )
    verify_domains(sqlite_session, acme.id)
    assert "code" in sign_in(test_client, fake_idp, conn.slug)


def test_an_unverified_domain_links_no_account(
    sqlite_session, test_client, fake_idp, fake_dns
):
    acme, conn = _unverified(sqlite_session, auto_join=False)
    invited, _ = member(sqlite_session, acme.id, "Viewer", email="ana@acme.test")
    fake_idp.claims = {"email": invited.email, "sub": "subject-ana"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "domain_unverified"}
    sqlite_session.refresh(invited)
    assert invited.password_hash  # nothing changed
    assert not sqlite_session.exec(select(SsoIdentity)).all()


def test_a_linked_identity_keeps_signing_in_when_the_domain_lapses(
    sqlite_session, test_client, fake_idp, fake_dns
):
    acme = company(sqlite_session)
    conn = connect(sqlite_session, acme.id, auto_join=True)
    fake_idp.claims = {"email": "ana@acme.test", "sub": "subject-ana"}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    row = _domain_row(sqlite_session, acme.id)
    sso_domains.check(sqlite_session, row, trigger="recheck")  # no record: lapses
    assert _domain_row(sqlite_session, acme.id).status == "unverified"
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    # A new person still cannot come in through it.
    fake_idp.claims = {"email": "bob@acme.test", "sub": "subject-bob"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "domain_unverified"}


def _legacy(session, created_days_ago=0):
    acme, conn = _unverified(session, auto_join=True)
    row = _domain_row(session, acme.id)
    row.legacy = True
    row.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=created_days_ago
    )
    session.add(row)
    session.commit()
    return acme, conn


def test_a_legacy_domain_works_during_the_grace_period(
    sqlite_session, test_client, fake_idp, fake_dns, monkeypatch
):
    monkeypatch.setattr(settings, "SSO_LEGACY_DOMAIN_GRACE", True)
    monkeypatch.setattr(settings, "SSO_LEGACY_DOMAIN_GRACE_DAYS", 30)
    acme, conn = _legacy(sqlite_session, created_days_ago=3)
    fake_idp.claims = {"email": "new@acme.test", "sub": "subject-new"}
    assert "code" in sign_in(test_client, fake_idp, conn.slug)
    shown = sso_domains.serialize(sqlite_session, _domain_row(sqlite_session, acme.id))
    assert shown["status"] == "grace" and shown["grace_until"]


@pytest.mark.parametrize(
    ("grace", "days_ago"),
    [(False, 0), (True, 31)],
    ids=["grace-off", "grace-over"],
)
def test_a_legacy_domain_stops_when_the_grace_is_off_or_over(
    sqlite_session, test_client, fake_idp, fake_dns, monkeypatch, grace, days_ago
):
    monkeypatch.setattr(settings, "SSO_LEGACY_DOMAIN_GRACE", grace)
    monkeypatch.setattr(settings, "SSO_LEGACY_DOMAIN_GRACE_DAYS", 30)
    _, conn = _legacy(sqlite_session, created_days_ago=days_ago)
    fake_idp.claims = {"email": "new@acme.test", "sub": "subject-new"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "domain_unverified"}


def test_the_grace_ends_once_another_company_verifies_the_domain(
    sqlite_session, test_client, fake_idp, fake_dns, monkeypatch
):
    monkeypatch.setattr(settings, "SSO_LEGACY_DOMAIN_GRACE", True)
    _, conn = _legacy(sqlite_session)
    owner = company(sqlite_session)
    connect(sqlite_session, owner.id, slug=f"real-{secrets.token_hex(3)}")
    fake_idp.claims = {"email": "new@acme.test", "sub": "subject-new"}
    assert sign_in(test_client, fake_idp, conn.slug) == {"error": "domain_unverified"}


def test_the_login_page_finds_the_connection_that_may_use_the_domain(
    sqlite_session, test_client, fake_idp, fake_dns
):
    # A squatter lists the domain unverified; the verified company is found.
    squatter, _ = _unverified(sqlite_session, allowed_domains=["corp-z.test"])
    real = company(sqlite_session)
    conn = connect(sqlite_session, real.id, allowed_domains=["corp-z.test"])
    params = start(test_client, fake_idp, "ana@corp-z.test")
    assert params["client_id"] == conn.client_id
    from models import SsoFlowState

    (state,) = sqlite_session.exec(select(SsoFlowState)).all()
    assert state.connection_id == conn.id
    assert squatter.id != real.id


def test_the_migration_gives_existing_domains_a_legacy_record(tmp_path, monkeypatch):
    import migrations
    from sqlalchemy import text
    from sqlmodel import Session, create_engine

    from alembic import command

    url = f"sqlite:///{tmp_path / 'domains.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    engine = create_engine(url)
    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0048")
    with Session(engine) as session:
        session.add(Company(company_name="Acme", autonomy_level=2))
        session.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sso_connections (company_id, slug, preset, issuer, "
                "client_id, allowed_domains, default_role, auto_join, enabled, "
                "require_sso) VALUES (1, 'acme', 'generic', 'https://idp.test', "
                "'c', :domains, 'Viewer', 1, 1, 0)"
            ),
            {"domains": '["acme.test", "acme2.test"]'},
        )
    migrations.upgrade_to_head(engine)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT company_id, domain, legacy, status, token FROM sso_domains "
                "ORDER BY domain"
            )
        ).fetchall()
    assert [(r[0], r[1], bool(r[2]), r[3]) for r in rows] == [
        (1, "acme.test", True, "unverified"),
        (1, "acme2.test", True, "unverified"),
    ]
    assert len({r[4] for r in rows}) == 2 and all(len(r[4]) >= 32 for r in rows)
    assert migrations.schema_differences(engine) == []
