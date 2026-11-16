"""A person's review of a sprint's integration, as on a run (F7 of the self-hosting
retrospective; the run's own is ``services/loop/run_review.py``).

The integration (``fabrique/sprint-<id>``, ``services/release/integration.py``)
is the one request of a sprint a person should read. A Manager leaves comments
on the lines of its diff and submits a review: ``comment``, ``approve`` or
``request_changes``. Each review is recorded (``IntegrationReview``) and
audited as ``integration_review_<event>`` with its actor; it carries the
comments still open.

**The diff is observed, never sent.** The workspace daemon reads it with git
(``workspace.git.diff``, ``vscode-daemon/git_diff.py``): the files between the
merge base of the base branch and the integration's pinned commit, their
hunks, and the sha256 of each file on both sides. A line comment is accepted
only on a line inside a hunk of that diff, on the side it names, and keeps
the file's hash on that side; a review keeps the commit, the merge base and
``diff_sha256``, which the backend computes from the daemon's per-file hashes.
The client sends only words and where they go.

**The merge gate.** With ``companies.integration_review_required`` (off by
default; an Owner turns it on, audited), merging the integration into the base
(``local_merge.integration_merge_spec``, from the web, the Manager agent or
the harness alike) needs an approving review of that integration: the latest
``approve`` or ``request_changes`` on it is an ``approve`` of its commit. The
local merge records the review it relied on. For a company that hosts the
factory, an approval may also carry ``protected_paths_ack``: the protected
paths of the diff a signed-in Owner names (``protected_merge.validate_ack``),
without which such an integration is not merged.

**Segregation of duties** (issue #543): an approval is a release decision, so
``sod.enforce`` holds it to the rule (``sod.RELEASE``) with the sprint's
starter as requester; the person who started the sprint does not approve its
integration (403, audited ``sod_refused``) unless the company's mode or its
single-person exception says otherwise.

**A published integration.** Publishing opens a pull or merge request on the
forge, whose own review decides a merge there; nothing here is posted to it
and nothing is read back from it. A review here records the request's URL it
was made beside (``published_url``) and gates only the local merge.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from models.company import Company
from models.integration import SprintIntegration
from models.integration_review import IntegrationReview, IntegrationReviewComment
from models.sprint import Sprint
from sqlmodel import Session, select

from services.loop import sod_requesters
from services.platform import sod
from services.platform.agent_service import AgentService
from services.release import protected_merge
from services.workspace.workspace_daemon import daemon_request

DIFF_SUBJECT = "workspace.git.diff"
# git diff and one cat-file batch, on the person's machine.
DIFF_TIMEOUT_SECONDS = 60.0
EVENTS = ("comment", "approve", "request_changes")
OUTCOMES = {
    "comment": "recorded",
    "approve": "approved",
    "request_changes": "changes_requested",
}
NOT_APPROVABLE = {
    "conflict": "The integration stopped at a conflict: integrate again once it is resolved",
    "tests_failed": "The suite failed on the integrated branch: it cannot be approved",
}
GATE_DETAIL = (
    "This company merges an integration only once a person approved it: "
    "review the integration's diff and approve it first"
)
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class IntegrationReviewRefused(Exception):
    """A review or a comment that cannot be recorded: ``status`` is the HTTP status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# -- The diff, from the daemon --------------------------------------------------


def diff_spec(session: Session, row: SprintIntegration) -> dict[str, Any]:
    """The ``workspace.git.diff`` request for *row*, or why there is none."""
    if not row.repository or not row.commit:
        raise IntegrationReviewRefused(
            409, "This integration names no commit to review: integrate the sprint"
        )
    base = row.base if row.base and row.base != "HEAD" else ""
    if not base:
        company = session.get(Company, row.company_id)
        base = (company.git_base_branch or "") if company else ""
    return {"workspace_root": row.repository, "base": base, "commit": row.commit}


def _sha256_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and _SHA256.match(value):
        return value
    raise IntegrationReviewRefused(502, "The workspace daemon sent an unreadable diff")


def _count(value: Any) -> int:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value or 0
    raise IntegrationReviewRefused(502, "The workspace daemon sent an unreadable diff")


def observed(reply: dict[str, Any], row: SprintIntegration) -> dict[str, Any]:
    """The daemon's diff of *row*, checked, with the backend's own ``diff_sha256``."""
    unreadable = IntegrationReviewRefused(
        502, "The workspace daemon sent an unreadable diff"
    )
    files = reply.get("files")
    merge_base = str(reply.get("merge_base") or "")
    if not reply.get("ok") or not isinstance(files, list) or not _SHA.match(merge_base):
        raise unreadable
    if reply.get("commit") != row.commit:
        raise IntegrationReviewRefused(
            502, "The workspace daemon read another commit than the integration's"
        )
    clean = []
    for f in files:
        if (
            not isinstance(f, dict)
            or not isinstance(f.get("path"), str)
            or not f["path"]
        ):
            raise unreadable
        patch = f.get("patch")
        clean.append(
            {
                "path": f["path"],
                "old_path": f.get("old_path")
                if isinstance(f.get("old_path"), str)
                else None,
                "change": str(f.get("change") or ""),
                "additions": _count(f.get("additions")),
                "deletions": _count(f.get("deletions")),
                "patch": patch if isinstance(patch, str) else None,
                "omitted": f.get("omitted")
                if isinstance(f.get("omitted"), str)
                else None,
                "sha256_old": _sha256_or_none(f.get("sha256_old")),
                "sha256_new": _sha256_or_none(f.get("sha256_new")),
            }
        )
    digest = hashlib.sha256(
        json.dumps(
            {
                "commit": row.commit,
                "merge_base": merge_base,
                "files": [
                    [f["path"], f["old_path"], f["sha256_old"], f["sha256_new"]]
                    for f in clean
                ],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "integration_id": row.id,
        "commit": row.commit,
        "merge_base": merge_base,
        "base": str(reply.get("base") or ""),
        "base_commit": str(reply.get("base_commit") or ""),
        "files": clean,
        "truncated": bool(reply.get("truncated")),
        "diff_sha256": digest,
    }


async def read_diff(nats_client: Any, session: Session, row: SprintIntegration) -> dict:
    """Ask the daemon for *row*'s diff; raises the daemon's errors as they come."""
    reply = await daemon_request(
        nats_client, DIFF_SUBJECT, diff_spec(session, row), timeout=DIFF_TIMEOUT_SECONDS
    )
    return observed(reply, row)


def hunk_lines(patch: str | None) -> tuple[set[int], set[int]]:
    """Pure: the old and the new line numbers a unified patch shows."""
    old: set[int] = set()
    new: set[int] = set()
    old_at = new_at = 0
    for line in (patch or "").split("\n"):
        match = _HUNK.match(line)
        if match:
            old_at, new_at = int(match.group(1)), int(match.group(3))
        elif line.startswith("+") and new_at:
            new.add(new_at)
            new_at += 1
        elif line.startswith("-") and old_at:
            old.add(old_at)
            old_at += 1
        elif line.startswith(" ") and (old_at or new_at):
            old.add(old_at)
            new.add(new_at)
            old_at, new_at = old_at + 1, new_at + 1
    return old, new


def anchor(diff: dict, path: str | None, line: int | None, side: str) -> str | None:
    """Where a comment goes, checked against the daemon's diff; the file's hash there."""
    if path is None:
        return None
    entry = next((f for f in diff["files"] if f["path"] == path), None)
    if entry is None:
        raise IntegrationReviewRefused(
            422, f"{path} is not among the files this integration changes"
        )
    file_hash = entry["sha256_new"] if side == "new" else entry["sha256_old"]
    if line is None:
        return file_hash
    old, new = hunk_lines(entry["patch"])
    if line not in (new if side == "new" else old):
        raise IntegrationReviewRefused(
            422, f"Line {line} ({side}) of {path} is not in the integration's diff"
        )
    return file_hash


# -- Comments and reviews -------------------------------------------------------


def add_comment(
    session: Session,
    row: SprintIntegration,
    diff: dict,
    *,
    path: str | None,
    line: int | None,
    side: str,
    body: str,
    author_name: str,
    author_user_id: int | None,
) -> IntegrationReviewComment:
    file_hash = anchor(diff, path, line, side)
    comment = IntegrationReviewComment(
        company_id=row.company_id,
        sprint_id=row.sprint_id,
        integration_id=row.id,
        path=path,
        line=line,
        side=side,
        body=body,
        commit=diff["commit"],
        file_sha256=file_hash,
        author_name=author_name,
        author_user_id=author_user_id,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return comment


def comments_of(
    session: Session, row: SprintIntegration
) -> list[IntegrationReviewComment]:
    return list(
        session.exec(
            select(IntegrationReviewComment)
            .where(
                IntegrationReviewComment.integration_id == row.id,
                IntegrationReviewComment.company_id == row.company_id,
            )
            .order_by(IntegrationReviewComment.created_at, IntegrationReviewComment.id)
        ).all()
    )


def reviews_of(session: Session, row: SprintIntegration) -> list[IntegrationReview]:
    return list(
        session.exec(
            select(IntegrationReview)
            .where(
                IntegrationReview.integration_id == row.id,
                IntegrationReview.company_id == row.company_id,
            )
            .order_by(IntegrationReview.created_at, IntegrationReview.id)
        ).all()
    )


def resolve_comment(
    session: Session, row: SprintIntegration, comment_id: int
) -> IntegrationReviewComment:
    comment = session.get(IntegrationReviewComment, comment_id)
    if (
        comment is None
        or comment.integration_id != row.id
        or comment.company_id != row.company_id
    ):
        raise IntegrationReviewRefused(404, "Review comment not found")
    if comment.resolved_at is None:
        comment.resolved_at = _now()
        session.add(comment)
        session.commit()
        session.refresh(comment)
    return comment


def _segregate(
    session: Session, row: SprintIntegration, actor: sod.Person | None
) -> str:
    sprint = session.get(Sprint, row.sprint_id)
    try:
        return sod.enforce(
            session,
            row.company_id,
            sod.RELEASE,
            {
                "kind": "sprint_integration_review",
                "id": row.id,
                "sprint_id": row.sprint_id,
            },
            actor,
            sod_requesters.of_sprint(session, sprint),
        )
    except sod.SodRefused as refused:
        raise IntegrationReviewRefused(refused.status, refused.detail) from refused


def _ack(session, row, diff, event, paths, actor) -> list[str] | None:
    """The protected paths an Owner acknowledges in this approval, checked."""
    if not paths:
        return None
    if event != "approve":
        raise IntegrationReviewRefused(
            422, "Protected paths are acknowledged in an approving review"
        )
    if not protected_merge.applies(session, row.company_id):
        raise IntegrationReviewRefused(
            409, "This company does not hold protected paths: nothing to acknowledge"
        )
    try:
        return protected_merge.validate_ack(session, row, diff, paths, actor)
    except protected_merge.ProtectedMergeRefused as refused:
        raise IntegrationReviewRefused(refused.status, refused.detail) from refused


def submit_review(
    session: Session,
    row: SprintIntegration,
    diff: dict,
    *,
    event: str,
    body: str | None,
    author_name: str,
    author_user_id: int | None,
    actor: sod.Person | None,
    protected_paths_ack: list[str] | None = None,
) -> IntegrationReview:
    """Record a review of *row* as *diff* shows it, carrying its open comments."""
    if event not in EVENTS:
        raise IntegrationReviewRefused(422, f"event is one of {', '.join(EVENTS)}")
    ack = _ack(session, row, diff, event, protected_paths_ack, actor)
    comments = [
        c
        for c in comments_of(session, row)
        if c.review_id is None and not c.resolved_at
    ]
    if event == "request_changes" and not body and not comments:
        raise IntegrationReviewRefused(
            422, "Say what to change: a comment on a line, or the review's text."
        )
    sod_outcome = None
    if event == "approve":
        if row.status in NOT_APPROVABLE:
            raise IntegrationReviewRefused(409, NOT_APPROVABLE[row.status])
        sod_outcome = _segregate(session, row, actor)
    published = (row.publication or {}).get("url")
    review = IntegrationReview(
        company_id=row.company_id,
        sprint_id=row.sprint_id,
        integration_id=row.id,
        event=event,
        body=body,
        outcome=OUTCOMES[event],
        commit=diff["commit"],
        merge_base=diff["merge_base"],
        diff_sha256=diff["diff_sha256"],
        files=len(diff["files"]),
        published_url=published if isinstance(published, str) else None,
        sod=sod_outcome,
        protected_paths_ack=ack,
        author_name=author_name,
        author_user_id=author_user_id,
    )
    session.add(review)
    session.flush()
    for comment in comments:
        comment.review_id = review.id
        session.add(comment)
    audit = AgentService().audit_interaction(
        session=session,
        company_id=row.company_id,
        interaction_id=f"sprint:{row.sprint_id}:integration",
        agent_id=f"human:{author_name}",
        action=f"integration_review_{event}",
        details={
            "company_id": str(row.company_id),
            "sprint_id": row.sprint_id,
            "integration_id": row.id,
            "review_id": review.id,
            "user_id": author_user_id,
            "token_id": actor.token_id if actor else None,
            "sod": sod_outcome,
        },
        result={
            "outcome": review.outcome,
            "comments": [c.id for c in comments],
            "commit": review.commit,
            "merge_base": review.merge_base,
            "diff_sha256": review.diff_sha256,
            "protected_paths_ack": ack,
            "body": body,
        },
        decision_signature=None,
        cost_tokens=0,
    )
    review.audit_log_id = audit.id
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


# -- The gate and the state -----------------------------------------------------


def required(session: Session, company_id: int) -> bool:
    company = session.get(Company, company_id)
    return bool(company and company.integration_review_required)


def approving_review(
    session: Session, row: SprintIntegration
) -> IntegrationReview | None:
    """The approval *row* stands on: its latest decision, when that approves its commit."""
    decision = session.exec(
        select(IntegrationReview)
        .where(
            IntegrationReview.integration_id == row.id,
            IntegrationReview.company_id == row.company_id,
            IntegrationReview.event.in_(("approve", "request_changes")),
        )
        .order_by(IntegrationReview.created_at.desc(), IntegrationReview.id.desc())
    ).first()
    if decision is None or decision.event != "approve" or decision.commit != row.commit:
        return None
    return decision


def require_approval(session: Session, row: SprintIntegration) -> None:
    """Refuse a merge the company wants approved first; nothing when it does not ask."""
    if required(session, row.company_id) and approving_review(session, row) is None:
        raise IntegrationReviewRefused(409, GATE_DETAIL)


def _brief(review: IntegrationReview | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "id": review.id,
        "event": review.event,
        "outcome": review.outcome,
        "author_name": review.author_name,
        "commit": review.commit,
        "created_at": review.created_at,
    }


def state(session: Session, company_id: int, row: SprintIntegration | None) -> dict:
    """What the integration's view shows of its reviews."""
    reviews = reviews_of(session, row) if row is not None else []
    approval = approving_review(session, row) if row is not None else None
    open_comments = (
        sum(
            1
            for c in comments_of(session, row)
            if c.review_id is None and not c.resolved_at
        )
        if row is not None
        else 0
    )
    published = (row.publication or {}).get("url") if row is not None else None
    return {
        "integration_id": row.id if row is not None else None,
        "required": required(session, company_id),
        "approved": approval is not None,
        "approving_review_id": approval.id if approval else None,
        "latest": _brief(reviews[-1] if reviews else None),
        "reviews": len(reviews),
        "open_comments": open_comments,
        "published_url": published if isinstance(published, str) else None,
        # The merge guard's last check (a company that hosts the factory).
        "protected_paths": protected_merge.facts(session, row, approval)
        if row is not None
        else None,
    }


def set_required(
    session: Session, company_id: int, value: bool, *, by: str, user_id: int | None
) -> dict[str, Any]:
    """Turn the merge gate on or off for the company; a change is audited."""
    company = session.get(Company, company_id)
    if company is None:
        raise IntegrationReviewRefused(404, "Company not found")
    before = bool(company.integration_review_required)
    if before != value:
        company.integration_review_required = value
        session.add(company)
        AgentService().audit_interaction(
            session=session,
            company_id=company_id,
            interaction_id=f"company:{company_id}:integration_review",
            agent_id=f"human:{by}",
            action="integration_review_requirement_changed",
            details={"company_id": str(company_id), "user_id": user_id},
            result={"from": before, "to": value},
            decision_signature=None,
            cost_tokens=0,
        )
        session.commit()
    return {"company_id": company_id, "required": value}
