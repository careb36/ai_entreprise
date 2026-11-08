"""What a run changed, the way a code review shows it: files, line counts, hunks.

The changes come from one of two sources, and never from a claim:

- ``workspace``: the per-file diffs the workspace daemon observed when it
  wrote the files (``vscode-daemon/file_diff.py``), read from the latest
  verification whose evidence carries the daemon's report;
- ``pull_request``: the unified diff La Fabrique fetched from GitHub for an
  ingested pull request, which is the run's artifact.

A producer that submitted a diff as its artifact, with no observation of a
disk, gets ``artifact``: what it proposes, labelled as such.

The files the producer declared travel apart: ``declared_not_observed``
lists the ones no observation covers, so a review sees the gap instead of a
claim dressed as a change.
"""

from __future__ import annotations

import re
from typing import Any

from models.agent_run import AgentRun, Verification
from sqlmodel import Session, select

# More than this and a review is not the right tool; the rest is counted.
MAX_FILES = 300
MAX_PATCH_CHARS = 200_000

_HEADER = re.compile(r"^diff --git a/(.*?) b/(.*)$")


def _count(patch_lines: list[str]) -> tuple[int, int]:
    additions = sum(1 for line in patch_lines if line.startswith("+"))
    deletions = sum(1 for line in patch_lines if line.startswith("-"))
    return additions, deletions


def _section(header: re.Match, lines: list[str]) -> dict[str, Any]:
    old_path, new_path = header.group(1), header.group(2)
    change = "modified"
    omitted = None
    patch_start = None
    for index, line in enumerate(lines):
        if line.startswith("@@"):
            patch_start = index
            break
        if line.startswith("new file mode") or line == "--- /dev/null":
            change = "added"
        elif line.startswith("deleted file mode") or line == "+++ /dev/null":
            change = "deleted"
        elif line.startswith("rename from "):
            change = "renamed"
            old_path = line[len("rename from ") :]
        elif line.startswith("rename to "):
            new_path = line[len("rename to ") :]
        elif line.startswith(("Binary files ", "GIT binary patch")):
            omitted = "binary"
    patch_lines = lines[patch_start:] if patch_start is not None else []
    # A trailing empty line is the separator before the next section, not content.
    while patch_lines and patch_lines[-1] == "":
        patch_lines.pop()
    additions, deletions = _count(patch_lines)
    patch = "\n".join(patch_lines)
    if len(patch) > MAX_PATCH_CHARS:
        patch, omitted = "", "too_large"
    return {
        "path": new_path if change != "deleted" else old_path,
        "old_path": old_path if change == "renamed" else None,
        "change": change,
        "additions": additions,
        "deletions": deletions,
        "patch": patch or None,
        "omitted": omitted,
    }


def parse_unified_diff(text: str | None) -> list[dict[str, Any]]:
    """One entry per file of a ``git diff``-style text, in its order."""
    files: list[dict[str, Any]] = []
    header: re.Match | None = None
    body: list[str] = []
    for line in (text or "").split("\n"):
        match = _HEADER.match(line)
        if match:
            if header is not None:
                files.append(_section(header, body))
            header, body = match, []
        elif header is not None:
            body.append(line)
    if header is not None:
        files.append(_section(header, body))
    return files


def _workspace_file(write: dict[str, Any]) -> dict[str, Any]:
    path = write.get("path") or ""
    if write.get("status") != "written":
        return {
            "path": path,
            "old_path": None,
            "change": "not_written",
            "status": write.get("status") or "not written",
            "additions": 0,
            "deletions": 0,
            "patch": None,
            "omitted": None,
        }
    if "change" not in write:
        # Written before the daemon reported diffs: the file is known, its change is not.
        return {
            "path": path,
            "old_path": None,
            "change": None,
            "additions": 0,
            "deletions": 0,
            "patch": None,
            "omitted": "not_recorded",
        }
    parsed = parse_unified_diff(write.get("diff"))
    patch = parsed[0]["patch"] if parsed else None
    return {
        "path": path,
        "old_path": None,
        "change": write.get("change"),
        "additions": int(write.get("additions") or 0),
        "deletions": int(write.get("deletions") or 0),
        "patch": patch,
        "omitted": write.get("diff_omitted"),
    }


def _latest_workspace_report(session: Session, run: AgentRun) -> Verification | None:
    verifications = session.exec(
        select(Verification)
        .where(
            Verification.agent_run_id == run.id,
            Verification.company_id == run.company_id,
        )
        .order_by(Verification.observed_at.desc(), Verification.id.desc())
    ).all()
    for verification in verifications:
        writes = (verification.evidence or {}).get("workspace_writes")
        if isinstance(writes, list):
            return verification
    return None


def _is_diff(text: str | None) -> bool:
    return (text or "").startswith("diff --git ")


def run_changes(session: Session, run: AgentRun) -> dict[str, Any]:
    """The files a run changed: the pull request, the daemon's observation, or the diff it sent."""
    source: str | None = None
    observed_by: dict[str, Any] | None = None
    reason: str | None = None
    files: list[dict[str, Any]] = []
    # The branch and commit the delivery landed on, with the Git workflow.
    git: dict[str, Any] | None = None
    report = None
    if (run.subject or {}).get("pr_url"):
        source = "pull_request"
    else:
        report = _latest_workspace_report(session, run)
        if report is None and _is_diff(run.artifact_text):
            source = "artifact"
    if source is not None:
        files = parse_unified_diff(run.artifact_text)
        if not files:
            reason = "the diff is empty"
    elif report is None:
        reason = "no report from the workspace daemon"
    else:
        source = "workspace"
        observed_by = {
            "verification_id": report.id,
            "observed_at": report.observed_at,
        }
        writes = (report.evidence or {}).get("workspace_writes") or []
        files = [_workspace_file(w) for w in writes if isinstance(w, dict)]
        git = (report.evidence or {}).get("git")
    hidden = max(0, len(files) - MAX_FILES)
    files = files[:MAX_FILES]
    observed_paths = {f["path"] for f in files} | {
        f["old_path"] for f in files if f["old_path"]
    }
    declared = [p for p in (run.claims or {}).get("files") or [] if isinstance(p, str)]
    return {
        "run_id": run.id,
        "source": source,
        "observed_by": observed_by,
        "git": git if isinstance(git, dict) else None,
        "reason": reason,
        "files": files,
        "files_not_shown": hidden,
        "totals": {
            "files": len(files) + hidden,
            "additions": sum(f["additions"] for f in files),
            "deletions": sum(f["deletions"] for f in files),
        },
        "declared_not_observed": [p for p in declared if p not in observed_paths],
    }
