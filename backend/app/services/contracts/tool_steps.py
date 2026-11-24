"""The step sets the tool ceiling; the role only narrows (owner, 2026-11-24).

Which tool families a turn may be offered is decided in two layers
(``docs/design/agent-toolsets.md``, "The step sets the ceiling, the role
narrows"):

1. **The step** (``CEILING``) sets the most a turn may do. An execution turn
   that delivers files may read, write in the task's worktree, run the
   folder's tests and committed scripts, and research. A brief, a review or a
   planning turn may only read (and, where a choice is made, research).
2. **The role** (``ROLE_DENY``) may only drop families within the step,
   never add one: ``families`` is the step's ceiling minus the role's deny
   set, so no role goes above its step. Empty for every role today: a
   TechWriter, a SecOps, an Architect or a PO executing a task gets the same
   tool families as a Developer.

Within ``W`` a step or a role may narrow *where* a turn writes
(``write_scope``): the documentation step (``DOCUMENT``) and the TechWriter
(``ROLE_WRITE_SCOPE``) write documentation only (``DOCS``: ``is_doc_path``).
Like a deny, a write scope only narrows: it never offers ``W`` where the step
does not. The tools context enforces it on the edit tools
(``services/tools/docs_write_policy.py``) and the loop on the files an answer
delivers in its own blocks (``services/loop/worker/deliver.py``).

The families:

- ``R`` read: list, read, search, find_file, read-only Git;
- ``W`` write: edit, move, delete, only where edits may go
  (``workspace_edits.allowed``: the task's worktree, or a project without
  the Git workflow);
- ``X`` run: the folder's tests (narrowed) and committed check scripts;
- ``S`` research: registries, advisories, fetch of allowed hosts.

What a family turns into (which tools, under which further conditions) is
the tools context's decision (``services/tools/developer_tools.py``); this
module is the rule alone, pure and without imports. The role keeps its
prompt and judgement; separation of duties and protected paths apply
whatever tools a turn had.
"""

from __future__ import annotations

READ, WRITE, RUN, RESEARCH = "R", "W", "X", "S"
FAMILIES: tuple[str, ...] = (READ, WRITE, RUN, RESEARCH)

# The steps that offer tools.
EXECUTE = "execute"
ARCHITECT_BRIEF = "architect_brief"
QA_REVIEW = "qa_review"
PLAN = "plan"
SPEC_AUTHOR = "spec_author"
ARCH_AUTHOR = "arch_author"
SCRUM_DECISION = "scrum_decision"
DOCUMENT = "documentation"  # Document a project: write documentation only.

CEILING: dict[str, frozenset[str]] = {
    EXECUTE: frozenset({READ, WRITE, RUN, RESEARCH}),
    ARCHITECT_BRIEF: frozenset({READ, RESEARCH}),
    QA_REVIEW: frozenset({READ}),
    PLAN: frozenset({READ}),
    SPEC_AUTHOR: frozenset({READ, RESEARCH}),
    ARCH_AUTHOR: frozenset({READ, RESEARCH}),
    SCRUM_DECISION: frozenset({READ}),
    DOCUMENT: frozenset({READ, WRITE, RUN, RESEARCH}),
}

# What a role drops within any step, by catalogue name (``roles.canonical``).
# Empty by default: a family is denied here only with a reason beside it.
ROLE_DENY: dict[str, frozenset[str]] = {}

# Where ``W`` may write, when narrowed. ``DOCS``: documentation paths only.
DOCS = "docs"
DOC_FOLDERS = frozenset({"docs", "doc"})
DOC_SUFFIXES = (".md", ".markdown", ".mdx", ".rst")
STEP_WRITE_SCOPE: dict[str, str] = {DOCUMENT: DOCS}
# A task whose kind is one of these is executed as the documentation step.
DOC_KINDS = frozenset({"documentation", "docs"})
# The TechWriter writes documentation (``docs/design/agent-toolsets.md``,
# "Document a whole project": W docs only), whatever task it executes.
ROLE_WRITE_SCOPE: dict[str, str] = {"TechWriter": DOCS}


def _key(role: str | None) -> str:
    """*role* lowercased, without spaces, dashes, underscores or slashes."""
    return "".join(ch for ch in (role or "").lower() if ch not in " -_/")


def denied(role: str | None) -> frozenset[str]:
    """The families *role* drops (``ROLE_DENY``), matched by its normalised name."""
    return frozenset(_role_entry(ROLE_DENY, role) or ())


def _role_entry(table: dict[str, object], role: str | None) -> object:
    key = _key(role)
    return next((v for name, v in table.items() if _key(name) == key), None)


def families(step: str, role: str | None = None) -> frozenset[str]:
    """The families a turn of *step* by *role* may be offered: the step's
    ceiling minus the role's deny set. An unknown step offers nothing."""
    return CEILING.get(step, frozenset()) - denied(role)


def allows(step: str, role: str | None, family: str) -> bool:
    """Whether a turn of *step* by *role* may be offered *family*."""
    return family in families(step, role)


def write_scope(step: str, role: str | None = None) -> str | None:
    """Where a turn of *step* by *role* may write: ``DOCS``, or ``None`` for
    anywhere ``W`` reaches; ``None`` too when the turn may not write at all."""
    if WRITE not in families(step, role):
        return None
    scope = STEP_WRITE_SCOPE.get(step) or _role_entry(ROLE_WRITE_SCOPE, role)
    return str(scope) if scope else None


def execution_step(kind: str | None = None) -> str:
    """The step a task of *kind* is executed as: ``DOCUMENT`` for a
    documentation task, else ``EXECUTE``."""
    return DOCUMENT if str(kind or "").strip().lower() in DOC_KINDS else EXECUTE


def is_doc_path(path: str | None) -> bool:
    """A documentation path: under a ``docs`` or ``doc`` folder, a ``README``
    whatever its suffix, or Markdown or reStructuredText anywhere."""
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    return (
        any(p.lower() in DOC_FOLDERS for p in parts[:-1])
        or name.upper().startswith("README")
        or name.lower().endswith(DOC_SUFFIXES)
    )


def writable(step: str, role: str | None, path: str | None) -> bool:
    """Whether *path* is within the write scope of a turn of *step* by *role*
    (whether ``W`` is offered at all is ``allows``)."""
    return write_scope(step, role) != DOCS or is_doc_path(path)


def record(step: str, role: str | None = None) -> dict[str, object]:
    """The step and its families for the run's ``tools_offered``, in a fixed
    order, and the write scope when it is narrowed."""
    allowed = families(step, role)
    out: dict[str, object] = {
        "step": step,
        "families": [f for f in FAMILIES if f in allowed],
    }
    scope = write_scope(step, role)
    if scope:
        out["write_scope"] = scope
    return out
