"""What the PO says is already done, and what its reads show (owner, 2026-11-24).

Sprint 8 was planned from an issue whose option, tests and docs were already
on main: four tasks, and a Developer who spent fifteen tool rounds finding
that out. Where the PO reads the project before it plans
(``planning_reads``), ``NOTE`` asks it to check first whether each part of
the objective already exists, to name what does in an optional
``already_done`` list, and to plan tasks only for what is missing.

Two fields, never one (``docs/POSITIONING.md``):

- ``already_done_claim`` (``claim_of``): the PO's words, each part with the
  evidence and the paths it names. A claim, whatever it says.
- ``already_done_observed`` (``observed``): what the backend observed of the
  PO's reads in this planning turn: each file read with the sha256 the daemon
  reported (or the backend hashed, for ``git show_base``), each search, and,
  per claimed path, whether the PO read it (``read: false`` is "not read";
  the claim is still shown, flagged).

When the PO claims parts done and plans no task, the plan is one QA task
that verifies the objective with the tests (``verification_task``), so a
person sees it confirmed by tests, not by the PO's word. Without reads (no
daemon, no root, 0 rounds) the PO is not asked and nothing is kept.

``record_plan`` writes both into the audit log under the plan's ref (as
``planner_fallback`` does, by the backend and never from a request body);
``carry_to_sprint`` copies the row to the sprint's planning ref when
``start_sprint`` names the ``plan_id``. The ``Sprint`` row has no column for
plan metadata.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from models import AuditLog, Sprint
from sqlmodel import Session, select

from services.contracts import roles
from services.contracts.task_text import clip_description
from services.planning import planner_fallback
from services.platform.agent_service import AgentService

NOTE = (
    "\n\n## Check what is already done\n"
    "Before you plan, check with the read tools whether each part of the "
    "objective already exists in the code: search for the flags, functions, "
    "options and files it names, read them, and read the tests that cover "
    "them. Plan tasks only for what is missing. Name each part that is "
    "already done in an extra key of your JSON answer:\n"
    '"already_done": [{"part": "what the objective asks", "evidence": '
    '"where it is: path(s), names", "paths": ["the files you read that show it"]}]\n'
    "Omit the key (or give []) when nothing is done yet. When every part is "
    'already done, answer "tasks": [] and the platform plans one QA task that '
    "verifies it with the tests; the fewer-tasks bound above does not apply "
    "to the parts already done."
)

ACTION = "already_done_claim"
MAX_PARTS = 12
MAX_PATHS = 10
PART_CHARS, EVIDENCE_CHARS, PATH_CHARS = 300, 500, 300
# Path-like words in the evidence: with a folder, or a file name with an extension.
_PATHISH = re.compile(r"[\w.-]+(?:/[\w.-]+)+/?|[\w-]+\.[A-Za-z]{1,5}\b")
_READ_TOOLS = ("read", "git")
_SEARCH_TOOLS = ("search", "find_file", "definition", "references")


def clean_path(value: Any) -> str:
    """*value* as a relative project path: no quotes, ``./`` or trailing punctuation."""
    text = str(value or "").strip().strip("`'\"").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip(".,;:)")[:PATH_CHARS]


def claim_of(parsed: Any) -> list[dict[str, Any]]:
    """The PO's ``already_done`` list, bounded: each part with its evidence
    and paths (named, or path-like words in the evidence). The PO's words."""
    raw = parsed.get("already_done") if isinstance(parsed, dict) else None
    claim: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"part": item}
        if not isinstance(item, dict) or not str(item.get("part") or "").strip():
            continue
        evidence = str(item.get("evidence") or "").strip()[:EVIDENCE_CHARS]
        named = item.get("paths") if isinstance(item.get("paths"), list) else []
        words = [clean_path(p) for p in named] + [
            clean_path(m.group(0)) for m in _PATHISH.finditer(evidence)
        ]
        claim.append(
            {
                "part": str(item["part"]).strip()[:PART_CHARS],
                "evidence": evidence,
                "paths": list(dict.fromkeys(p for p in words if p))[:MAX_PATHS],
            }
        )
    return claim[:MAX_PARTS]


def _covers(claimed: str, read: str) -> bool:
    """Whether a read of *read* is a read of the *claimed* path: the same
    file, a file under a claimed folder, or a claimed bare file name."""
    folder = claimed.rstrip("/")
    if read == folder or read.startswith(folder + "/"):
        return True
    return "/" not in folder and read.rsplit("/", 1)[-1] == folder


def observed(
    reads: list[dict[str, Any]], claim: list[dict[str, Any]]
) -> dict[str, Any]:
    """What the backend observed of the PO's reads (``tool_calls``), and per
    claimed path whether it was read in this planning turn."""
    files: list[dict[str, Any]] = []
    searches: list[dict[str, Any]] = []
    for call in reads:
        if not isinstance(call, dict) or call.get("is_error"):
            continue
        tool, arguments = call.get("tool"), call.get("arguments") or {}
        path = clean_path(call.get("path") or arguments.get("path"))
        if tool in _READ_TOOLS and path and call.get("sha256"):
            files.append(
                {
                    "path": path,
                    "sha256": call.get("sha256"),
                    "bytes": call.get("bytes"),
                    "tool": tool,
                    "round": call.get("round"),
                }
            )
        elif tool in _SEARCH_TOOLS:
            searches.append(
                {
                    "tool": tool,
                    "pattern": arguments.get("pattern") or arguments.get("name"),
                    "path": path or None,
                    "match_count": call.get("match_count"),
                }
            )
    claimed = list(dict.fromkeys(p for part in claim for p in part["paths"]))
    checked = []
    for path in claimed:
        seen = [
            {"path": f["path"], "sha256": f["sha256"]}
            for f in files
            if _covers(path, f["path"])
        ]
        checked.append({"path": path, "read": bool(seen), "files": seen})
    return {"files_read": files, "searches": searches, "claimed_paths": checked}


def verification_task(objective: str, claim: list[dict[str, Any]]) -> dict[str, Any]:
    """The one QA task of a plan whose every part the PO claims done."""
    parts = "; ".join(
        f"{c['part']} ({', '.join(c['paths'])})" if c["paths"] else c["part"]
        for c in claim
    )
    return {
        "title": "Verify that the objective is already met",
        "description": clip_description(
            f"Verify that {objective.strip()} is already met: run the tests "
            "that cover it and check each criterion. The PO says these parts "
            f"are already done (its claim, not yet verified): {parts}."
        ),
        "priority": "high",
        "assignee": roles.QA,
        "depends_on": [],
    }


def _write(
    session: Session,
    company_id: int,
    ref: str,
    details: dict[str, Any],
    claim: Any,
    commit: bool,
) -> AuditLog:
    return AgentService().audit_interaction(
        session=session,
        company_id=company_id,
        interaction_id=ref,
        agent_id=planner_fallback.AGENT,
        action=ACTION,
        details=details,
        result={"claim": claim},
        decision_signature=None,
        cost_tokens=0,
        commit=commit,
    )


def record_plan(
    session: Session,
    company_id: int,
    kind: str,
    plan: dict[str, Any],
    plan_id: str | None,
) -> str | None:
    """Record *plan*'s claim and observations under its ref; the ``plan_id``
    (*plan_id*, or a new one), or *plan_id* when the PO claimed nothing."""
    claim = plan.get("already_done_claim")
    if not claim:
        return plan_id
    plan_id = plan_id or uuid4().hex
    _write(
        session,
        company_id,
        planner_fallback.plan_ref(company_id, plan_id),
        {
            "company_id": str(company_id),
            "plan_kind": kind,
            "observed": plan.get("already_done_observed") or {},
        },
        claim,
        commit=True,
    )
    return plan_id


def carry_to_sprint(session: Session, sprint: Sprint, plan_id: str | None) -> bool:
    """Copy the plan's row to the sprint's planning ref, read back from the
    backend's own record; the caller commits. False when there is none."""
    if not plan_id:
        return False
    row = session.exec(
        select(AuditLog).where(
            AuditLog.company_id == sprint.company_id,
            AuditLog.interaction_id
            == planner_fallback.plan_ref(sprint.company_id, plan_id),
            AuditLog.action == ACTION,
        )
    ).first()
    if row is None or not isinstance(row.result, dict):
        return False
    details = row.details if isinstance(row.details, dict) else {}
    _write(
        session,
        sprint.company_id,
        planner_fallback.sprint_ref(sprint.id),
        {
            "sprint_id": sprint.id,
            "plan_id": plan_id,
            "plan_audit_id": row.id,
            "observed": details.get("observed") or {},
        },
        row.result.get("claim"),
        commit=False,
    )
    return True
