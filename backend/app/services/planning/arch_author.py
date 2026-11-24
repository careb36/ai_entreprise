"""The Architect drafts the company's architecture for an approved specification.

From the specification, the project's detected stack and the files at its
root, the Architect proposes the modules, the interfaces between them, the
decisions and the conventions, in a fixed JSON shape a parser checks, and
lists what it could not decide. The draft is a claim: ``arch_check``
numbers it and says what blocks its approval, a person answers, edits and
approves. Once approved, ``context_for`` gives each task the modules its
requirements map to, and ``write_document`` sends the document and one ADR
file per decision to the repository through the workspace daemon, whose
sha256 of the bytes on disk is what the backend keeps, per file. The
architecture is the company's (``arch_versions``): for a new specification
the Architect revises the current version, and the earlier specifications'
covers are kept. While it writes or revises, the Architect may read the
project with the read-only workspace tools beside its research tools
(``tool_steps.ARCH_AUTHOR``: R and S; ``planning_reads.author_turn``).
"""

from __future__ import annotations

import json
from typing import Any

from logging_config import logger
from models import Agent, BacklogItem, ProjectDiscovery, Sprint
from models.architecture import ARCHITECTURE_PATH, Architecture
from models.specification import Specification
from sqlalchemy.engine import Engine
from sqlmodel import Session, select, text

from services.contracts import tool_steps
from services.evidence import standards
from services.planning import (
    adr_docs,
    arch_check,
    arch_versions,
    planning_reads,
    spec_author,
    spec_check,
    spec_planning,
)
from services.planning.sprint_planner import _build_stack_summary, _list_root_files
from services.tools import research_tools

ARCHITECT_ROLE = "Architect"
DRAFT_MAX_TOKENS = 8000


class ArchAuthorError(Exception):
    """The Architect gave no usable draft (a failed call, or an answer off the format)."""


def architect_profile(session: Session, company_id: int) -> tuple[str, str | None, str]:
    """The company's Architect agent: its model (or the company's, or the role's default), system prompt and name."""
    from services.models import company_models

    architect = session.exec(
        select(Agent).where(
            Agent.company_id == company_id, Agent.role == ARCHITECT_ROLE
        )
    ).first()
    configured = (architect.model if architect else "") or ""
    return (
        company_models.resolve(session, company_id, ARCHITECT_ROLE, configured),
        (architect.system_prompt if architect else None) or None,
        (architect.name if architect else "") or ARCHITECT_ROLE,
    )


DRAFT_FORMAT = """\
Answer with one JSON object and nothing else, in this exact shape:

{
  "title": "a short name for the architecture",
  "overview": "three to six sentences: how the system is put together and why",
  "modules": [
    {
      "name": "a module, in one or two words",
      "responsibility": "what it alone is responsible for; what it never does",
      "path": "its folder or file in the repository, e.g. src/greetings/",
      "depends_on": ["names of the modules it calls"],
      "covers": ["REQ-1", "REQ-3"]
    }
  ],
  "interfaces": [
    {
      "name": "what the interface is called",
      "kind": "http | function | event | cli | schema",
      "module": "the module that provides it",
      "contract": "the routes, the signature, the schema or the event, as a test could hold it to",
      "consumers": ["names of the modules that use it"]
    }
  ],
  "decisions": [
    {"title": "the decision, as a short sentence", "context": "what made it necessary", "decision": "what was decided", "consequences": "what it costs and what it makes easier"}
  ],
  "conventions": ["a rule every task follows: layout, naming, errors, tests, logging"],
  "questions": ["what you could not decide; one question each, specific"]
}

Rules:
- Do not number anything; the ids are assigned afterwards. Refer to modules by name and to requirements by their REQ id.
- Every requirement of the specification is covered by at least one module; a module covers what it is responsible for, not everything.
- No dependency runs in a circle. Each interface belongs to one module and has a contract.
- Record the decisions a developer would otherwise take alone (storage, framework, boundaries, error handling), each with its consequences.
- Keep to the detected stack unless a decision says why not. Ask a question instead of guessing.
"""


def draft_prompt(
    architect_name: str,
    spec: dict[str, Any],
    detected_stack: dict[str, Any] | None,
    root_files: str,
    existing: dict[str, Any] | None,
) -> str:
    """The question to the Architect: a first draft, or a revision from the answers."""
    lines = [
        (
            f"You are {architect_name}, the Architect of an AI software factory. Design "
            "how the specification below is built: the agents that implement each task "
            "will read the modules, interfaces, decisions and conventions you write, "
            "and nothing else about the design."
        ),
        "",
        f"## Specification: {spec.get('title')}",
        spec.get("summary") or "",
        "",
        spec_check.as_prompt(spec),
        "",
    ]
    if stack := spec_author._stack(detected_stack):
        lines += ["## Detected stack of the project", _build_stack_summary(stack), ""]
    if root_files:
        lines += ["## Files at the project's root", root_files, ""]
    if existing:
        answered = [q for q in existing.get("questions") or [] if q.get("answer")]
        serves = sorted(
            {
                key
                for m in existing.get("modules") or []
                for key in m.get("earlier_covers") or {}
            },
            key=int,
        )
        lines += [
            "## The current draft",
            (
                "Revise it: keep what the answers confirm, change what they correct, and "
                "ask again only what is still open."
            ),
            "",
        ]
        if serves:
            lines += [
                (
                    "This is the company's architecture: its modules also serve the "
                    f"earlier specifications {', '.join(serves)}. Keep the modules, "
                    "interfaces and decisions they rely on, and add the covers of "
                    "the specification above; their covers are kept for you."
                ),
                "",
            ]
        lines += [
            json.dumps(
                {
                    k: (
                        [
                            {a: b for a, b in m.items() if a != "earlier_covers"}
                            for m in existing.get(k) or []
                        ]
                        if k == "modules"
                        else existing.get(k)
                    )
                    for k in (
                        "title",
                        "overview",
                        "modules",
                        "interfaces",
                        "decisions",
                        "conventions",
                    )
                },
                ensure_ascii=False,
            )[:16000],
            "",
        ]
        if answered:
            lines += ["## Answers to your questions", ""]
            lines += [f"- Q: {q['question']}\n  A: {q['answer']}" for q in answered]
            lines.append("")
    lines.append(DRAFT_FORMAT)
    return "\n".join(lines)


def parse_draft(text_: str) -> dict[str, Any] | None:
    """The draft in the fixed shape, normalized; ``None`` when unusable."""
    try:
        parsed = json.loads(spec_author._strip_fences(text_))
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    arch = arch_check.normalize(parsed)
    if not arch["title"] or not arch["modules"]:
        return None
    # When a decision was accepted, and what supersedes it, is a person's
    # record, never the model's.
    for decision in arch["decisions"]:
        decision.pop("date", None)
        decision.pop("superseded_by", None)
    return arch


def content_of(arch: Architecture) -> dict[str, Any]:
    return {
        "title": arch.title,
        "overview": arch.overview,
        "modules": arch.modules or [],
        "interfaces": arch.interfaces or [],
        "decisions": arch.decisions or [],
        "conventions": arch.conventions or [],
        "questions": arch.questions or [],
    }


def requirement_ids(spec: Specification) -> list[str]:
    return arch_versions.requirement_ids(spec)


def prepare(
    session: Session,
    spec: Specification,
    existing: Architecture | None = None,
) -> dict[str, Any]:
    """Everything ``draft_from`` needs, read with *session*: the model, the
    prompt and the billing unit. Plain values, so the call to the proxy can
    run in another thread without the session."""
    model, system_prompt, name = architect_profile(session, spec.company_id)
    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == spec.company_id)
        .order_by(text("created_at DESC"))
    ).first()
    root_files = (
        _list_root_files(discovery.project_path)
        if discovery and discovery.project_path
        else ""
    )
    prompt = draft_prompt(
        name,
        spec_planning.spec_content(spec),
        discovery.detected_stack if discovery else None,
        root_files,
        content_of(existing) if existing is not None else None,
    )
    return {
        "specification_id": spec.id,
        "company_id": spec.company_id,
        "model": model,
        "system_prompt": system_prompt,
        "prompt": prompt
        + standards.prompt_section(ARCHITECT_ROLE)
        + research_tools.prompt_section(ARCHITECT_ROLE),
        "bu_id": spec_author.business_unit_id(session, spec.company_id),
        "questions": (existing.questions if existing is not None else []) or [],
        "research_hosts": research_tools.company_hosts(session, spec.company_id),
        # The repository map is added by the caller that holds NATS
        # (``repo_map.fetch_section``): this function reads only the database.
        "project_path": discovery.project_path if discovery else None,
    }


def draft_from(request: dict[str, Any]) -> dict[str, Any]:
    """The Architect's draft for a ``prepare``d request, normalized; no database.

    Raises ``ArchAuthorError`` when nothing usable came back.
    """
    logger.info(
        "Architect drafting for specification=%s model=%s",
        request["specification_id"],
        request["model"],
    )
    toolbox, note, record = planning_reads.author_turn(
        tool_steps.ARCH_AUTHOR,
        ARCHITECT_ROLE,
        request.get("company_id"),
        request["model"],
        research_tools.toolbox_for_request(ARCHITECT_ROLE, request),
    )
    try:
        answer, calls = spec_author.ask_with_tools(
            request["model"],
            request["system_prompt"],
            request["prompt"] + note,
            max_tokens=DRAFT_MAX_TOKENS,
            role=ARCHITECT_ROLE,
            bu_id=request["bu_id"],
            toolbox=toolbox,
            company_id=request.get("company_id"),
            record=record,
            exhausted_note=planning_reads.AUTHOR_EXHAUSTED_NOTE if record else None,
        )
    except spec_author.SpecAuthorError as exc:
        raise ArchAuthorError(str(exc)) from exc
    arch = parse_draft(answer)
    if arch is None:
        raise ArchAuthorError(
            "The Architect's answer is not an architecture in the expected format"
        )
    arch["questions"] = spec_author.merge_questions(
        request["questions"], arch["questions"]
    )
    arch["sources"] = research_tools.sources_of(calls)
    return arch


def draft(
    session: Session,
    spec: Specification,
    existing: Architecture | None = None,
) -> dict[str, Any]:
    """``prepare`` then ``draft_from``, in one thread."""
    return draft_from(prepare(session, spec, existing))


# ---------------------------------------------------------------------------
# The approved architecture in a task's prompts, and in the repository
# ---------------------------------------------------------------------------


def approved_for(session: Session, specification_id: int) -> Architecture | None:
    """The version of the company's architecture that holds for a specification
    (``arch_versions.for_specification``), if any."""
    spec = session.get(Specification, specification_id)
    return arch_versions.for_specification(session, spec) if spec else None


def content_for(session: Session, specification_id: int) -> dict[str, Any] | None:
    """That version's content as the specification sees it: each module's
    ``covers`` are its covers in that specification (``arch_versions.view_for``)."""
    arch = approved_for(session, specification_id)
    if arch is None:
        return None
    return arch_versions.view_for(
        content_of(arch), arch.specification_id, specification_id
    )


def section(arch: dict[str, Any], covers: list[str]) -> str:
    """The prompt section for a task covering the criteria *covers*.

    A task covering no criterion still gets the decisions and conventions,
    which hold for every task.
    """
    body = arch_check.as_prompt(arch, {c.split(".")[0] for c in covers})
    if not body:
        return ""
    return (
        "\n\n## Architecture\n"
        "From the approved architecture: the modules your requirements map to, "
        "with their interfaces, and the decisions and conventions every task "
        "follows. Build within them; a change to them is a decision for the "
        "Architect, not for this task.\n\n" + body
    )


def context_for(session: Session, sprint: Sprint | None, item: Any) -> str:
    """The architecture section for *item* of *sprint*; empty without an approved one."""
    if sprint is None or sprint.specification_id is None:
        return ""
    content = content_for(session, sprint.specification_id)
    return section(content, spec_planning.covers_of(item)) if content else ""


def planning_context(session: Session, specification_id: int) -> str:
    """The approved architecture for the PO's planning prompt, or nothing."""
    content = content_for(session, specification_id)
    if content is None:
        return ""
    return (
        "\n\n## Architecture (approved)\n"
        "Split the work along these modules: a task works in one module where "
        "it can, and names it in its description.\n\n" + arch_check.as_prompt(content)
    )


def document(session: Session, arch: Architecture) -> str:
    spec = session.get(Specification, arch.specification_id)
    earlier = arch_versions.earlier_specifications(session, arch)
    return arch_check.as_markdown(
        content_of(arch),
        spec.title if spec else "",
        arch.version,
        {key: str(e["title"]) for key, e in earlier.items()},
    )


async def _adr_entries(nats_client: Any, root: str, timeout: float) -> list[str]:
    """The ``docs/adr`` listing the daemon reports; empty when the folder is not there."""
    from services.workspace.workspace_daemon import DaemonError, daemon_request

    try:
        reply = await daemon_request(
            nats_client,
            "workspace.list",
            {"workspace_root": root, "path": adr_docs.ADR_DIRECTORY, "depth": 1},
            timeout,
        )
    except DaemonError as exc:
        if exc.status == 404 and exc.message.startswith("Not a folder"):
            return []
        raise
    if reply.get("truncated"):
        raise DaemonError(
            502,
            f"The {adr_docs.ADR_DIRECTORY} listing was truncated; "
            "the decisions cannot be numbered safely",
        )
    entries = reply.get("entries")
    return [entry for entry in entries or [] if isinstance(entry, str)]


async def write_document(
    nats_client: Any, session: Session, arch: Architecture, timeout: float
) -> dict[str, Any]:
    """Write ``docs/ARCHITECTURE.md`` and one ADR file per decision through the daemon.

    The repository's ``docs/adr`` is read first: the first free number numbers
    a decision with no file, and a decision whose slug is already there keeps
    its file and number (``adr_docs``). The returned dict is the document's
    report (``path``, ``sha256``) with ``files``: every file written, each
    with the sha256 the daemon reported, never one computed here. Raises
    ``DaemonUnavailable`` when no daemon answers, ``DaemonError``
    (``services.workspace.workspace_daemon``) when one refuses a file or
    cannot number the decisions, and ``ArchAuthorError`` when the company
    has no project folder.
    """
    from services.workspace.workspace_daemon import DaemonError, daemon_request

    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == arch.company_id)
        .order_by(text("created_at DESC"))
    ).first()
    if discovery is None or not discovery.project_path:
        raise ArchAuthorError("The company has no project folder to write to")
    root = discovery.project_path
    entries = await _adr_entries(nats_client, root, timeout)
    spec = session.get(Specification, arch.specification_id)
    approved_on = (
        arch.approved_at.date().isoformat()
        if arch.status != "draft" and arch.approved_at
        else None
    )
    adrs = adr_docs.adr_documents(
        content_of(arch),
        spec.title if spec else "",
        arch.version,
        first_number=adr_docs.first_free_number(entries),
        approved_on=approved_on,
        existing=adr_docs.adr_files(entries),
    )
    files = [
        {
            "path": ARCHITECTURE_PATH,
            "content": document(session, arch),
            "overwrite_existing": True,
        },
        *[
            {
                "path": adr["path"],
                "content": adr["markdown"],
                "overwrite_existing": True,
            }
            for adr in adrs
        ],
    ]
    reply = await daemon_request(
        nats_client,
        "workspace.generate",
        {
            "workspace_root": root,
            "files": files,
            "interaction_id": f"architecture-{arch.id}",
        },
        timeout,
    )
    reported = {
        f.get("path"): f
        for f in (reply.get("files") if isinstance(reply, dict) else None) or []
        if isinstance(f, dict)
    }
    by_path = {adr["path"]: adr for adr in adrs}
    written = []
    for item in files:
        got = reported.get(item["path"])
        if got is None or got.get("status") != "written" or not got.get("sha256"):
            raise DaemonError(
                502,
                f"The workspace daemon did not report {item['path']} as written",
            )
        entry: dict[str, Any] = {
            "path": item["path"],
            "sha256": str(got["sha256"]),
        }
        if isinstance(got.get("bytes"), int):
            entry["bytes"] = got["bytes"]
        adr = by_path.get(item["path"])
        if adr is None:
            entry["kind"] = "architecture"
        else:
            entry.update(
                {
                    "kind": "adr",
                    "decision_id": adr["id"],
                    "number": adr["number"],
                }
            )
        written.append(entry)
    document_written = next(
        entry for entry in written if entry["path"] == ARCHITECTURE_PATH
    )
    return {**document_written, "files": written}


def architecture_for_item(engine: Engine, item_id: int) -> str:
    """The architecture section for the task *item_id*, read from the database."""
    try:
        with Session(engine) as session:
            item = session.get(BacklogItem, item_id)
            if item is None:
                return ""
            return context_for(session, session.get(Sprint, item.sprint_id), item)
    except Exception as exc:  # noqa: BLE001 - The architecture is context; the task goes on without it.
        logger.debug("Architecture fetch failed for item=%s: %s", item_id, exc)
        return ""
