"""The Architect drafts the architecture of an approved specification.

From the specification, the project's detected stack and the files at its
root, the Architect proposes the modules, the interfaces between them, the
decisions and the conventions, in a fixed JSON shape a parser checks, and
lists what it could not decide. The draft is a claim: ``arch_check``
numbers it and says what blocks its approval, a person answers, edits and
approves. Once approved, ``context_for`` gives each task the modules its
requirements map to, and ``write_document`` sends it to the repository
through the workspace daemon, whose sha256 of the bytes on disk is what the
backend keeps.
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

from services.evidence import standards
from services.planning import arch_check, spec_author, spec_check, spec_planning
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
        lines += [
            "## The current draft",
            (
                "Revise it: keep what the answers confirm, change what they correct, and "
                "ask again only what is still open."
            ),
            "",
            json.dumps(
                {
                    k: existing.get(k)
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
    return [
        r["id"] for r in spec.requirements or [] if isinstance(r, dict) and r.get("id")
    ]


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
    try:
        answer, calls = spec_author.ask_with_tools(
            request["model"],
            request["system_prompt"],
            request["prompt"],
            max_tokens=DRAFT_MAX_TOKENS,
            role=ARCHITECT_ROLE,
            bu_id=request["bu_id"],
            toolbox=research_tools.toolbox_for_request(ARCHITECT_ROLE, request),
            company_id=request.get("company_id"),
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
    """The approved architecture of a specification, if any."""
    return session.exec(
        select(Architecture)
        .where(
            Architecture.specification_id == specification_id,
            Architecture.status == "approved",
        )
        .order_by(Architecture.version.desc())
    ).first()


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
    arch = approved_for(session, sprint.specification_id)
    return section(content_of(arch), spec_planning.covers_of(item)) if arch else ""


def planning_context(session: Session, specification_id: int) -> str:
    """The approved architecture for the PO's planning prompt, or nothing."""
    arch = approved_for(session, specification_id)
    if arch is None:
        return ""
    return (
        "\n\n## Architecture (approved)\n"
        "Split the work along these modules: a task works in one module where "
        "it can, and names it in its description.\n\n"
        + arch_check.as_prompt(content_of(arch))
    )


def document(session: Session, arch: Architecture) -> str:
    spec = session.get(Specification, arch.specification_id)
    return arch_check.as_markdown(
        content_of(arch), spec.title if spec else "", arch.version
    )


async def write_document(
    nats_client: Any, session: Session, arch: Architecture, timeout: float
) -> dict[str, Any]:
    """Send ``docs/ARCHITECTURE.md`` to the repository through the daemon.

    Returns the daemon's report of the file; raises ``DaemonUnavailable`` or
    ``DaemonError`` (``services.workspace.workspace_daemon``) otherwise, and
    ``ArchAuthorError`` when the company has no project folder.
    """
    from services.workspace.workspace_daemon import DaemonError, daemon_request

    discovery = session.exec(
        select(ProjectDiscovery)
        .where(ProjectDiscovery.company_id == arch.company_id)
        .order_by(text("created_at DESC"))
    ).first()
    if discovery is None or not discovery.project_path:
        raise ArchAuthorError("The company has no project folder to write to")
    reply = await daemon_request(
        nats_client,
        "workspace.generate",
        {
            "workspace_root": discovery.project_path,
            "files": [
                {
                    "path": ARCHITECTURE_PATH,
                    "content": document(session, arch),
                    "overwrite_existing": True,
                }
            ],
            "interaction_id": f"architecture-{arch.id}",
        },
        timeout,
    )
    files = reply.get("files") if isinstance(reply, dict) else None
    written = next(
        (
            f
            for f in files or []
            if isinstance(f, dict) and f.get("path") == ARCHITECTURE_PATH
        ),
        None,
    )
    if (
        written is None
        or written.get("status") != "written"
        or not written.get("sha256")
    ):
        raise DaemonError(
            502, "The workspace daemon did not report the file as written"
        )
    return written


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
