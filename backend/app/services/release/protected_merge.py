"""The integration merge's guard on protected paths (``docs/design/rsi-loop.md``,
section 3.3 layer 3, PR 3 of section 8; REQ-13).

For a company that hosts the factory (``companies.protected_paths_enabled``,
``services/platform/protected_paths_setting.py``), the local merge of a
sprint's integration (``local_merge.integration_merge_spec``, from the web,
the Manager agent or the harness alike) reads the integration's diff from the
workspace daemon (``workspace.git.diff``, git between the merge base and the
pinned commit) and matches every file it shows, and a renamed file's old
path, against the running backend's protected list
(``services/evidence/protected_check.patterns_in_force``). The paths a task
declared, or the integration recorded from its runs, are never read here:
only git's diff counts.

A match is merged only when the integration's approving review (its latest
decision, an ``approve`` of the pinned commit, ``integration_review``) was
recorded by a signed-in Owner (never an API token, never the Manager agent),
names every matched path in ``protected_paths_ack`` and was made on the same
``diff_sha256`` the backend computes now. Otherwise the merge is refused
(409) naming the paths. A diff the daemon cut short is refused too: a file it
did not show could be protected.

Each check is kept on the integration (``details["protected_paths"]``: the
commit, the diff's hash, the matched paths, whether they were acknowledged),
so the Manager agent's inbox sees it as a fact
(``management/manager_inbox_extra._integration_items``); a refusal is audited
as ``integration_merge_refused_protected_paths``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from models.integration import SprintIntegration
from models.integration_review import IntegrationReview
from sqlmodel import Session, select

from services.contracts import protected_paths
from services.evidence import protected_check
from services.platform import protected_paths_setting, sod, teams
from services.platform.agent_service import AgentService

AUDIT_REFUSED = "integration_merge_refused_protected_paths"
DETAILS_KEY = "protected_paths"
OWNER = "Owner"
NOT_A_PERSON = (
    "Protected paths are acknowledged by a signed-in Owner, "
    "never an API token or the Manager agent"
)


class ProtectedMergeRefused(Exception):
    """A merge or an acknowledgement refused: ``status`` is the HTTP status."""

    def __init__(self, status: int, detail: str, paths: list[str] | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.paths = paths or []


def applies(session: Session, company_id: int | None) -> bool:
    """Whether the company's integration merges are checked for protected paths."""
    return protected_paths_setting.enabled(session, company_id)


def _diff_paths(diff: dict[str, Any]) -> Iterable[str]:
    """The parsed files' paths, a rename's source included, then the inventory."""
    for entry in diff.get("files") or []:
        yield entry["path"]
        if entry.get("old_path"):
            yield entry["old_path"]
    yield from diff.get("paths") or []


def _normalized(paths: Iterable[Any]) -> set[str]:
    return {
        p
        for p in (protected_paths.normalize(x) for x in paths if isinstance(x, str))
        if p is not None
    }


def inventory_problem(diff: dict[str, Any]) -> str | None:
    """Why the diff's path inventory cannot be trusted; ``None`` when it can.

    The daemon lists every path with ``git diff --name-status -z``, which no
    quoting hides; the patch parser skips a header git quotes. Each parsed
    path must be listed, and with the whole diff shown every listed path must
    be parsed: a disagreement means a file the parser could not read.
    """
    listed = diff.get("paths")
    if not isinstance(listed, list) or diff.get("paths_truncated"):
        return (
            "The daemon did not report a complete list of the paths this "
            "integration changes, so a protected path could be hidden: a person "
            "merges it (update the workspace daemon)"
        )
    inventory = _normalized(listed)
    parsed: set[str] = set()
    for entry in diff.get("files") or []:
        parsed |= _normalized([entry.get("path"), entry.get("old_path")])
    if parsed - inventory or (not diff.get("truncated") and inventory - parsed):
        unread = sorted((inventory - parsed) | (parsed - inventory))
        return (
            "The daemon's parsed diff and its list of changed paths do not agree "
            f"({', '.join(unread[:10])}), so a protected path could be hidden: "
            "a person merges it"
        )
    return None


def matched(diff: dict[str, Any]) -> list[str]:
    """Pure over the daemon's diff: the protected paths it shows or lists, each once."""
    return [
        m.path
        for m in protected_paths.matches(
            _diff_paths(diff), protected_check.patterns_in_force()
        )
    ]


def _by_owner(session: Session, company_id: int, user_id: int | None) -> bool:
    # A token's synthetic user has a negative id; the Manager agent has none.
    if not user_id or user_id < 0:
        return False
    return teams.member_role(session, company_id, user_id) == OWNER


def validate_ack(
    session: Session,
    row: SprintIntegration,
    diff: dict[str, Any],
    paths: list[str],
    actor: sod.Person | None,
) -> list[str]:
    """The protected paths an approval acknowledges, normalized, or why not.

    Only a signed-in Owner records one (403 otherwise), and each path must be
    a protected path the daemon's diff of the integration shows (422).
    """
    if actor is None or actor.token_id is not None:
        raise ProtectedMergeRefused(403, NOT_A_PERSON)
    if not _by_owner(session, row.company_id, actor.user_id):
        raise ProtectedMergeRefused(403, NOT_A_PERSON)
    found = set(matched(diff))
    named: list[str] = []
    for raw in paths:
        path = protected_paths.normalize(raw) if isinstance(raw, str) else None
        if path is None or path not in found:
            raise ProtectedMergeRefused(
                422, f"{raw} is not a protected path this integration changes"
            )
        if path not in named:
            named.append(path)
    return named


def _latest_ack(session: Session, row: SprintIntegration) -> IntegrationReview | None:
    rows = session.exec(
        select(IntegrationReview)
        .where(
            IntegrationReview.integration_id == row.id,
            IntegrationReview.company_id == row.company_id,
            IntegrationReview.event == "approve",
        )
        .order_by(IntegrationReview.created_at.desc(), IntegrationReview.id.desc())
    ).all()
    return next((r for r in rows if r.protected_paths_ack), None)


def _missing_ack(
    session: Session,
    row: SprintIntegration,
    diff: dict[str, Any],
    approval: IntegrationReview | None,
    found: list[str],
) -> str | None:
    """Why *found* may not be merged yet; ``None`` when an Owner acknowledged it."""
    lead = f"This integration changes protected paths ({', '.join(found)}): "
    ask = (
        "an Owner names each of them (protected_paths_ack) in an approving "
        f"review of commit {(row.commit or '')[:12]} before it is merged"
    )
    if approval is None:
        earlier = _latest_ack(session, row)
        if earlier is not None and earlier.commit != row.commit:
            return lead + (
                f"the acknowledgement was of commit {earlier.commit[:12]}, "
                f"not this one; {ask}"
            )
        return lead + ask
    acked = set(approval.protected_paths_ack or [])
    if not acked:
        return lead + f"the approving review acknowledges none of them; {ask}"
    if not _by_owner(session, row.company_id, approval.author_user_id):
        return (
            lead + f"the acknowledgement was not recorded by a signed-in Owner; {ask}"
        )
    if approval.diff_sha256 != diff.get("diff_sha256"):
        return lead + f"the diff changed since it was acknowledged; {ask}"
    missing = [p for p in found if p not in acked]
    if missing:
        return lead + f"the acknowledgement does not name {', '.join(missing)}; {ask}"
    return None


def _record(
    session: Session,
    row: SprintIntegration,
    diff: dict[str, Any],
    found: list[str],
    approval: IntegrationReview | None,
    refusal: str | None,
) -> None:
    observation = {
        "commit": row.commit,
        "diff_sha256": diff.get("diff_sha256"),
        "matched": found,
        "truncated": bool(diff.get("truncated")),
        # The inventory missing or disagreeing with the parsed files.
        "incomplete": inventory_problem(diff) is not None,
        "acknowledged": bool(found) and refusal is None,
        "review_id": approval.id if approval is not None else None,
        "refused": refusal,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    # A new dict, so the JSON column is seen as changed.
    row.details = {**(row.details or {}), DETAILS_KEY: observation}
    session.add(row)


def guard(
    session: Session,
    row: SprintIntegration,
    diff: dict[str, Any] | None,
    approval: IntegrationReview | None,
) -> list[str]:
    """Refuse the merge of *row* unless its protected paths were acknowledged.

    *diff* is the daemon's, checked (``integration_review.observed``);
    *approval* is the review the merge would stand on. Returns the matched
    paths (empty when the company does not host the factory). A refusal is
    recorded on the integration, audited and committed before it is raised,
    so it survives the caller's rollback.
    """
    if not applies(session, row.company_id):
        return []
    if diff is None or diff.get("commit") != row.commit:
        raise ProtectedMergeRefused(
            409,
            "This company's protected paths are checked from the integration's "
            "diff, and it was not read for this commit: merge again",
        )
    found = matched(diff)
    refusal = None
    if diff.get("truncated"):
        refusal = (
            "The daemon's diff of this integration was cut short, so a protected "
            "path could be among the files it did not show: a person merges it"
        )
    else:
        refusal = inventory_problem(diff)
    if refusal is None and found:
        refusal = _missing_ack(session, row, diff, approval, found)
    _record(session, row, diff, found, approval, refusal)
    if refusal is None:
        return found
    AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"sprint:{row.sprint_id}:integration",
        agent_id="platform:protected_paths",
        action=AUDIT_REFUSED,
        details={
            "company_id": str(row.company_id),
            "sprint_id": row.sprint_id,
            "integration_id": row.id,
            "review_id": approval.id if approval is not None else None,
        },
        result={
            "commit": row.commit,
            "diff_sha256": diff.get("diff_sha256"),
            "matched": found,
            "truncated": bool(diff.get("truncated")),
            "reason": refusal,
        },
        decision_signature=None,
        cost_tokens=0,
    )
    session.commit()
    raise ProtectedMergeRefused(409, refusal, found)


def observation(row: SprintIntegration | None) -> dict[str, Any] | None:
    """The last check kept on *row*, when it was of the integration's commit."""
    if row is None:
        return None
    kept = (row.details or {}).get(DETAILS_KEY)
    if isinstance(kept, dict) and kept.get("commit") == row.commit:
        return kept
    return None


def facts(
    session: Session, row: SprintIntegration, approval: IntegrationReview | None
) -> dict[str, Any] | None:
    """What the Manager agent sees of the check: ``None`` when it does not apply.

    ``protected_paths`` is ``None`` until a merge attempt read the diff of
    this commit (unknown, not absent); ``protected_paths_acked`` is read again
    from *approval*, so an Owner's acknowledgement after a refusal shows.
    """
    if not applies(session, row.company_id):
        return None
    kept = observation(row)
    if kept is None:
        return {"protected_paths": None, "protected_paths_acked": False}
    found = [str(p) for p in kept.get("matched") or []]
    unreadable = bool(kept.get("truncated") or kept.get("incomplete"))
    acked = bool(found) and not unreadable
    if acked:
        seen = {"diff_sha256": kept.get("diff_sha256")}
        acked = _missing_ack(session, row, seen, approval, found) is None
    return {
        "protected_paths": found,
        "protected_paths_acked": acked,
        "protected_paths_truncated": unreadable,
        # Both change the item, so the agent is asked again after either.
        "protected_paths_diff": str(kept.get("diff_sha256") or ""),
        "protected_paths_review_id": approval.id if approval is not None else None,
    }


def refresh(session: Session, row: SprintIntegration, diff: dict[str, Any]) -> None:
    """Keep *diff*'s check when it differs from the one kept on *row*.

    A review reads the diff again; when the base absorbed part of the
    integration since a refused merge, the earlier attempt's paths and digest
    no longer describe it, so they are replaced (committed with the review)
    and the item can move on to a new guarded attempt.
    """
    if not applies(session, row.company_id) or diff.get("commit") != row.commit:
        return
    kept = observation(row)
    if kept is not None and kept.get("diff_sha256") == diff.get("diff_sha256"):
        return
    _record(session, row, diff, matched(diff), None, None)
