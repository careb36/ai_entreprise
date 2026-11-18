"""Compiling a documentation pack, checking it for drift, writing it to the repository.

``compile_pack`` reads the snapshot (``collect``), builds every document and
diagram with the pure builders, and stores a new draft ``DocumentationPack``
(older drafts become superseded). The only model call is the charter's
background, asked of the company's TechWriter (``prose_request`` /
``write_prose``, run by the router in a thread): bounded, sanitized, marked as
a claim, and left out with an explicit line when the call fails.

``lint`` recomputes each document's and diagram's input digest from the current
records: a mismatch is drift (the records changed since the pack was compiled)
and blocks the Owner's approval until the pack is compiled again; a document
without any source and sections with no record are reported too.

``write_pack`` sends an approved pack to ``docs/project/`` in the project's
repository through the daemon's ``workspace.generate`` (the same path
``arch_author.write_document`` uses) and returns the sha256 the daemon read
back for each file.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from models import Agent, Company, DocumentationPack, ProjectDiscovery
from models.documentation import PACK_DIRECTORY
from sqlalchemy import func
from sqlmodel import Session, select

from services.planning.project_docs import (
    c4,
    checks,
    comms_docs,
    control_docs,
    log_docs,
    quality_docs,
    release_docs,
    scope_docs,
    security_docs,
    specs,
    status_docs,
    texts,
)
from services.planning.project_docs.layout import (
    DOMAINS,
    FOCUS_AREAS,
    input_sha256,
    tr_of,
)
from services.planning.project_docs.refs import digest
from services.planning.project_docs.sanitize import PROSE_LIMIT, md_inline

logger = logging.getLogger(__name__)

COMPILER_ROLE = "TechWriter"
PROSE_MAX_TOKENS = 600
PROSE_TIMEOUT_SECONDS = 60.0
INDEX_KEY = "index"


class PackError(Exception):
    """A pack cannot be compiled or written (no project folder, no company)."""


def _now() -> datetime:
    return datetime.now(UTC)


def file_name(key: str) -> str:
    return "README.md" if key == INDEX_KEY else f"{key}.md"


def _index(snapshot: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
    lang, tr = tr_of(snapshot)
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")
    inputs = tuple(sorted({name for d in documents for name in d["inputs"]}))

    def version_of(record: dict | None) -> str:
        if not record:
            return tr("index.none")
        return tr(
            "index.version_of",
            id=md_inline(record["id"]),
            version=md_inline(record.get("version")),
        )

    body = [
        tr("index.project", name=md_inline(snapshot["project"]["name"])),
        tr(
            "index.spec",
            value=", ".join(version_of(s) for s in specs.approved(snapshot))
            if specs.several(snapshot)
            else version_of(spec),
        ),
        tr("index.arch", value=version_of(arch)),
        "",
        tr("index.documents"),
        "",
        tr("index.placement"),
        "",
        tr("index.header"),
        "|---|---|---|---|---|---|---|---|",
    ]
    order = {name: i for i, name in enumerate(DOMAINS)}
    focus = {name: i for i, name in enumerate(FOCUS_AREAS)}
    ranked = sorted(
        documents,
        key=lambda d: (
            order.get(d.get("domain") or "", len(order)),
            focus.get(d.get("focus_area") or "", len(focus)),
        ),
    )
    nothing = tr("index.nothing")
    for d in ranked:
        domain = texts.domain(lang, d["domain"]) if d.get("domain") else "-"
        area = texts.focus(lang, d["focus_area"]) if d.get("focus_area") else "-"
        body.append(
            f"| {domain} | {area} | "
            f"{md_inline(d['title'])} | {file_name(d['key'])} | {len(d['sources'])} | "
            f"{md_inline(', '.join(d['missing']) or nothing)} | "
            f"{md_inline(', '.join(d.get('persons') or []) or nothing)} | "
            f"{md_inline(', '.join(c['section'] for c in d['claims']) or nothing)} |"
        )
    body += [
        "",
        tr("index.read_heading"),
        "",
        tr("index.read"),
        "",
        tr("index.frameworks"),
        "",
        tr("index.pmbok"),
        tr("index.c4"),
        tr("index.wstg"),
    ]
    title = tr("index.title")
    lines = [f"# {title}", "", f"_{tr('generated_note')}_", "", *body, ""]
    return {
        "key": INDEX_KEY,
        "title": title,
        "framework": "",
        "markdown": "\n".join(lines),
        "sources": [],
        "missing": [],
        "claims": [],
        "persons": [],
        "domain": "",
        "focus_area": "",
        "inputs": list(inputs),
        "input_sha256": input_sha256(snapshot, inputs),
    }


def build_documents(
    snapshot: dict[str, Any], prose: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every document and diagram of a pack from *snapshot*; deterministic for a given *prose*."""
    diagrams = c4.diagrams(snapshot)
    documents = [
        scope_docs.charter(snapshot, prose),
        scope_docs.scope(snapshot),
        scope_docs.wbs(snapshot),
        quality_docs.plan(snapshot),
        log_docs.traceability(snapshot),
        control_docs.schedule(snapshot),
        release_docs.plan(snapshot),
        control_docs.raci(snapshot),
        comms_docs.plan(snapshot),
        control_docs.risks(snapshot),
        log_docs.issues(snapshot),
        log_docs.changes(snapshot),
        control_docs.lessons(snapshot),
        status_docs.report(snapshot),
        checks.document(snapshot),
        security_docs.checklist(snapshot),
        security_docs.report(snapshot),
        c4.document(snapshot, diagrams),
    ]
    return [_index(snapshot, documents), *documents], diagrams


# --- the TechWriter's prose ----------------------------------------------------


def prose_request(
    session: Session, company: Company, snapshot: dict[str, Any]
) -> dict[str, Any] | None:
    """What the TechWriter is asked for the charter's background; None without a specification."""
    from services.models import company_models

    spec = snapshot.get("specification")
    if not spec:
        return None
    writer = session.exec(
        select(Agent).where(Agent.company_id == company.id, Agent.role == COMPILER_ROLE)
    ).first()
    requirements = "\n".join(
        f"- {r.get('id')}: {r.get('title')}"
        for r in (spec.get("requirements") or [])[:30]
    )
    prompt = (
        "Write the background section of a project charter in at most 120 words "
        "of plain prose: why this project exists and what it changes for its "
        "users. Use only the facts below; do not add numbers, dates, names or "
        "promises that are not in them. No headings, lists, links or markup.\n\n"
        f"Project: {spec.get('title')}\nSummary: {spec.get('summary')}\n"
        f"Requirements:\n{requirements}\n\n"
        + texts.translator(texts.of(snapshot))("prose.instruction")
    )
    configured = (writer.model if writer else "") or ""
    return {
        "company_id": company.id,
        "model": company_models.resolve(session, company.id, COMPILER_ROLE, configured),
        "system_prompt": (writer.system_prompt if writer else None) or None,
        "prompt": prompt,
    }


def write_prose(request: dict[str, Any] | None) -> str | None:
    """The TechWriter's answer, cut to ``PROSE_LIMIT``; None when there is none.
    No database: the router runs it in a thread."""
    if request is None:
        return None
    from services.planning import spec_author

    try:
        answer = spec_author.ask(
            request["model"],
            request["system_prompt"],
            request["prompt"],
            max_tokens=PROSE_MAX_TOKENS,
            timeout=PROSE_TIMEOUT_SECONDS,
            role=COMPILER_ROLE,
            company_id=request["company_id"],
        )
    except Exception as exc:  # noqa: BLE001 - Prose is optional: the charter says none was written.
        logger.warning(
            "TechWriter prose failed for company %s: %s", request["company_id"], exc
        )
        return None
    answer = (answer or "").strip()
    return answer[:PROSE_LIMIT] or None


# --- compiling ------------------------------------------------------------------


def compile_pack(
    session: Session,
    company: Company,
    snapshot: dict[str, Any],
    requested_by: str,
    prose: str | None = None,
) -> DocumentationPack:
    """A new draft pack from *snapshot*; older drafts become superseded. The caller commits."""
    documents, diagrams = build_documents(snapshot, prose)
    for old in session.exec(
        select(DocumentationPack).where(
            DocumentationPack.company_id == company.id,
            DocumentationPack.status == "draft",
        )
    ).all():
        old.status = "superseded"
        old.updated_at = _now()
        session.add(old)
    latest = session.exec(
        select(func.max(DocumentationPack.version)).where(
            DocumentationPack.company_id == company.id
        )
    ).one()
    spec, arch = snapshot.get("specification"), snapshot.get("architecture")
    pack = DocumentationPack(
        company_id=company.id,
        version=int(latest or 0) + 1,
        specification_id=spec["id"] if spec else None,
        architecture_id=arch["id"] if arch else None,
        compiled_by=COMPILER_ROLE,
        requested_by=requested_by,
        language=texts.of(snapshot),
        documents=documents,
        diagrams=diagrams,
        inputs_sha256=digest(
            [d["input_sha256"] for d in documents]
            + [d["input_sha256"] for d in diagrams]
        ),
    )
    session.add(pack)
    return pack


# --- drift and lint -------------------------------------------------------------


def lint(pack: DocumentationPack, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drift and gaps of *pack* against the current *snapshot*."""
    findings: list[dict[str, Any]] = []
    documents = []
    for doc in pack.documents or []:
        current = input_sha256(snapshot, tuple(doc.get("inputs") or []))
        stale = current != doc.get("input_sha256")
        documents.append(
            {
                "key": doc.get("key"),
                "stale": stale,
                "missing": list(doc.get("missing") or []),
                "claims": len(doc.get("claims") or []),
                "sources": len(doc.get("sources") or []),
            }
        )
        if stale:
            findings.append(
                {
                    "severity": "blocking",
                    "where": doc.get("key"),
                    "message": "The records it was compiled from changed since: compile the pack again.",
                }
            )
        if doc.get("key") != INDEX_KEY and not doc.get("sources"):
            findings.append(
                {
                    "severity": "warning",
                    "where": doc.get("key"),
                    "message": "It cites no record.",
                }
            )
        for section in doc.get("missing") or []:
            findings.append(
                {
                    "severity": "info",
                    "where": doc.get("key"),
                    "message": f"No record for: {section}.",
                }
            )
    current_c4 = input_sha256(snapshot, c4.C4_INPUTS)
    diagrams = []
    for diagram in pack.diagrams or []:
        stale = current_c4 != diagram.get("input_sha256")
        diagrams.append({"key": diagram.get("key"), "stale": stale})
        if stale:
            findings.append(
                {
                    "severity": "blocking",
                    "where": diagram.get("key"),
                    "message": "The architecture or the detected stack changed since the diagram was drawn.",
                }
            )
    if not pack.diagrams and snapshot.get("architecture"):
        findings.append(
            {
                "severity": "blocking",
                "where": "c4",
                "message": "An architecture exists now; the pack has no diagram of it.",
            }
        )
    current_language = texts.of(snapshot)
    if texts.language(pack.language) != current_language:
        findings.append(
            {
                "severity": "blocking",
                "where": INDEX_KEY,
                "message": f"The pack is written in {texts.language(pack.language)}; the project's documentation language is now {current_language}: compile the pack again.",
            }
        )
    blocking = sum(1 for f in findings if f["severity"] == "blocking")
    return {
        "ok": blocking == 0,
        "stale": any(d["stale"] for d in documents + diagrams) or blocking > 0,
        "blocking": blocking,
        "findings": findings,
        "documents": documents,
        "diagrams": diagrams,
    }


# --- writing to the repository -------------------------------------------------


def files_of(pack: DocumentationPack) -> list[dict[str, Any]]:
    return [
        {
            "path": f"{PACK_DIRECTORY}/{file_name(str(doc.get('key')))}",
            "content": doc.get("markdown") or "",
            "overwrite_existing": True,
        }
        for doc in pack.documents or []
    ]


async def write_pack(
    nats_client: Any, session: Session, pack: DocumentationPack, timeout: float
) -> list[dict[str, Any]]:
    """Send the pack's documents to ``docs/project/`` through the daemon.

    Returns ``[{path, sha256}]`` as the daemon reported each file; raises
    ``PackError`` without a project folder, ``DaemonUnavailable`` or
    ``DaemonError`` (``services.workspace.workspace_daemon``) otherwise.
    """
    from services.workspace.workspace_daemon import DaemonError, daemon_request

    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == pack.company_id)
        .order_by(ProjectDiscovery.id.desc())
    ).first()
    if discovery is None or not discovery.project_path:
        raise PackError("The company has no project folder to write to")
    files = files_of(pack)
    reply = await daemon_request(
        nats_client,
        "workspace.generate",
        {
            "workspace_root": discovery.project_path,
            "files": files,
            "interaction_id": f"documentation-pack-{pack.id}",
        },
        timeout,
    )
    reported = {
        f.get("path"): f
        for f in (reply.get("files") if isinstance(reply, dict) else None) or []
        if isinstance(f, dict)
    }
    written = []
    for item in files:
        got = reported.get(item["path"])
        if got is None or got.get("status") != "written" or not got.get("sha256"):
            raise DaemonError(
                502, f"The workspace daemon did not report {item['path']} as written"
            )
        written.append({"path": item["path"], "sha256": str(got["sha256"])})
    return written
