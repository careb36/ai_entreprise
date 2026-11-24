"""How the harness reads the snapshot it made (``--checkout``).

The ``records`` step compares the architecture's document and ADRs with the
snapshot's files, then checks git sees no change once the product has written
them. On the command line the snapshot is a folder on this computer:
``Local`` reads it and asks git. An owner action started from the app
(``docs/design/owner-actions.md``) runs the same step inside the backend,
which never touches the user's files; it passes a reader that asks the
workspace daemon instead (``backend/app/services/management/
self_host_snapshot.py``), with the same two methods.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class Local:
    """The snapshot as a folder on this computer."""

    def __init__(self, root: Path, git: Callable[..., str]):
        self.root, self.git = root, git

    def read(self, path: str) -> str | None:
        """The file's text, or ``None`` when it is not there."""
        target = self.root / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    def dirty(self, *paths: str) -> str:
        """``git status --porcelain`` for *paths*, stripped: empty when unchanged."""
        return self.git(self.root, "status", "--porcelain", "--", *paths).strip()
