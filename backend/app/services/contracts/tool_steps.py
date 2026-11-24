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
   tools as a Developer.

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

CEILING: dict[str, frozenset[str]] = {
    EXECUTE: frozenset({READ, WRITE, RUN, RESEARCH}),
    ARCHITECT_BRIEF: frozenset({READ, RESEARCH}),
    QA_REVIEW: frozenset({READ}),
    PLAN: frozenset({READ}),
    SPEC_AUTHOR: frozenset({READ, RESEARCH}),
    ARCH_AUTHOR: frozenset({READ, RESEARCH}),
    SCRUM_DECISION: frozenset({READ}),
}

# What a role drops within any step, by catalogue name (``roles.canonical``).
# Empty by default: a family is denied here only with a reason beside it.
ROLE_DENY: dict[str, frozenset[str]] = {}


def _key(role: str | None) -> str:
    """*role* lowercased, without spaces, dashes, underscores or slashes."""
    return "".join(ch for ch in (role or "").lower() if ch not in " -_/")


def denied(role: str | None) -> frozenset[str]:
    """The families *role* drops (``ROLE_DENY``), matched by its normalised name."""
    key = _key(role)
    for name, deny in ROLE_DENY.items():
        if _key(name) == key:
            return frozenset(deny)
    return frozenset()


def families(step: str, role: str | None = None) -> frozenset[str]:
    """The families a turn of *step* by *role* may be offered: the step's
    ceiling minus the role's deny set. An unknown step offers nothing."""
    return CEILING.get(step, frozenset()) - denied(role)


def allows(step: str, role: str | None, family: str) -> bool:
    """Whether a turn of *step* by *role* may be offered *family*."""
    return family in families(step, role)


def record(step: str, role: str | None = None) -> dict[str, object]:
    """The step and its families for the run's ``tools_offered``, in a fixed order."""
    allowed = families(step, role)
    return {"step": step, "families": [f for f in FAMILIES if f in allowed]}
