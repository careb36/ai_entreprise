"""``workspace.hash``: the sha256 of named files as they are on disk.

An observation the backend compares with what an earlier delivery wrote
(``services/acceptance.py`` keeps the acceptance tests QA delivered intact):
a file changed behind the loop's back is seen, and never trusted on the
agent's word.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def hash_files(
    workspace_path: str, paths: list, resolve_target_path: Callable[[str, str], Optional[Path]]
) -> List[Dict[str, Any]]:
    """One dict per path: ``status`` ``present`` with its ``sha256`` and
    ``bytes``, ``missing`` when there is no such file, ``rejected`` for a path
    outside the workspace, ``workspace_missing`` when the folder is not here."""
    results = []
    workspace_root = Path(workspace_path).expanduser()
    if not workspace_root.is_absolute() or not workspace_root.is_dir():
        return [{"path": p, "status": "workspace_missing"} for p in paths]
    for requested in paths:
        target = resolve_target_path(workspace_path, requested) if isinstance(requested, str) else None
        if not target or target == workspace_root.resolve():
            results.append({"path": requested, "status": "rejected"})
        elif not target.is_file():
            results.append({"path": requested, "status": "missing"})
        else:
            on_disk = target.read_bytes()
            results.append({
                "path": requested,
                "status": "present",
                "bytes": len(on_disk),
                "sha256": hashlib.sha256(on_disk).hexdigest(),
            })
    return results


async def handle_hash(nc, msg, reply, request_body, resolve_target_path) -> None:
    """``workspace.hash`` → ``{"files": [...]}`` or ``{"error": {status, message}}``."""
    data = request_body(msg)
    root, paths = data.get("workspace_root"), data.get("paths")
    if not isinstance(root, str) or not isinstance(paths, list):
        body = {"error": {"status": 400, "message": "workspace_root and paths are required."}}
    else:
        body = {"files": await asyncio.to_thread(hash_files, root, paths, resolve_target_path)}
    await reply(nc, msg, body)
