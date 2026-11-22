"""SSO re-checks counted for ``/metrics`` (issue #684; ``docs/archive/design/SSO.md``).

The backend re-checks one thing about SSO on its own: each allowed domain's
DNS record (``sso_domains``), on the schedule (``trigger="recheck"``) and
when a newer proof by another company makes it look the older claim up
again (``trigger="conflict"``). An Owner's own check is not a re-check and is
not counted here. ``record_domain_recheck`` counts each re-check by its
outcome: ``ok`` when the record still holds the token, otherwise ``failed``
with the reason from ``FAILURE_REASONS`` (an outcome outside it is
``other``, so the label set stays bounded). ``record_recheck_cycle`` counts
each cycle of the scheduled loop this replica ran, ``failed`` when it raised.

No label names a company, a domain or a person: which domain failed is in
``sso_domain_checks`` and the audit chain, read by an Owner, not in a
public scrape. Every series exists from zero, so an alert on the rate of
failures has a series to read before the first one.
"""

from __future__ import annotations

from prometheus_client import Counter

RECHECK_TRIGGERS = ("recheck", "conflict")
FAILURE_REASONS = (
    "not_found",
    "no_match",
    "unavailable",
    "verified_elsewhere",
    "other",
)

DOMAIN_RECHECKS = Counter(
    "fabrique_sso_domain_rechecks_total",
    "SSO domain DNS re-checks, by trigger and outcome",
    ["trigger", "outcome", "reason"],
)

RECHECK_CYCLES = Counter(
    "fabrique_sso_domain_recheck_cycles_total",
    "Cycles of the scheduled SSO domain re-check run by this replica",
    ["outcome"],
)

for _trigger in RECHECK_TRIGGERS:
    DOMAIN_RECHECKS.labels(trigger=_trigger, outcome="ok", reason="verified")
    for _reason in FAILURE_REASONS:
        DOMAIN_RECHECKS.labels(trigger=_trigger, outcome="failed", reason=_reason)
for _outcome in ("ok", "failed"):
    RECHECK_CYCLES.labels(outcome=_outcome)


def record_domain_recheck(trigger: str, check_outcome: str) -> None:
    """Count one domain re-check; a trigger that is not a re-check is ignored."""
    if trigger not in RECHECK_TRIGGERS:
        return
    if check_outcome == "verified":
        DOMAIN_RECHECKS.labels(trigger=trigger, outcome="ok", reason="verified").inc()
        return
    reason = check_outcome if check_outcome in FAILURE_REASONS else "other"
    DOMAIN_RECHECKS.labels(trigger=trigger, outcome="failed", reason=reason).inc()


def record_recheck_cycle(ok: bool) -> None:
    """Count one cycle of the scheduled re-check that this replica ran."""
    RECHECK_CYCLES.labels(outcome="ok" if ok else "failed").inc()
