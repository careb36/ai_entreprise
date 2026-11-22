"""SSO re-checks counted in ``/metrics`` (issue #684, the metric half;
``docs/archive/design/SSO.md``, T20–T21; ``docs/OPERATIONS.md``, ``/metrics``).

The backend re-checks one thing about SSO on its own: each allowed domain's
DNS record, on the schedule (``trigger="recheck"``) and when a newer proof
by another company makes it look the older claim up again
(``trigger="conflict"``). Each such lookup is counted by its outcome, ``ok``
or ``failed`` with the reason from a fixed set; a cycle of the scheduled loop
that raises is counted too. An Owner's own check is not a re-check. No label
names a company, a domain or a person."""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from config import settings
from models import SsoDomain
from prometheus_client import REGISTRY
from services.platform import dns_client, sso_domains, sso_metrics
from sqlmodel import select
from sso_fake_dns import FakeDns
from sso_fake_idp import company

RECHECKS = "fabrique_sso_domain_rechecks_total"
CYCLES = "fabrique_sso_domain_recheck_cycles_total"


@pytest.fixture
def fake_dns(monkeypatch):
    monkeypatch.setattr(settings, "SSO_DOMAIN_RECHECK_HOURS", 24)
    return FakeDns().install(monkeypatch)


def _rechecks(trigger: str, outcome: str, reason: str) -> float:
    value = REGISTRY.get_sample_value(
        RECHECKS, {"trigger": trigger, "outcome": outcome, "reason": reason}
    )
    return value or 0.0


def _cycles(outcome: str) -> float:
    return REGISTRY.get_sample_value(CYCLES, {"outcome": outcome}) or 0.0


def _row(session, company_id: int, domain: str) -> SsoDomain:
    session.add(
        SsoDomain(company_id=company_id, domain=domain, token=sso_domains.new_token())
    )
    session.commit()
    return session.exec(
        select(SsoDomain).where(
            SsoDomain.company_id == company_id, SsoDomain.domain == domain
        )
    ).one()


def _later() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=25)


def test_each_scheduled_recheck_is_counted_by_its_outcome(sqlite_session, fake_dns):
    acme = company(sqlite_session)
    tag = secrets.token_hex(3)
    held = _row(sqlite_session, acme.id, f"held-{tag}.test")
    gone = _row(sqlite_session, acme.id, f"gone-{tag}.test")
    wrong = _row(sqlite_session, acme.id, f"wrong-{tag}.test")
    down = _row(sqlite_session, acme.id, f"down-{tag}.test")
    fake_dns.publish(held)
    fake_dns.publish(wrong, "la-fabrique-domain-verification=not-ours")
    fake_dns.answers[sso_domains.record_name(down.domain)] = dns_client.DnsUnavailable(
        "Timeout"
    )
    before = {
        "verified": _rechecks("recheck", "ok", "verified"),
        "not_found": _rechecks("recheck", "failed", "not_found"),
        "no_match": _rechecks("recheck", "failed", "no_match"),
        "unavailable": _rechecks("recheck", "failed", "unavailable"),
    }
    assert sso_domains.recheck_due(sqlite_session, now=_later()) >= 4
    for name in ("verified", "not_found", "no_match", "unavailable"):
        outcome = "ok" if name == "verified" else "failed"
        # At least ours (other tests' rows may be due in the same database).
        assert _rechecks("recheck", outcome, name) - before[name] >= 1, name
    assert sso_domains.find(sqlite_session, acme.id, gone.domain).last_outcome == (
        "not_found"
    )


def test_an_owner_check_is_not_a_recheck(sqlite_session, fake_dns):
    acme = company(sqlite_session)
    row = _row(sqlite_session, acme.id, f"own-{secrets.token_hex(3)}.test")
    fake_dns.publish(row)
    before = sum(
        _rechecks(trigger, "ok", "verified") for trigger in ("recheck", "conflict")
    )
    assert sso_domains.check(sqlite_session, row, trigger="owner")["outcome"] == (
        "verified"
    )
    after = sum(
        _rechecks(trigger, "ok", "verified") for trigger in ("recheck", "conflict")
    )
    assert after == before


def test_the_conflict_recheck_of_an_older_claim_is_counted(sqlite_session, fake_dns):
    domain = f"moved-{secrets.token_hex(3)}.test"
    old_row = _row(sqlite_session, company(sqlite_session).id, domain)
    fake_dns.publish(old_row)
    sso_domains.check(sqlite_session, old_row, trigger="owner")
    new_row = _row(sqlite_session, company(sqlite_session).id, domain)
    name = sso_domains.record_name(domain)
    fake_dns.answers[name] = [
        sso_domains.record_value(old_row),
        sso_domains.record_value(new_row),
    ]
    still = _rechecks("conflict", "ok", "verified")
    sso_domains.check(sqlite_session, new_row, trigger="owner")
    assert _rechecks("conflict", "ok", "verified") == still + 1
    fake_dns.answers[name] = [sso_domains.record_value(new_row)]
    released = _rechecks("conflict", "failed", "no_match")
    sso_domains.check(sqlite_session, new_row, trigger="owner")
    assert _rechecks("conflict", "failed", "no_match") == released + 1


def test_an_outcome_outside_the_known_set_is_counted_as_other():
    before = _rechecks("recheck", "failed", "other")
    sso_metrics.record_domain_recheck("recheck", "something-new")
    assert _rechecks("recheck", "failed", "other") == before + 1
    # A trigger that is not a re-check is not counted at all.
    sso_metrics.record_domain_recheck("owner", "verified")
    assert (
        REGISTRY.get_sample_value(
            RECHECKS, {"trigger": "owner", "outcome": "ok", "reason": "verified"}
        )
        is None
    )


def test_a_failed_recheck_cycle_is_counted_and_the_loop_survives(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("database gone")

    failed, ok = _cycles("failed"), _cycles("ok")
    monkeypatch.setattr(sso_domains.locks, "run_exclusive", boom)
    assert asyncio.run(sso_domains.recheck_once(object())) is None
    assert _cycles("failed") == failed + 1
    monkeypatch.setattr(sso_domains.locks, "run_exclusive", lambda *a, **k: 3)
    assert asyncio.run(sso_domains.recheck_once(object())) == 3
    assert _cycles("ok") == ok + 1
    # The lock held by another replica: that replica counts its cycle.
    monkeypatch.setattr(sso_domains.locks, "run_exclusive", lambda *a, **k: None)
    assert asyncio.run(sso_domains.recheck_once(object())) is None
    assert (_cycles("ok"), _cycles("failed")) == (ok + 1, failed + 1)


def test_metrics_expose_every_series_from_zero_without_personal_labels(test_client):
    text = test_client.get("/metrics").text
    allowed = {
        "trigger": set(sso_metrics.RECHECK_TRIGGERS),
        "outcome": {"ok", "failed"},
        "reason": {"verified", *sso_metrics.FAILURE_REASONS},
    }
    lines = [line for line in text.splitlines() if line.startswith(RECHECKS + "{")]
    # Every combination exists before any re-check ran, so a rate alert on a
    # failure has a series to read.
    assert len(lines) == len(sso_metrics.RECHECK_TRIGGERS) * (
        1 + len(sso_metrics.FAILURE_REASONS)
    )
    for line in lines:
        labels = dict(re.findall(r'(\w+)="([^"]*)"', line))
        assert set(labels) == set(allowed), line
        for key, value in labels.items():
            assert value in allowed[key], line
        assert (labels["outcome"] == "ok") == (labels["reason"] == "verified"), line
    cycles = [line for line in text.splitlines() if line.startswith(CYCLES + "{")]
    assert sorted(re.findall(r'outcome="(\w+)"', " ".join(cycles))) == [
        "failed",
        "ok",
    ]
