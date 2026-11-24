"""The docs-only write policy: a documentation task writes documentation only (issue #766).

Documenting a whole project (``docs/design/agent-toolsets.md``, "Next changes"
item 10) needs to read all of the code and to write only documentation in the
task's worktree. A toolbox whose task is documentation (``applies``: the
TechWriter role, or a task whose kind or step is ``documentation``) gets
``docs_only``; then every write the edit tools ask (``workspace__edit``,
``move``, ``delete``: ``workspace_edits._deliverable``) must name a
documentation path (``is_doc_path``):

- anything under a folder named ``docs`` or ``doc``;
- a ``README`` file, whatever its suffix;
- Markdown or reStructuredText anywhere (``.md``, ``.markdown``, ``.mdx``, ``.rst``).

Any other path is refused before the daemon is asked, with a reply that says
why and what may be written, so the model changes course instead of retrying.
The reading tools are unchanged. This is the backend's decision from the
task, never the model's; the daemon's own path checks still apply.

Not covered yet: the files an answer delivers in its own blocks
(``workspace.generate``) are not filtered by this policy.
"""

from __future__ import annotations

from typing import Any

from services.contracts import roles

DOC_ROLES = frozenset({roles.TECH_WRITER})
DOC_KINDS = frozenset({"documentation", "docs"})
DOC_FOLDERS = frozenset({"docs", "doc"})
DOC_SUFFIXES = (".md", ".markdown", ".mdx", ".rst")
REFUSAL = (
    "a documentation task writes documentation only: {path} is refused; write "
    "under docs/ or doc/, README files, or .md, .markdown, .mdx, .rst files"
)


def applies(role: str | None, kind: str | None = None) -> bool:
    """Whether a task is documentation: its role, or its kind or step."""
    if roles.canonical(role) in DOC_ROLES:
        return True
    return str(kind or "").strip().lower() in DOC_KINDS


def apply(box: Any, role: str | None, kind: str | None = None) -> None:
    """Set *box*'s ``docs_only`` from ``applies``."""
    if box is not None:
        box.docs_only = applies(role, kind)


def is_doc_path(path: str) -> bool:
    """A path a documentation task may write (module docstring)."""
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    return (
        any(p.lower() in DOC_FOLDERS for p in parts[:-1])
        or name.upper().startswith("README")
        or name.lower().endswith(DOC_SUFFIXES)
    )


def check(box: Any, path: str) -> None:
    """Refuse a write outside the documentation when *box* is docs-only."""
    if getattr(box, "docs_only", False) and not is_doc_path(path):
        raise RuntimeError(REFUSAL.format(path=path or "(no path)"))
