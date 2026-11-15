"""The wiki's citations resolved against the company's records (issue #445).

A citation is the author's claim that a record supports a statement; this
module checks the record exists in the same company and says what it is.
It never trusts what the citation says about the record: a run's verdict, a
note's status or a file's observer are read here, from the records.

- ``run`` / ``verification``: an ``AgentRun`` / ``Verification`` of the company;
  with the run's latest verdict.
- ``task``: a ``BacklogItem`` of one of the company's sprints.
- ``note``: a ``SprintBoardNote`` of the company, with its status.
- ``retro``: a completed sprint of the company (its retrospective).
- ``file``: a path and sha256 prefix that one of the company's recorded
  daemon reports (``workspace_writes``) observed; the latest such report
  names the run and its verdict.
- ``req`` / ``adr`` / ``mod``: an id in one of the company's specifications /
  approved architectures.

Reads the models directly, in Python rather than JSON operators in SQL, so
the same code runs on SQLite (the tests) and Postgres; the file scan is
bounded to the company's latest ``FILE_SCAN`` verifications.
"""

from __future__ import annotations

from typing import Any

from models import (
    AgentRun,
    Architecture,
    BacklogItem,
    Specification,
    Sprint,
    SprintBoardNote,
    Verification,
)
from sqlmodel import Session, select

FILE_SCAN = 500


def _ids(citations: list[dict[str, Any]], kind: str) -> list[int]:
    return sorted({int(c["ref"]) for c in citations if c["kind"] == kind})


def _latest_verdicts(session: Session, run_ids: list[int]) -> dict[int, str]:
    verdicts: dict[int, str] = {}
    if not run_ids:
        return verdicts
    for row in session.exec(
        select(Verification)
        .where(Verification.agent_run_id.in_(run_ids))  # type: ignore[union-attr]
        .order_by(Verification.id.desc())  # type: ignore[union-attr]
    ).all():
        verdicts.setdefault(row.agent_run_id, row.verdict)
    return verdicts


def file_observations(
    session: Session, company_id: int, paths: set[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Per path, what the company's recorded daemon reports observed, newest
    first: ``{sha256, run_id, verification_id, verdict, observed_at}``."""
    rows = session.exec(
        select(Verification)
        .where(Verification.company_id == company_id)
        .order_by(Verification.id.desc())  # type: ignore[union-attr]
        .limit(FILE_SCAN)
    ).all()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        writes = (row.evidence or {}).get("workspace_writes")
        if not isinstance(writes, list):
            continue
        for write in writes:
            if not isinstance(write, dict) or write.get("status") != "written":
                continue
            path, sha = write.get("path"), write.get("sha256")
            if not isinstance(path, str) or not isinstance(sha, str):
                continue
            if paths is not None and path not in paths:
                continue
            out.setdefault(path, []).append(
                {
                    "sha256": sha.lower(),
                    "run_id": row.agent_run_id,
                    "verification_id": row.id,
                    "verdict": row.verdict,
                    "observed_at": row.observed_at,
                }
            )
    return out


def _spec_ids(session: Session, company_id: int) -> dict[str, str]:
    known: dict[str, str] = {}
    for spec in session.exec(
        select(Specification).where(Specification.company_id == company_id)
    ).all():
        for req in spec.requirements or []:
            if not isinstance(req, dict) or not req.get("id"):
                continue
            known.setdefault(str(req["id"]).upper(), str(req.get("title") or ""))
            for crit in req.get("criteria") or []:
                if isinstance(crit, dict) and crit.get("id"):
                    known.setdefault(
                        str(crit["id"]).upper(), str(req.get("title") or "")
                    )
        for nfr in spec.quality or []:
            if isinstance(nfr, dict) and nfr.get("id"):
                known.setdefault(
                    str(nfr["id"]).upper(), str(nfr.get("attribute") or "")
                )
    return known


def _arch_ids(session: Session, company_id: int) -> dict[str, str]:
    known: dict[str, str] = {}
    for arch in session.exec(
        select(Architecture).where(
            Architecture.company_id == company_id,
            Architecture.status.in_(("approved", "superseded")),  # type: ignore[union-attr]
        )
    ).all():
        for module in arch.modules or []:
            if isinstance(module, dict) and module.get("id"):
                known.setdefault(
                    str(module["id"]).upper(), str(module.get("name") or "")
                )
        for decision in arch.decisions or []:
            if isinstance(decision, dict) and decision.get("id"):
                known.setdefault(
                    str(decision["id"]).upper(), str(decision.get("title") or "")
                )
    return known


def resolve(
    session: Session, company_id: int, citations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Each distinct citation with ``known`` and what the records say of it."""
    runs = {
        r.id: r
        for r in session.exec(
            select(AgentRun).where(
                AgentRun.company_id == company_id,
                AgentRun.id.in_(_ids(citations, "run")),  # type: ignore[union-attr]
            )
        ).all()
    }
    verdicts = _latest_verdicts(session, list(runs))
    verifications = {
        v.id: v
        for v in session.exec(
            select(Verification).where(
                Verification.company_id == company_id,
                Verification.id.in_(_ids(citations, "verification")),  # type: ignore[union-attr]
            )
        ).all()
    }
    sprint_ids = set(
        session.exec(select(Sprint.id).where(Sprint.company_id == company_id)).all()
    )
    tasks = {
        t.id: t
        for t in session.exec(
            select(BacklogItem).where(
                BacklogItem.id.in_(_ids(citations, "task"))  # type: ignore[union-attr]
            )
        ).all()
        if t.sprint_id in sprint_ids
    }
    notes = {
        n.id: n
        for n in session.exec(
            select(SprintBoardNote).where(
                SprintBoardNote.company_id == company_id,
                SprintBoardNote.id.in_(_ids(citations, "note")),  # type: ignore[union-attr]
            )
        ).all()
    }
    retros = {
        s.id: s
        for s in session.exec(
            select(Sprint).where(
                Sprint.company_id == company_id,
                Sprint.id.in_(_ids(citations, "retro")),  # type: ignore[union-attr]
            )
        ).all()
    }
    wanted_files = {c["ref"] for c in citations if c["kind"] == "file"}
    files = file_observations(session, company_id, wanted_files) if wanted_files else {}
    specs = (
        _spec_ids(session, company_id)
        if any(c["kind"] == "req" for c in citations)
        else {}
    )
    archs = (
        _arch_ids(session, company_id)
        if any(c["kind"] in ("adr", "mod") for c in citations)
        else {}
    )

    resolved: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for c in citations:
        key = (c["kind"], c["ref"], c.get("sha256"))
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {"kind": c["kind"], "ref": c["ref"], "known": False}
        if c.get("sha256"):
            entry["sha256"] = c["sha256"]
        kind, ref = c["kind"], c["ref"]
        if kind == "run" and int(ref) in runs:
            run = runs[int(ref)]
            entry |= {
                "known": True,
                "verdict": verdicts.get(run.id),
                "task_id": (run.subject or {}).get("backlog_item_id"),
            }
        elif kind == "verification" and int(ref) in verifications:
            v = verifications[int(ref)]
            entry |= {"known": True, "verdict": v.verdict, "run_id": v.agent_run_id}
        elif kind == "task" and int(ref) in tasks:
            t = tasks[int(ref)]
            entry |= {"known": True, "title": t.title, "status": t.status}
        elif kind == "note" and int(ref) in notes:
            n = notes[int(ref)]
            entry |= {"known": True, "status": n.status, "sprint_id": n.sprint_id}
        elif kind == "retro" and int(ref) in retros:
            s = retros[int(ref)]
            entry |= {
                "known": s.status == "completed",
                "sprint_name": s.sprint_name,
                "status": s.status,
            }
        elif kind == "file":
            match = next(
                (o for o in files.get(ref, []) if o["sha256"].startswith(c["sha256"])),
                None,
            )
            if match is not None:
                entry |= {
                    "known": True,
                    "run_id": match["run_id"],
                    "verification_id": match["verification_id"],
                    "verdict": match["verdict"],
                }
        elif kind == "req" and (ref in specs or ref.split(".")[0] in specs):
            entry |= {
                "known": True,
                "title": specs.get(ref) or specs.get(ref.split(".")[0]),
            }
        elif kind in ("adr", "mod") and ref in archs:
            entry |= {"known": True, "title": archs[ref]}
        resolved.append(entry)
    return resolved


def unknown(resolved: list[dict[str, Any]]) -> list[str]:
    """The citations no record supports, as written in the body's syntax."""
    out = []
    for s in resolved:
        if s["known"]:
            continue
        if s["kind"] == "file":
            out.append(f"[file:{s['ref']}@{s.get('sha256')}]")
        elif s["kind"] in ("req", "adr", "mod"):
            out.append(f"[{s['ref']}]")
        else:
            out.append(f"[{s['kind']}:{s['ref']}]")
    return out
