"""DNS verification of a company's SSO domains (issue #512; docs/design/SSO.md,
T16–T22): every allowed domain gets a random token issued by the backend;
the Owner publishes it as a TXT record at ``_la-fabrique-verify.<domain>``;
a check, on request or at the re-check, records what DNS answered and when,
and only an answer holding the token verifies the domain. A transient
failure changes nothing; a definite "not there" unverifies. A domain
verified by one company cannot be listed by another, and a claim proved now
re-checks the older one first."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from config import settings
from models import AuditLog, SsoDomain, SsoDomainCheck
from services.platform import dns_client, sso_domains
from sqlmodel import select
from sso_fake_dns import FakeDns
from sso_fake_idp import CLIENT_ID, ISSUER, company, member


@pytest.fixture
def fake_dns(monkeypatch, fake_idp):
    return FakeDns().install(monkeypatch)


def _url(company_id, suffix=""):
    return f"/v1/companies/{company_id}/sso{suffix}"


def _body(domains, **overrides):
    body = {
        "slug": f"acme-{secrets.token_hex(3)}",
        "preset": "generic",
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "allowed_domains": domains,
        "default_role": "Viewer",
        "auto_join": True,
        "enabled": True,
    }
    body.update(overrides)
    return body


def _domain(session, company_id, domain) -> SsoDomain:
    session.expire_all()
    return session.exec(
        select(SsoDomain).where(
            SsoDomain.company_id == company_id, SsoDomain.domain == domain
        )
    ).one()


def _setup(session, client, domains=None):
    acme = company(session)
    _, owner = member(session, acme.id, "Owner")
    domains = domains or [f"d{secrets.token_hex(4)}.test"]
    saved = client.put(_url(acme.id), json=_body(domains), headers=owner)
    assert saved.status_code == 200, saved.text
    return acme, owner, domains


def _check(client, company_id, domain, headers):
    return client.post(_url(company_id, f"/domains/{domain}/check"), headers=headers)


def test_each_allowed_domain_gets_a_token_to_publish(
    sqlite_session, test_client, fake_dns
):
    acme, owner, (first,) = _setup(sqlite_session, test_client)
    other, other_owner, (second,) = _setup(sqlite_session, test_client)
    listed = test_client.get(_url(acme.id, "/domains"), headers=owner)
    assert listed.status_code == 200
    (row,) = listed.json()["domains"]
    assert row["domain"] == first and row["status"] == "unverified"
    assert row["record_name"] == f"_la-fabrique-verify.{first}"
    assert row["record_value"].startswith("la-fabrique-domain-verification=")
    token = row["record_value"].split("=", 1)[1]
    assert len(token) >= 32  # random, from the backend; never from the Owner
    other_value = _domain(sqlite_session, other.id, second)
    assert sso_domains.record_value(other_value) != row["record_value"]
    # The settings page shows them beside the connection.
    assert test_client.get(_url(acme.id), headers=owner).json()["domains"] == [row]
    # Only the company's Owner reads them.
    _, manager = member(sqlite_session, acme.id, "Manager")
    assert (
        test_client.get(_url(acme.id, "/domains"), headers=manager).status_code == 403
    )
    assert (
        test_client.get(_url(acme.id, "/domains"), headers=other_owner).status_code
        == 403
    )
    assert _check(test_client, acme.id, first, manager).status_code == 403


def test_a_published_token_verifies_and_the_observation_is_recorded(
    sqlite_session, test_client, fake_dns
):
    acme, owner, (domain,) = _setup(sqlite_session, test_client)
    row = _domain(sqlite_session, acme.id, domain)
    fake_dns.answers[sso_domains.record_name(domain)] = [
        "v=spf1 -all",
        sso_domains.record_value(row),
    ]
    checked = _check(test_client, acme.id, domain, owner)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["outcome"] == "verified" and body["domain"]["status"] == "verified"
    assert body["answer"] == ["v=spf1 -all", sso_domains.record_value(row)]
    assert fake_dns.asked == [f"_la-fabrique-verify.{domain}"]
    (seen,) = sqlite_session.exec(
        select(SsoDomainCheck).where(SsoDomainCheck.domain == domain)
    ).all()
    assert seen.company_id == acme.id and seen.outcome == "verified"
    assert seen.trigger == "owner" and seen.requested_by
    assert seen.answer == body["answer"] and seen.checked_at is not None
    assert _domain(sqlite_session, acme.id, domain).verified_at is not None
    actions = sqlite_session.exec(
        select(AuditLog.action).where(AuditLog.company_id == acme.id)
    ).all()
    assert "sso_domain_verified" in actions


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (None, "not_found"),
        (["la-fabrique-domain-verification=guessed"], "no_match"),
        ([], "no_match"),
    ],
)
def test_a_missing_or_wrong_record_does_not_verify(
    sqlite_session, test_client, fake_dns, answer, outcome
):
    acme, owner, (domain,) = _setup(sqlite_session, test_client)
    if answer is not None:
        fake_dns.answers[sso_domains.record_name(domain)] = answer
    body = _check(test_client, acme.id, domain, owner).json()
    assert body["outcome"] == outcome and body["domain"]["status"] == "unverified"
    assert body["answer"] == (answer or [])


def test_a_transient_failure_keeps_the_status_and_is_recorded(
    sqlite_session, test_client, fake_dns
):
    acme, owner, (domain,) = _setup(sqlite_session, test_client)
    row = _domain(sqlite_session, acme.id, domain)
    fake_dns.publish(row)
    assert _check(test_client, acme.id, domain, owner).json()["outcome"] == "verified"
    fake_dns.answers[sso_domains.record_name(domain)] = dns_client.DnsUnavailable(
        "Timeout"
    )
    body = _check(test_client, acme.id, domain, owner).json()
    assert body["outcome"] == "unavailable" and body["domain"]["status"] == "verified"
    assert "Timeout" in body["detail"]


def test_only_the_companys_own_domains_are_looked_up(
    sqlite_session, test_client, fake_dns
):
    acme, owner, _ = _setup(sqlite_session, test_client)
    for name in ("internal.corp.test", "..", "x" * 300):
        response = _check(test_client, acme.id, name, owner)
        assert response.status_code == 404, name
    assert fake_dns.asked == []


def test_the_recheck_unverifies_a_withdrawn_record_and_verifies_a_new_one(
    sqlite_session, test_client, fake_dns, monkeypatch
):
    monkeypatch.setattr(settings, "SSO_DOMAIN_RECHECK_HOURS", 24)
    acme, owner, (kept, gone) = _setup(
        sqlite_session, test_client, ["kept-x.test", "gone-x.test"]
    )
    for name in (kept, gone):
        fake_dns.publish(_domain(sqlite_session, acme.id, name))
        _check(test_client, acme.id, name, owner)
    fake_dns.withdraw(_domain(sqlite_session, acme.id, gone))
    # Not due yet: nothing is asked.
    fake_dns.asked.clear()
    assert sso_domains.recheck_due(sqlite_session) == 0
    assert fake_dns.asked == []
    later = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=25)
    assert sso_domains.recheck_due(sqlite_session, now=later) == 2
    assert _domain(sqlite_session, acme.id, kept).status == "verified"
    assert _domain(sqlite_session, acme.id, gone).status == "unverified"
    triggers = sqlite_session.exec(
        select(SsoDomainCheck.trigger).where(SsoDomainCheck.domain == gone)
    ).all()
    assert triggers == ["owner", "recheck"]
    unverified = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == acme.id, AuditLog.action == "sso_domain_unverified"
        )
    ).one()
    assert unverified.result["domain"] == gone
    assert unverified.result["trigger"] == "recheck"
    # A pending domain is verified by the re-check once its record appears.
    fake_dns.publish(_domain(sqlite_session, acme.id, gone))
    much_later = later + timedelta(hours=25)
    sso_domains.recheck_due(sqlite_session, now=much_later)
    assert _domain(sqlite_session, acme.id, gone).status == "verified"


def test_the_recheck_can_be_turned_off(
    sqlite_session, test_client, fake_dns, monkeypatch
):
    monkeypatch.setattr(settings, "SSO_DOMAIN_RECHECK_HOURS", 0)
    _setup(sqlite_session, test_client)
    later = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30)
    assert sso_domains.recheck_due(sqlite_session, now=later) == 0
    assert fake_dns.asked == []


def test_a_domain_verified_by_one_company_cannot_be_listed_by_another(
    sqlite_session, test_client, fake_dns
):
    acme, owner, (domain,) = _setup(sqlite_session, test_client)
    other = company(sqlite_session)
    _, other_owner = member(sqlite_session, other.id, "Owner")
    # Unverified, a listing proves nothing, so it holds nothing: a squatter's
    # listing does not keep the real company out.
    listed = test_client.put(_url(other.id), json=_body([domain]), headers=other_owner)
    assert listed.status_code == 200
    fake_dns.publish(_domain(sqlite_session, acme.id, domain))
    assert _check(test_client, acme.id, domain, owner).json()["outcome"] == "verified"
    refused = test_client.put(
        _url(other.id), json=_body(["fresh-x.test", domain]), headers=other_owner
    )
    assert refused.status_code == 409
    assert "verified by another company" in refused.json()["detail"]


def test_a_newer_proof_rechecks_the_older_claim_first(
    sqlite_session, test_client, fake_dns
):
    domain = f"moved-{secrets.token_hex(3)}.test"
    old, old_owner, _ = _setup(sqlite_session, test_client, [domain])
    old_row = _domain(sqlite_session, old.id, domain)
    fake_dns.publish(old_row)
    _check(test_client, old.id, domain, old_owner)
    new = company(sqlite_session)
    member(sqlite_session, new.id, "Owner")
    # The new company listed it before the old one verified (allowed then).
    sqlite_session.add(SsoDomain(company_id=new.id, domain=domain, token="t" * 32))
    sqlite_session.commit()
    new_row = _domain(sqlite_session, new.id, domain)
    # Both records published: the older claim still holds.
    name = sso_domains.record_name(domain)
    fake_dns.answers[name] = [
        sso_domains.record_value(old_row),
        sso_domains.record_value(new_row),
    ]
    held = sso_domains.check(sqlite_session, new_row, trigger="owner")
    assert held["outcome"] == "verified_elsewhere"
    assert _domain(sqlite_session, new.id, domain).status == "unverified"
    # The domain changed hands: only the new company's record is there.
    fake_dns.answers[name] = [sso_domains.record_value(new_row)]
    moved = sso_domains.check(
        sqlite_session, _domain(sqlite_session, new.id, domain), trigger="owner"
    )
    assert moved["outcome"] == "verified"
    assert _domain(sqlite_session, old.id, domain).status == "unverified"
    assert _domain(sqlite_session, new.id, domain).status == "verified"
    conflict = sqlite_session.exec(
        select(SsoDomainCheck).where(
            SsoDomainCheck.company_id == old.id, SsoDomainCheck.trigger == "conflict"
        )
    ).all()
    assert [c.outcome for c in conflict] == ["verified", "no_match"]


def test_removing_a_domain_drops_its_record_but_not_its_observations(
    sqlite_session, test_client, fake_dns
):
    acme, owner, (domain,) = _setup(sqlite_session, test_client)
    _check(test_client, acme.id, domain, owner)
    body = _body(["kept-y.test"])
    assert test_client.put(_url(acme.id), json=body, headers=owner).status_code == 200
    names = [
        d["domain"]
        for d in test_client.get(_url(acme.id, "/domains"), headers=owner).json()[
            "domains"
        ]
    ]
    assert names == ["kept-y.test"]
    assert sqlite_session.exec(
        select(SsoDomainCheck).where(SsoDomainCheck.domain == domain)
    ).all()
    assert test_client.delete(_url(acme.id), headers=owner).status_code == 204
    sqlite_session.expire_all()
    assert not sqlite_session.exec(
        select(SsoDomain).where(SsoDomain.company_id == acme.id)
    ).all()
