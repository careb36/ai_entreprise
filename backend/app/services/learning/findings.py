"""The findings of the self-improvement loop, as records
(``docs/design/rsi-loop.md``, PR 9 of §8; REQ-18, REQ-20).

A finding is a defect or gap the platform observed, kept with the
specification drafted for it and its target measure: the person-written
check that fails on the baseline and should pass once the finding is fixed.
Its outcomes are the cycles run for it, each with the fitness decision
(``services/evidence/fitness.py``), its reasons, the baseline's and the
candidate's sha and the date. An outcome is an observation, so the platform
writes it and nobody else: a producer other than ``PLATFORM`` is refused
(REQ-27, rule 6 of ``docs/POSITIONING.md``). A model or an agent never
writes one, and no hash is taken from the party being recorded: the
specification's sha256 is computed here, from the text as read.

``may_start`` is REQ-20: a specification whose text is the same — the sha256
of its *normalised* text decides, so whitespace and line endings cannot make
a difference — as one whose cycle was reverted does not start a cycle again,
unless a signed-in person asks: never an agent, never an API token (the rule
of ``routers/sod.owner_person``). Another company's reverts are not read.

Every function takes the caller's session and leaves committing to it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from models import Finding, User
from models.finding import FINDING_STATUSES, OUTCOME_DECISIONS
from sqlmodel import Session, select

# The only producer an outcome may name. The fitness observations say the
# same (``services/evidence/fitness_checks.PLATFORM``); evidence is not a
# context learning may import, so the name is repeated here, as data.
PLATFORM = "platform"

STATUS_BY_DECISION = {
    "keep": "kept",
    "revert": "reverted",
    "unmeasured": "unmeasured",
}


class FindingRefused(ValueError):
    """A finding operation the service refuses; the message says why."""


class MayStart(NamedTuple):
    """REQ-20's answer: whether a cycle may start, why, and the finding whose
    reverted specification is in the way (``None`` when none is)."""

    allowed: bool
    reason: str
    reverted_finding_id: int | None = None


# ── The specification's text ─────────────────────────────────────────────────


def normalize_specification_text(text: str) -> str:
    """The text with every whitespace run as one space, the ends stripped:
    line endings, indentation and trailing spaces do not make two
    specifications different (REQ-20)."""
    return " ".join((text or "").split())


def specification_sha256(text: str) -> str:
    """The sha256 of the normalised text, computed here — never received
    from a producer (rule 6)."""
    normalized = normalize_specification_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ── Creating ─────────────────────────────────────────────────────────────────


def create_from_specification_file(
    session: Session,
    company_id: int,
    path: str | Path,
    *,
    source: str | None = None,
    source_ref: str | None = None,
) -> Finding:
    """A finding from a specification file in the PO's shape (as
    ``scripts/self_host/specs/``): the title and the target measure come from
    the file, and a file without a target measure is refused (REQ-2: no cycle
    without a finding that has one). The sha256 is computed from the bytes as
    read here, whatever the file itself claims."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FindingRefused(
            f"the specification file could not be read: {exc}"
        ) from exc
    try:
        record = json.loads(text)
    except ValueError as exc:
        raise FindingRefused(f"{file_path}: not a JSON specification file") from exc
    if not isinstance(record, dict):
        raise FindingRefused(f"{file_path}: not a JSON object")
    measure = record.get("target_measure")
    if not isinstance(measure, str) or not measure.strip():
        raise FindingRefused(
            "the specification names no target_measure: "
            "a cycle needs a finding with a measure (REQ-2)"
        )
    return create_from_specification(
        session,
        company_id,
        specification_text=text,
        title=record.get("title"),
        target_measure=measure,
        specification_path=file_path.as_posix(),
        source=source,
        source_ref=source_ref,
    )


def create_from_specification(
    session: Session,
    company_id: int,
    *,
    specification_text: str,
    title: str | None,
    target_measure: str | None = None,
    specification_path: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    store_text: bool = True,
) -> Finding:
    """Record a finding with the specification drafted for it.

    The sha256 of the normalised text is computed here; a second finding of
    the same company carrying the same specification is refused, so the text
    of a reverted cycle stays findable by ``may_start``. *target_measure* may
    be ``None`` for a finding recorded before its grader is written, but the
    measure must be there before a cycle runs (REQ-2), and ``open_findings``
    only offers the ones that have one.
    """
    text = specification_text or ""
    if not text.strip():
        raise FindingRefused("the specification text is empty")
    clean_title = (title or "").strip()
    if not clean_title:
        raise FindingRefused("the finding has no title")
    sha = specification_sha256(text)
    earlier = session.exec(
        select(Finding).where(
            Finding.company_id == company_id, Finding.specification_sha256 == sha
        )
    ).first()
    if earlier is not None:
        raise FindingRefused(
            f"finding {earlier.id} of this company already carries this specification"
        )
    finding = Finding(
        company_id=company_id,
        source=source or None,
        source_ref=source_ref or None,
        title=clean_title,
        specification_sha256=sha,
        specification_path=specification_path or None,
        specification_text=text if store_text else None,
        target_measure=(target_measure or "").strip() or None,
        status="open",
    )
    session.add(finding)
    session.flush()
    return finding


# ── Reading ──────────────────────────────────────────────────────────────────


def get_finding(session: Session, finding_id: int) -> Finding | None:
    return session.get(Finding, finding_id)


def list_findings(
    session: Session, company_id: int, status: str | None = None
) -> list[Finding]:
    """The company's findings, newest first; only *status* when given."""
    if status is not None and status not in FINDING_STATUSES:
        raise FindingRefused(
            f"unknown status {status!r}; use one of: {', '.join(FINDING_STATUSES)}"
        )
    query = select(Finding).where(Finding.company_id == company_id)
    if status is not None:
        query = query.where(Finding.status == status)
    return list(session.exec(query.order_by(Finding.id.desc())).all())


def open_findings(
    session: Session, company_id: int, *, selectable_only: bool = False
) -> list[Finding]:
    """The findings no cycle has settled yet, newest first.

    With *selectable_only*, only what a cycle may be started for (§1.4 of the
    design): a target measure, and no recorded revert of the same
    specification — the rule ``procedures.propose(skip_failed=True)`` follows
    for procedures.
    """
    rows = list_findings(session, company_id, status="open")
    if not selectable_only:
        return rows
    return [
        finding
        for finding in rows
        if finding.target_measure and may_start(session, company_id, finding).allowed
    ]


# ── The cycle's outcomes: observations the platform writes ───────────────────


def record_outcome(
    session: Session,
    finding: Finding,
    *,
    produced_by: str,
    decision: str,
    baseline_sha: str,
    candidate_sha: str,
    reasons: list[Any] | None = None,
    cycle: str | int | None = None,
    date: datetime | None = None,
) -> Finding:
    """One cycle's outcome on *finding* (REQ-18): the decision (``keep``,
    ``revert`` or ``unmeasured``), its reasons, the baseline's and the
    candidate's sha, and the date.

    An observation the platform writes: any other *produced_by* — a model, an
    agent, the build under test — is refused (REQ-27), and so are a decision
    outside ``OUTCOME_DECISIONS``, a missing sha and a finding a person
    closed. The finding's status follows the decision; the outcomes stay a
    list, one entry per cycle.
    """
    if produced_by != PLATFORM:
        raise FindingRefused(
            "an outcome is an observation the platform writes; "
            f"produced_by was {produced_by!r}"
        )
    if decision not in OUTCOME_DECISIONS:
        raise FindingRefused(
            f"the decision is one of {', '.join(OUTCOME_DECISIONS)}, not {decision!r}"
        )
    if not (isinstance(baseline_sha, str) and baseline_sha.strip()):
        raise FindingRefused("the outcome names no baseline sha")
    if not (isinstance(candidate_sha, str) and candidate_sha.strip()):
        raise FindingRefused("the outcome names no candidate sha")
    if reasons is not None and not isinstance(reasons, list):
        raise FindingRefused("the reasons are a list")
    if finding.status == "closed_by_person":
        raise FindingRefused("a person closed this finding; its outcomes are settled")
    entry = {
        "cycle": cycle,
        "decision": decision,
        "reasons": list(reasons or []),
        "baseline_sha": baseline_sha,
        "candidate_sha": candidate_sha,
        "date": (date or datetime.now(UTC)).isoformat(),
        "produced_by": PLATFORM,
    }
    finding.outcomes = [*(finding.outcomes or []), entry]
    finding.status = STATUS_BY_DECISION[decision]
    finding.updated_at = datetime.now(UTC)
    session.add(finding)
    return finding


# ── The status a person or a cycle gives ─────────────────────────────────────


def mark_in_cycle(session: Session, finding: Finding) -> Finding:
    """An open finding a cycle starts for (whether one may: ``may_start``)."""
    if finding.status != "open":
        raise FindingRefused(f"the finding is {finding.status}, not open")
    finding.status = "in_cycle"
    finding.updated_at = datetime.now(UTC)
    session.add(finding)
    return finding


def close_by_person(session: Session, finding: Finding, asked_by: Any) -> Finding:
    """A person takes the finding off the loop; an agent, an API token or
    nobody cannot (the same check ``may_start`` makes)."""
    person = person_who_asked(session, asked_by)
    if person is None:
        raise FindingRefused(
            "a finding is closed by a signed-in person, not an agent or a token"
        )
    if finding.status == "closed_by_person":
        raise FindingRefused("the finding is already closed")
    finding.status = "closed_by_person"
    finding.closed_by = person.email
    finding.updated_at = datetime.now(UTC)
    session.add(finding)
    return finding


# ── REQ-20: the same text as a reverted cycle ────────────────────────────────


def person_who_asked(session: Session, asked_by: Any) -> User | None:
    """The signed-in person behind *asked_by*, or ``None``.

    A person is a ``User`` row that exists: an API token's synthetic user
    (``_api_token_company_id``, a negative id) is refused the way
    ``routers/sod.owner_person`` refuses it, and so is anything else — an
    agent's role name, an ``Agent`` row, a label, a ``User`` that was never
    persisted, or nobody.
    """
    if not isinstance(asked_by, User):
        return None
    if getattr(asked_by, "_api_token_company_id", None) is not None:
        return None
    if asked_by.id is None or asked_by.id <= 0:
        return None
    return session.get(User, asked_by.id)


def _reverted(finding: Finding) -> bool:
    """Whether one of *finding*'s outcomes is a cycle that was reverted."""
    return any(
        isinstance(outcome, dict) and outcome.get("decision") == "revert"
        for outcome in (finding.outcomes or [])
    )


def reverted_twin(session: Session, company_id: int, sha: str) -> Finding | None:
    """The company's earliest finding whose specification has the sha256 *sha*
    and whose cycle was reverted; ``None`` when there is none. Another
    company's findings are never read."""
    rows = session.exec(
        select(Finding)
        .where(Finding.company_id == company_id, Finding.specification_sha256 == sha)
        .order_by(Finding.id)
    ).all()
    return next((finding for finding in rows if _reverted(finding)), None)


def may_start(
    session: Session,
    company_id: int,
    finding_or_specification: Finding | str,
    asked_by: Any = None,
) -> MayStart:
    """REQ-20: whether a cycle may start for this specification.

    *finding_or_specification* is a finding (its sha256 decides) or the
    specification's text (its sha256 is computed here). The same text as a
    specification whose cycle was reverted — after normalisation, so
    whitespace and line endings cannot make a difference — does not start
    again, unless a signed-in person asked (*asked_by*): an agent, an API
    token or nobody gets the refusal, naming the reverted finding.
    """
    if isinstance(finding_or_specification, Finding):
        sha = finding_or_specification.specification_sha256
        if not sha:
            # No specification drafted yet: nothing a revert could match.
            return MayStart(True, "no_specification", None)
    elif isinstance(finding_or_specification, str):
        if not finding_or_specification.strip():
            return MayStart(False, "empty_specification", None)
        sha = specification_sha256(finding_or_specification)
    else:
        return MayStart(False, "not_a_finding_or_specification", None)
    reverted = reverted_twin(session, company_id, sha)
    if reverted is None:
        return MayStart(True, "no_reverted_cycle", None)
    if person_who_asked(session, asked_by) is None:
        return MayStart(False, "specification_reverted", reverted.id)
    return MayStart(True, "person_asked", reverted.id)
