"""What a changelog fragment is judged against, observed after its delivery.

A delivery that writes ``changelog.d/<issue>-<slug>.md`` (the daemon's
``workspace_writes``) gets ``evidence["changelog_diff"]``, which the
Verifier's ``changelog_matches_diff`` check reads
(``services/evidence/changelog_diff.py``, where the rules are):

- the paths the delivery's commit changes against the merge base of the
  company's base branch: the daemon's ``workspace.git.diff`` of the commit
  the daemon reported (``evidence["git"]``, the task branch with its
  prerequisites merged), the same request QA's branch view makes
  (``workspace/branch_view.py``). Never the agent's list of files;
- each fragment as git holds it at that commit, and the cited and changed
  files as the merge base holds them (``workspace.git.read_at``): the bytes
  come from the daemon, the sha256 is computed here, and only which code
  names occur in each base file is kept (``changelog_diff.found_in``);
- the issue the sprint's objective names (``changelog_diff.sprint_issue``).

Whatever cannot be observed is recorded as ``error`` with the reason, and the
check is then ``unverifiable``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
from typing import Any

from models.sprint import Sprint
from sqlalchemy.engine import Engine
from sqlmodel import Session

from services.evidence import changelog_diff
from services.platform import forge_policy_setting
from services.workspace import branch_view
from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)

logger = logging.getLogger(__name__)

READ_AT = "workspace.git.read_at"
TIMEOUT_SECONDS = 20.0


def _context(engine: Engine, sprint_id: Any, company_id: Any) -> tuple[dict, str]:
    """``(the sprint's issue, the forge policy in force)``, read from the database."""
    with Session(engine) as session:
        sprint = session.get(Sprint, sprint_id) if sprint_id is not None else None
        issue = changelog_diff.sprint_issue(sprint.objective if sprint else None)
        return issue, forge_policy_setting.effective(session, company_id)


def _decoded(entry: dict[str, Any]) -> tuple[bytes | None, str]:
    status = str(entry.get("status") or "missing")
    if status != "present":
        return None, status
    try:
        return base64.b64decode(
            str(entry.get("content_b64") or ""), validate=True
        ), status
    except (binascii.Error, ValueError):
        return None, "unreadable"


async def _read_at(
    nats_client: Any, root: str, commit: str, paths: list[str]
) -> list[dict[str, Any]]:
    """Each of *paths* at *commit*: ``path``, ``status``, and ``data`` when present."""
    if not paths:
        return []
    reply = await daemon_request(
        nats_client,
        READ_AT,
        {"workspace_root": root, "commit": commit, "paths": paths},
        timeout=TIMEOUT_SECONDS,
    )
    if reply.get("commit") != commit:
        raise DaemonError(502, "the workspace daemon read another commit")
    out = []
    for entry in reply.get("files") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            data, status = _decoded(entry)
            out.append({"path": entry["path"], "status": status, "data": data})
    return out


def _text(data: bytes | None) -> str | None:
    if data is None or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "replace")


async def _observe(
    nats_client: Any, evidence: dict[str, Any], written: list[str], forge_policy: str
) -> dict[str, Any]:
    git = evidence.get("git") if isinstance(evidence.get("git"), dict) else {}
    commit = git.get("commit") or git.get("parent")
    spec = branch_view.request({**git, "commit": commit}, forge_policy)
    if spec is None:
        return {"error": "no Git commit of the delivery to compare with its base"}
    diff = await daemon_request(
        nats_client, branch_view.DIFF_SUBJECT, spec, timeout=TIMEOUT_SECONDS
    )
    if diff.get("commit") != commit or not diff.get("merge_base"):
        return {"error": "the workspace daemon read another commit"}
    if isinstance(diff.get("paths"), list):
        changed = [p for p in diff["paths"] if isinstance(p, str)]
        cut = bool(diff.get("paths_truncated"))
    else:  # An older daemon: the files of the diff, with its own cut.
        files = [f for f in diff.get("files") or [] if isinstance(f, dict)]
        changed = [f["path"] for f in files if isinstance(f.get("path"), str)]
        cut = bool(diff.get("truncated"))
    root = str(spec["workspace_root"])
    record: dict[str, Any] = {
        "base": diff.get("base"),
        "merge_base": diff["merge_base"],
        "commit": commit,
        "changed": sorted(set(changed)),
        "changed_truncated": cut,
    }
    fragments = []
    for entry in await _read_at(nats_client, root, commit, written):
        text = _text(entry["data"])
        row = {"path": entry["path"], "status": entry["status"]}
        if entry["data"] is not None:
            row["sha256"] = hashlib.sha256(entry["data"]).hexdigest()
        if text is not None:
            row["text"] = text
        fragments.append(row)
    record["fragments"] = fragments
    parsed = [
        changelog_diff.parse(f["path"], f["text"]) for f in fragments if "text" in f
    ]
    wanted, base_cut = changelog_diff.base_paths(parsed, record["changed"])
    names = changelog_diff.names_to_search(parsed)
    base_files = []
    for entry in await _read_at(nats_client, root, record["merge_base"], wanted):
        row = {"path": entry["path"], "status": entry["status"]}
        if entry["data"] is not None:
            row["sha256"] = hashlib.sha256(entry["data"]).hexdigest()
            row["found"] = changelog_diff.found_in(_text(entry["data"]) or "", names)
        base_files.append(row)
    record.update(base_files=base_files, base_cut=base_cut, names_searched=names)
    return record


async def observe(
    nats_client: Any, engine: Engine, sprint: dict[str, Any], evidence: dict | None
) -> None:
    """Put ``changelog_diff`` on *evidence* when the delivery wrote a fragment."""
    written = changelog_diff.fragments_written(evidence)
    if not evidence or not written:
        return
    try:
        issue, forge_policy = await asyncio.to_thread(
            _context, engine, sprint.get("id"), sprint.get("company_id")
        )
    except Exception as exc:  # noqa: BLE001 - Unknown issue: a finding only.
        logger.warning("Could not read the sprint for the changelog check: %s", exc)
        issue = {"number": None, "reason": "the sprint could not be read"}
        forge_policy = "none"
    try:
        record = await _observe(nats_client, evidence, written, forge_policy)
    except (DaemonUnavailable, DaemonError) as exc:
        record = {"error": f"the workspace daemon did not answer ({exc})"}
    record["sprint_issue"] = issue
    evidence[changelog_diff.EVIDENCE_KEY] = record
