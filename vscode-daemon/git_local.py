"""How a repository stands on this machine, read without contacting any remote.

What the New company form and the update of the local copy (``git_sync.py``,
issue #745) need to know before anything moves:

- ``current_branch``: what the person's checkout has checked out (``None``
  when detached);
- ``clean``: no uncommitted changes to tracked files in that checkout
  (untracked files do not count: a fast-forward never touches them unless it
  would overwrite one, and git refuses that itself);
- ``operation_in_progress``: a rebase, merge, cherry-pick or revert git has
  not finished, by the files git keeps for it (``rebase-merge``,
  ``MERGE_HEAD``...);
- ``remote_url``: the URL of the base's remote, with any credentials it
  carries removed (``git_workspace._strip_credentials``).

Only local git commands: ``status``, ``rev-parse``, ``symbolic-ref`` and
``config``. Nothing here contacts a remote, whatever the forge policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from git_workspace import _strip_credentials, run_git

# The files git keeps while an operation is unfinished, and the name we give it.
_IN_PROGRESS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
)


def current_branch(checkout: Path) -> Optional[str]:
    head = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], checkout, check=False)
    return (head.stdout.strip() or None) if head.returncode == 0 else None


def clean(checkout: Path) -> bool:
    """True when *checkout* has no uncommitted change to a tracked file."""
    status = run_git(["status", "--porcelain", "--untracked-files=no"], checkout)
    return not status.stdout.strip()


def operation_in_progress(checkout: Path) -> Optional[str]:
    """``rebase``, ``merge``, ``cherry-pick`` or ``revert`` when one is unfinished in *checkout*."""
    for name, operation in _IN_PROGRESS:
        found = run_git(["rev-parse", "--git-path", name], checkout, check=False)
        if found.returncode != 0 or not found.stdout.strip():
            continue
        path = Path(found.stdout.strip())
        if not path.is_absolute():
            path = checkout / path
        if path.exists():
            return operation
    return None


def remote_url(root: Path, remote: str) -> Optional[str]:
    """*remote*'s URL without credentials; ``None`` when it has none configured."""
    url = run_git(["config", "--get", f"remote.{remote}.url"], root, check=False).stdout.strip()
    return _strip_credentials(url) if url else None


def state(checkout: Path) -> Dict[str, Any]:
    """The checkout as it is now: its branch, whether it is clean, and any unfinished operation."""
    return {
        "current_branch": current_branch(checkout),
        "clean": clean(checkout),
        "operation_in_progress": operation_in_progress(checkout),
    }
