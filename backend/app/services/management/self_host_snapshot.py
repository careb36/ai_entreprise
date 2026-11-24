"""The self-hosting harness, for an owner action (``docs/design/owner-actions.md``).

``harness_dir`` finds ``scripts/self_host`` (the harness #329 and #701 built):
``SELF_HOST_REPOSITORY_DIR`` when set, else the backend's own checkout, else
the repository the compose file mounts at ``AGENT_WORKSPACE_ROOT``. The code
is La Fabrique's own, imported read-only, as the /bench page reads its
scorecards (``routers/bench.py``); the user's project is never read here.

``DaemonSnapshot`` is the reader the harness's ``records`` step takes
(``scripts/self_host/snapshot.py``) when the backend runs it: the snapshot
lives on the daemon's computer, so its files are read through
``workspace.git.read_at`` (the bytes as the snapshot committed them, decoded
here) and its status through ``workspace.git.diff``'s ``status`` view. The
backend never opens the folder.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from config import settings

READ_AT = "workspace.git.read_at"
DIFF = "workspace.git.diff"
CHECKOUT = "workspace.self_host.checkout"
# services/management/self_host_snapshot.py -> the repository, in a checkout.
_CHECKOUT_ROOT = Path(__file__).resolve().parents[4]
_MODULE_NAME = "fabrique_owner_action_self_host"


def harness_dir() -> Path | None:
    """The folder holding ``self_host.py``, or ``None`` when none is found."""
    candidates = [settings.SELF_HOST_REPOSITORY_DIR.strip(), str(_CHECKOUT_ROOT)]
    candidates.append(os.getenv("AGENT_WORKSPACE_ROOT", ""))
    for root in filter(None, candidates):
        folder = Path(root) / "scripts" / "self_host"
        if (folder / "self_host.py").is_file():
            return folder
    return None


def load_harness(folder: Path) -> ModuleType:
    """``self_host.py`` imported from *folder* (once per process)."""
    loaded = sys.modules.get(_MODULE_NAME)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, folder / "self_host.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import the harness from {folder}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_MODULE_NAME, None)
        raise
    return module


class DaemonSnapshot:
    """The snapshot on the daemon's computer, read through the daemon.

    *request* sends one daemon request and returns its reply; it is called
    from the harness's worker thread."""

    def __init__(self, request: Callable[[str, dict[str, Any]], dict], root: str):
        self.request, self.root = request, root

    def read(self, path: str) -> str | None:
        reply = self.request(
            READ_AT, {"workspace_root": self.root, "base": "main", "paths": [path]}
        )
        for entry in reply.get("files") or []:
            if entry.get("path") == path and entry.get("status") == "present":
                raw = base64.b64decode(entry.get("content_b64") or "")
                return raw.decode("utf-8", "replace")
        return None

    def dirty(self, *paths: str) -> str:
        reply = self.request(DIFF, {"workspace_root": self.root, "view": "status"})
        lines = [
            f"{e.get('code', '??')} {e.get('path', '')}"
            for e in reply.get("entries") or []
            if any(
                str(e.get("path", "")) == p
                or str(e.get("path", "")).startswith(p + "/")
                for p in paths
            )
        ]
        return "\n".join(lines)
