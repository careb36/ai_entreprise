"""Bringing a published run's review comments back from GitHub or GitLab.

Once a run's branch is a pull request or merge request
(``services/git_publish.py``), people may review it there. A Manager brings
those comments to the run: the workspace daemon reads them with the person's
``gh`` or ``glab`` login (``vscode-daemon/platform_reviews.py``,
``workspace.git.reviews``) and each one becomes a ``ReviewComment`` with its
``source``, its id there and its page. Reading again updates a comment that
was edited there while it still waits here, and never duplicates one.

They are a person's words, like any comment written here: they appear on
the lines they are about, and the next review a Manager submits carries the
open ones to whoever must act. A review submitted on the platform decides
nothing here; its state (``CHANGES_REQUESTED``, ``APPROVED``) is kept beside
its text.
"""

from __future__ import annotations

from typing import Any

from models.agent_run import AgentRun
from models.review import ReviewComment, RunPublication
from sqlmodel import Session, select

from services.review_changes import run_changes

SOURCES = ("github", "gitlab")
MAX_BODY_CHARS = 4000


class SyncRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def sync_spec(
    session: Session, run: AgentRun, publication: RunPublication
) -> dict[str, Any]:
    """The ``workspace.git.reviews`` request for *publication*, or why there is none."""
    if (
        publication.provider not in SOURCES
        or publication.how == "compare"
        or not publication.url
    ):
        raise SyncRefused(
            409,
            "This publication opened no pull or merge request to read comments from: "
            "open it on the platform, then publish again.",
        )
    git = run_changes(session, run).get("git") or {}
    return {
        "workspace_root": git.get("repository") or "",
        "provider": publication.provider,
        "url": publication.url,
    }


def _body(item: dict[str, Any]) -> str:
    body = str(item.get("body") or "").strip()
    if not body and item.get("state"):
        body = str(item["state"]).replace("_", " ").lower()
    return body[:MAX_BODY_CHARS]


def import_comments(
    session: Session, run: AgentRun, publication: RunPublication, reply: dict[str, Any]
) -> dict[str, Any]:
    """Store what the daemon read: new comments added, waiting ones brought up to date."""
    items = reply.get("comments")
    if not reply.get("ok") or not isinstance(items, list):
        raise SyncRefused(502, "The workspace daemon sent an unreadable answer")
    # Two syncs of one run at once (two replicas, two clicks) would both miss
    # an id and insert it twice: the run's row is locked until the commit.
    session.exec(select(AgentRun).where(AgentRun.id == run.id).with_for_update()).one()
    existing = {
        c.external_id: c
        for c in session.exec(
            select(ReviewComment).where(
                ReviewComment.agent_run_id == run.id,
                ReviewComment.company_id == run.company_id,
                ReviewComment.external_id.is_not(None),
            )
        ).all()
    }
    added = updated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        external_id = str(item.get("external_id") or "")
        body = _body(item)
        if not external_id.startswith(f"{publication.provider}:") or not body:
            continue
        line = (
            item.get("line")
            if isinstance(item.get("line"), int) and item["line"] > 0
            else None
        )
        path = str(item["path"]) if item.get("path") else None
        fields = {
            "path": path,
            "line": line if path else None,
            "side": "old" if item.get("side") == "old" else "new",
            "body": body,
            "external_url": str(item["url"])
            if str(item.get("url") or "").startswith("https://")
            else None,
            "external_state": str(item["state"]) if item.get("state") else None,
        }
        comment = existing.get(external_id)
        if comment is None:
            comment = ReviewComment(
                company_id=run.company_id,
                agent_run_id=run.id,
                author_name=str(item.get("author") or "").strip()[:200]
                or publication.provider,
                source=publication.provider,
                external_id=external_id,
                **fields,
            )
            added += 1
        elif comment.review_id is None and any(
            getattr(comment, k) != v for k, v in fields.items()
        ):
            # Edited on the platform while it still waits here; once a review
            # carried it, what it said then is what the task received.
            for key, value in fields.items():
                setattr(comment, key, value)
            updated += 1
        else:
            continue
        session.add(comment)
    session.commit()
    return {
        "publication_id": publication.id,
        "read": len(items),
        "added": added,
        "updated": updated,
        "truncated": bool(reply.get("truncated")),
    }
