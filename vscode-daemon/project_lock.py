"""One lock per project folder, shared by everything that writes the person's files.

The daemon serves requests in worker threads, so a delivery
(``delivery_snapshot.deliver``), an edit of the Developer's tool
(``workspace_edit``) and an undo (``delivery_snapshot.undo``) can run at the
same time on the same project. Each holds the project's lock from its first
read to its last write: an undo never checks a file, then restores it over a
write that came in between, and a snapshot always describes the bytes the
delivery replaced.

Keyed by the canonical path of the project (``~`` expanded, symlinks
resolved). Reentrant, so a holder may call another locked step.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict

_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


def canonical(workspace_path: str) -> str:
    try:
        return str(Path(workspace_path).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(Path(workspace_path).expanduser())


def lock_for(workspace_path: str) -> threading.RLock:
    """The lock of the project at *workspace_path*, the same object for every alias of it."""
    key = canonical(workspace_path)
    with _GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock
