"""The task ids an item depends on, read from a row or a dict.

Pure, so the Git workflow's delivery spec (``workspace.git_workflow``) and the
dependencies between a sprint's tasks (``tasks.dependencies``, which
re-exports it) read ``depends_on`` the same way (ADR 0013).
"""

from __future__ import annotations

from typing import Any


def depends_on_of(item: Any) -> list[int]:
    """The task ids an item (row or dict) depends on."""
    raw = (
        item.get("depends_on")
        if isinstance(item, dict)
        else getattr(item, "depends_on", None)
    )
    if not isinstance(raw, list):
        return []
    ids = []
    for value in raw:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(ids))
