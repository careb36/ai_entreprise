"""The docs-only write policy: a documentation task writes documentation only (issue #766).

Documenting a whole project (``docs/design/agent-toolsets.md``, "Next changes"
item 10) needs to read all of the code and to write only documentation in the
task's worktree. Where a turn writes is the step's rule
(``contracts/tool_steps.write_scope``): the documentation step, and the
TechWriter in any step that writes (a role's narrowing within ``W``, never a
widening), write documentation only. ``apply`` reads that rule, or a task
whose kind is ``documentation``, and sets the toolbox's ``docs_only``; then
every write the edit tools ask (``workspace__edit``, ``move``, ``delete``:
``workspace_edits._deliverable``) must name a documentation path
(``tool_steps.is_doc_path``):

- anything under a folder named ``docs`` or ``doc``;
- a ``README`` file, whatever its suffix;
- Markdown or reStructuredText anywhere (``.md``, ``.markdown``, ``.mdx``, ``.rst``).

Any other path is refused before the daemon is asked, with a reply that says
why and what may be written, so the model changes course instead of retrying.
The reading tools are unchanged. This is the backend's decision from the
task, never the model's; the daemon's own path checks still apply.

The files an answer delivers in its own blocks (``workspace.generate``) follow
the same rule in the loop (``services/loop/worker/deliver.py``): a path
outside the documentation is left out of the delivery and recorded on the
run's evidence (``docs_write``) with the reason.
"""

from __future__ import annotations

from typing import Any

from services.contracts import roles, tool_steps

REFUSAL = (
    "a documentation task writes documentation only: {path} is refused; write "
    "under docs/ or doc/, README files, or .md, .markdown, .mdx, .rst files"
)
is_doc_path = tool_steps.is_doc_path


def applies(
    role: str | None, kind: str | None = None, step: str = tool_steps.EXECUTE
) -> bool:
    """Whether a turn writes documentation only: its step and role
    (``tool_steps.write_scope``), or a task whose kind is documentation."""
    if step == tool_steps.EXECUTE:
        step = tool_steps.execution_step(kind)
    return tool_steps.write_scope(step, roles.canonical(role)) == tool_steps.DOCS


def apply(
    box: Any,
    role: str | None,
    kind: str | None = None,
    step: str = tool_steps.EXECUTE,
) -> None:
    """Set *box*'s ``docs_only`` from ``applies``."""
    if box is not None:
        box.docs_only = applies(role, kind, step)


def check(box: Any, path: str) -> None:
    """Refuse a write outside the documentation when *box* is docs-only."""
    if getattr(box, "docs_only", False) and not is_doc_path(path):
        raise RuntimeError(REFUSAL.format(path=path or "(no path)"))
