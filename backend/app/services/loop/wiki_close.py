"""The project wiki at sprint close: compile, lint, and optionally write to the repository (issue #445).

Runs after the retrospective and the procedures round, at a first close only
(``services/loop/worker/close.py``). Three steps, each best-effort: a
failure is recorded (an audit row, ``wiki_compiled``) and never stops the
close.

1. **Compile.** ``gather_inputs`` reads the structured inputs: the sprint's
   verified runs and the files the daemon observed them write, with sha256
   and diff (``services/evidence/touched.verified_writes``), the
   retrospective's summary, the board's active notes, the approved
   architecture's modules and decisions and the specification's requirements;
   ``services/learning/wiki_compile.compile_sprint`` does the rest.
2. **Lint.** ``lint_company`` runs the three rules of
   ``services/contracts/wiki_lint.py`` on every page with a version in force,
   against the latest verified writes and, without the Git workflow, the
   daemon's sha256 of each cited file as it is on disk (``workspace.hash``:
   with the workflow, files live on task branches, so the person's checkout
   is not the reference and only the evidence is read).
3. **Write.** With the company's ``write_to_repo`` setting on (off by
   default), the pages compiled this sprint go to ``docs/wiki/<slug>.md``
   through the daemon's ``workspace.generate``: on ``fabrique/wiki`` with the
   Git workflow, else with the daemon's snapshot. The daemon's reply (paths
   and sha256 read back) is the record, audited as ``wiki_written_to_repo``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config import settings
from database import engine
from logging_config import setup_logger
from models import BacklogItem, Company, Specification, Sprint
from sqlmodel import Session, select

from services.contracts import wiki, wiki_lint, wiki_templates
from services.evidence import touched
from services.learning import retrospective, wiki_compile, wiki_store
from services.planning import arch_author
from services.platform import forge_policy_setting
from services.platform.agent_service import AgentService
from services.tasks import sprint_board
from services.workspace import git_workflow, workspace_daemon

logger = setup_logger("fabrique-wiki")

WIKI_DIR = "docs/wiki"
WIKI_BRANCH = "fabrique/wiki"
MAX_HASHED_FILES = 50


def gather_inputs(
    session: Session, sprint_id: int, retro_metrics: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """What the compilation reads, as data; None without a sprint or company."""
    sprint = session.get(Sprint, sprint_id)
    company = session.get(Company, sprint.company_id) if sprint else None
    if sprint is None or company is None:
        return None
    item_ids = list(
        session.exec(
            select(BacklogItem.id).where(BacklogItem.sprint_id == sprint_id)
        ).all()
    )
    runs: dict[int, dict[str, Any]] = {}
    for write in touched.verified_writes(session, company.id, item_ids):
        run = runs.setdefault(
            write["run_id"],
            {
                "run_id": write["run_id"],
                "verification_id": write["verification_id"],
                "task_id": write["backlog_item_id"],
                "title": write["title"],
                "files": [],
            },
        )
        if write["sha256"]:
            run["files"].append(
                {
                    "path": write["path"],
                    "sha256": write["sha256"],
                    "diff": write["diff"],
                }
            )
    modules: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    requirements: list[dict[str, Any]] = []
    if sprint.specification_id is not None:
        arch = arch_author.approved_for(session, sprint.specification_id)
        if arch is not None:
            modules = [
                m for m in arch.modules or [] if isinstance(m, dict) and m.get("id")
            ]
            decisions = [d for d in arch.decisions or [] if isinstance(d, dict)]
        spec = session.get(Specification, sprint.specification_id)
        if spec is not None:
            requirements = [
                {"id": r["id"], "title": r.get("title")}
                for r in spec.requirements or []
                if isinstance(r, dict) and r.get("id")
            ]
    notes = [
        {
            "id": n.id,
            "kind": n.kind,
            "text": n.text,
            "files": list((n.links or {}).get("files") or []),
        }
        for n in sprint_board.notes_of(session, sprint_id, active_only=True)
    ]
    return {
        "company_id": company.id,
        "sprint_id": sprint_id,
        "language": wiki_templates.language_of(company),
        "runs": [r for r in runs.values() if r["files"]],
        "retrospective": retrospective.summary(retro_metrics) if retro_metrics else "",
        "notes": notes,
        "modules": modules,
        "decisions": decisions,
        "requirements": requirements,
    }


def observed_files(
    session: Session, company_id: int, disk: dict[str, dict[str, Any]] | None = None
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Per path the latest observation (the daemon's hash on disk when given,
    else the latest verified write), and every verified write (rule 2)."""
    writes = touched.verified_writes(session, company_id)
    observed: dict[str, dict[str, Any]] = {}
    for write in writes:  # newest run first
        if write["sha256"] and write["path"] not in observed:
            observed[write["path"]] = {
                "status": "present",
                "sha256": write["sha256"],
                "observed_by": f"verified run #{write['run_id']}",
            }
    for path, seen in (disk or {}).items():
        observed[path] = seen | {"observed_by": "workspace daemon"}
    return observed, writes


def lint_company(
    session: Session, company_id: int, disk: dict[str, dict[str, Any]] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Run the lint on every page with a version in force; flushed, not committed.
    Returns the reasons per slug (empty lists left out)."""
    observed, writes = observed_files(session, company_id, disk)
    pages = [
        p for p in wiki_store.pages_of(session, company_id) if p.current_version_id
    ]
    contradictions = wiki_lint.contradictions(
        [{"slug": p.slug, "body": p.body} for p in pages]
    )
    found: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        version = wiki_store.current_version(session, page)
        reasons = wiki_lint.file_findings(page.body, observed)
        reasons += wiki_lint.module_findings(
            {"body": page.body, "module_path": page.module_path},
            version.created_at if version else None,
            writes,
        )
        reasons += contradictions.get(page.slug, [])
        wiki_store.apply_lint(session, page, reasons)
        if reasons:
            found[page.slug] = reasons
    session.flush()
    return found


def cited_paths(session: Session, company_id: int) -> list[str]:
    paths: list[str] = []
    for page in wiki_store.pages_of(session, company_id):
        for cited in wiki_lint.cited_files(page.body):
            if cited["path"] not in paths:
                paths.append(cited["path"])
    return paths[:MAX_HASHED_FILES]


async def disk_hashes(
    nats_client: Any, company: dict[str, Any], paths: list[str]
) -> dict[str, dict[str, Any]] | None:
    """The daemon's sha256 of *paths* on disk, without the Git workflow only;
    None when it cannot be asked (the evidence alone is read then)."""
    if not paths or company.get("git_workflow") or not company.get("root"):
        return None
    try:
        reply = await workspace_daemon.daemon_request(
            nats_client,
            "workspace.hash",
            {"workspace_root": company["root"], "paths": paths},
            settings.WORKSPACE_WRITE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - The lint reads the evidence alone.
        logger.info("Wiki lint without the daemon's hashes: %s", exc)
        return None
    out: dict[str, dict[str, Any]] = {}
    for f in reply.get("files") or []:
        if isinstance(f, dict) and f.get("status") in ("present", "missing"):
            out[str(f.get("path"))] = {"status": f["status"], "sha256": f.get("sha256")}
    return out


def repo_files(
    session: Session, company_id: int, slugs: list[str]
) -> list[dict[str, Any]]:
    """The pages to write, as ``workspace.generate`` files: in force, not stale."""
    files = []
    for slug in slugs:
        page = wiki_store.page_of(session, company_id, slug)
        if page is None or page.status != wiki.CURRENT:
            continue
        header = (
            f"<!-- Project wiki page '{page.slug}' v{page.version}, compiled by La "
            "Fabrique from the evidence. It is a claim: each statement cites its "
            "sources. -->\n\n"
        )
        files.append(
            {
                "path": f"{WIKI_DIR}/{page.slug}.md",
                "content": header + page.body,
                "overwrite_existing": True,
            }
        )
    return files


async def write_pages(
    nats_client: Any,
    company: dict[str, Any],
    sprint_id: int,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write *files* through the daemon; the reply's paths and sha256 are the record."""
    spec = None
    if company.get("git_workflow"):
        spec = {
            "branch": WIKI_BRANCH,
            "base": company.get("git_base_branch") or "",
            "message": f"Update the project wiki\n\nFabrique-Sprint: {sprint_id}\n",
            "forge_policy": company.get("forge_policy") or "none",
        }
    request: dict[str, Any] = {
        "workspace_root": company["root"],
        "files": files,
        "interaction_id": f"wiki-sprint-{sprint_id}",
    }
    if spec:
        request["git"] = spec
    reply = await workspace_daemon.daemon_request(
        nats_client, "workspace.generate", request, git_workflow.delivery_timeout(spec)
    )
    return {
        "files": [
            {k: f.get(k) for k in ("path", "status", "sha256")}
            for f in reply.get("files") or []
            if isinstance(f, dict)
        ],
        "git": reply.get("git") if isinstance(reply.get("git"), dict) else None,
        "error": reply.get("error"),
    }


def _audit(
    company_id: int, sprint_id: int, action: str, result: dict[str, Any]
) -> None:
    try:
        with Session(engine) as session:
            AgentService().audit_interaction(
                session=session,
                company_id=company_id,
                interaction_id=f"sprint:{sprint_id}:wiki",
                agent_id="system:wiki",
                action=action,
                details={"company_id": str(company_id), "sprint_id": sprint_id},
                result=result,
                decision_signature=None,
                cost_tokens=0,
            )
    except Exception as exc:  # noqa: BLE001 - The audit row is best-effort here.
        logger.warning("Wiki audit row not written for sprint=%s: %s", sprint_id, exc)


def _compile(sprint_id: int, retro_metrics: dict[str, Any] | None) -> dict[str, Any]:
    with Session(engine) as session:
        inputs = gather_inputs(session, sprint_id, retro_metrics)
        if inputs is None:
            return {}
        company = session.get(Company, inputs["company_id"])
        summary = wiki_compile.compile_sprint(session, inputs)
        summary["company"] = {
            "id": inputs["company_id"],
            "git_workflow": bool(company and company.git_workflow),
            "git_base_branch": company.git_base_branch if company else None,
            "forge_policy": forge_policy_setting.effective(
                session, inputs["company_id"]
            ),
            "write_to_repo": wiki_store.write_to_repo(session, inputs["company_id"]),
        }
        summary["cited"] = cited_paths(session, inputs["company_id"])
        return summary


def _lint(company_id: int, disk: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    with Session(engine) as session:
        found = lint_company(session, company_id, disk)
        session.commit()
        return found


def _files(company_id: int, slugs: list[str]) -> list[dict[str, Any]]:
    with Session(engine) as session:
        return repo_files(session, company_id, slugs)


async def after_close(
    nats_client: Any, sprint_id: int, retro_metrics: dict[str, Any] | None
) -> dict[str, Any]:
    """Compile, lint and optionally write the wiki for a sprint that just closed.

    Never raises; returns what happened (also audited as ``wiki_compiled``)."""
    if not settings.WIKI_ENABLED:
        return {"skipped": "WIKI_ENABLED is off"}
    try:
        summary = await asyncio.to_thread(_compile, sprint_id, retro_metrics)
    except Exception as exc:  # noqa: BLE001 - The close goes on without the wiki.
        logger.warning("Wiki compilation failed for sprint=%s: %s", sprint_id, exc)
        return {"failed": [{"error": str(exc)[:300]}]}
    company = summary.pop("company", None)
    if not company:
        return summary
    from services.loop.worker import (
        deliver,  # the company's folder, as deliveries find it
    )

    company["root"] = await asyncio.to_thread(
        deliver._resolve_workspace_root, company["id"]
    )
    try:
        disk = await disk_hashes(nats_client, company, summary.pop("cited", []))
        summary["stale"] = await asyncio.to_thread(_lint, company["id"], disk)
        summary["lint_read_disk"] = disk is not None
    except Exception as exc:  # noqa: BLE001 - Recorded; the pages stay as they were.
        summary["lint_error"] = str(exc)[:300]
    changed = [e["slug"] for e in summary.get("compiled", [])]
    if summary.get("decisions") == "compiled":
        changed.append(wiki.DECISIONS_SLUG)
    if company["write_to_repo"] and changed and company.get("root"):
        try:
            files = await asyncio.to_thread(_files, company["id"], changed)
            if files:
                written = await write_pages(nats_client, company, sprint_id, files)
                _audit(company["id"], sprint_id, "wiki_written_to_repo", written)
                summary["repo"] = written
        except Exception as exc:  # noqa: BLE001 - Recorded; the pages stay in the database.
            summary["repo"] = {"error": str(exc)[:300]}
    _audit(company["id"], sprint_id, "wiki_compiled", summary)
    return summary
