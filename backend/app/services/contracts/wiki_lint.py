"""The wiki's lint: three deterministic rules, no model (issue #445).

Each rule takes a page as plain data and the observations the caller read
(the evidence's records, the daemon's hashes) and returns the reasons the
page no longer holds; a page with a reason is marked ``stale`` and left out
of prompts until it is revised (``services/learning/wiki_store.py``).

1. ``file_findings``: a ``[file:path@sha]`` citation whose file the daemon
   reports missing (``file_missing``), or whose latest observed sha256 does
   not start with the cited one (``file_changed``). A file nobody observed is
   not judged.
2. ``module_findings``: a verified run, observed after the page's current
   version was written, changed a file in the page's module (its ``path``) or
   a file the page cites (``module_changed``).
3. ``contradictions``: two pages in force give the same fact key different
   values (``contradiction``); both are flagged, each naming the other.

The reasons are data (``{rule, ...}``), shown beside the page and kept on it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.contracts import wiki, wiki_templates

MAX_REASONS = 20


def in_path(path: str, prefix: str | None) -> bool:
    """*path* is *prefix* or under the folder *prefix*."""
    prefix = (prefix or "").strip().rstrip("/")
    path = (path or "").strip().rstrip("/")
    return bool(prefix) and (path == prefix or path.startswith(prefix + "/"))


def cited_files(body: str) -> list[dict[str, str]]:
    """The body's file citations, ``{path, sha256}``, one per path (the first)."""
    seen: dict[str, str] = {}
    for c in wiki.citations(body):
        if c["kind"] == "file" and c["ref"] not in seen:
            seen[c["ref"]] = c["sha256"]
    return [{"path": p, "sha256": s} for p, s in seen.items()]


def file_findings(
    body: str, observed: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rule 1. *observed* maps a path to ``{status: present|missing, sha256}``
    (the latest observation: the daemon's hash of the file on disk, else the
    latest verified run's write)."""
    reasons: list[dict[str, Any]] = []
    for cited in cited_files(body):
        seen = observed.get(cited["path"])
        if not seen:
            continue
        if seen.get("status") == "missing":
            reasons.append({"rule": "file_missing", "path": cited["path"]})
            continue
        sha = str(seen.get("sha256") or "").lower()
        if sha and not sha.startswith(cited["sha256"]):
            reasons.append(
                {
                    "rule": "file_changed",
                    "path": cited["path"],
                    "cited": cited["sha256"],
                    "observed": sha,
                    "observed_by": seen.get("observed_by"),
                }
            )
    return reasons[:MAX_REASONS]


def module_findings(
    page: dict[str, Any],
    written_at: datetime | None,
    verified_writes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rule 2. *verified_writes* are ``{run_id, path, observed_at}`` of verified
    runs; *written_at* when the page's current version was written."""
    if written_at is None:
        return []
    cited = {f["path"] for f in cited_files(page.get("body") or "")}
    module_path = page.get("module_path")
    reasons: list[dict[str, Any]] = []
    seen_runs: set[int] = set()
    for write in sorted(verified_writes, key=lambda w: w.get("run_id") or 0):
        at = write.get("observed_at")
        if at is None or at <= written_at or write.get("run_id") in seen_runs:
            continue
        path = str(write.get("path") or "")
        if path in cited or in_path(path, module_path):
            seen_runs.add(write.get("run_id"))
            reasons.append(
                {"rule": "module_changed", "run_id": write.get("run_id"), "path": path}
            )
    return reasons[:MAX_REASONS]


def contradictions(pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Rule 3. *pages* are ``{slug, body}`` of the pages in force; per slug,
    the facts it gives that another page gives differently."""
    by_key: dict[str, list[tuple[str, str]]] = {}
    for page in pages:
        for fact in wiki.facts(page.get("body") or ""):
            by_key.setdefault(fact["key"], []).append((page["slug"], fact["value"]))
    reasons: dict[str, list[dict[str, Any]]] = {}
    for key, values in sorted(by_key.items()):
        for slug, value in values:
            for other_slug, other_value in values:
                if other_slug == slug or wiki.normalize_fact(
                    other_value
                ) == wiki.normalize_fact(value):
                    continue
                reasons.setdefault(slug, []).append(
                    {
                        "rule": "contradiction",
                        "key": key,
                        "value": value,
                        "other_page": other_slug,
                        "other_value": other_value,
                    }
                )
    return {slug: found[:MAX_REASONS] for slug, found in reasons.items()}


def describe(reason: dict[str, Any], language: str = "en") -> str:
    """One line for a reason, in the project's language (``wiki_templates``)."""
    return wiki_templates.describe_reason(reason, language)
