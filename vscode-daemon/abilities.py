"""
What this daemon announces it can do (issue #315, protocol §2.1).

Until now the backend guessed a daemon's abilities from its replies: a subject
nobody answers ("no responders") was read as "an older daemon", a reply
without ``full_log`` as "a daemon from before full logs". The daemon now says
which subjects it serves and which optional request fields it honours, in
``workspace.status`` and in every ``workspace.heartbeat`` (so from the moment
it connects), under ``abilities``:

    {"abilities_version": 1,
     "subjects": ["workspace.app.check", ...],
     "features": {"workspace.execute": ["full_log", ...], ...}}

This is the daemon's claim about itself, never an observation of a run: the
backend keeps it under ``reported_by_daemon`` and uses it only to choose
whether to send a request and how to word a reply that lacks a field; what a
run did is still read from that run's reply (``vscode-daemon/protocol/
PROTOCOL.md`` §2.1). A daemon from before this module announces nothing, and
the backend keeps guessing as before.

``SUBJECTS`` is kept equal to the request subjects of ``daemon.subscriptions``
by ``backend/tests/test_daemon_protocol.py``.
"""

from typing import Any, Dict

# Raised when the announcement's shape changes in a way an older reader would
# misread; adding a subject or a feature keeps it.
ABILITIES_VERSION = 1

# Every request subject this daemon serves.
SUBJECTS = (
    "workspace.app.check",
    "workspace.browse",
    "workspace.capabilities",
    "workspace.console.collect",
    "workspace.console.prepare",
    "workspace.edit",
    "workspace.edit.finish",
    "workspace.execute",
    "workspace.find",
    "workspace.generate",
    "workspace.git.base_status",
    "workspace.git.diff",
    "workspace.git.integrate",
    "workspace.git.merge",
    "workspace.git.publish",
    "workspace.git.read_at",
    "workspace.git.reviews",
    "workspace.git.sync_base",
    "workspace.graph",
    "workspace.hash",
    "workspace.inspect",
    "workspace.list",
    "workspace.map",
    "workspace.read",
    "workspace.related",
    "workspace.sandbox.image",
    "workspace.sandbox.reset",
    "workspace.search",
    "workspace.self_host.checkout",
    "workspace.self_host.git",
    "workspace.self_host.land",
    "workspace.self_host.push",
    "workspace.signatures",
    "workspace.status",
    "workspace.undo",
)

# The optional request fields added within protocol version 1 (PROTOCOL.md
# §5.1, §5.1.1, §5.2, §5.3), per subject: an older daemon ignores them silently.
_FORGE = ("forge_policy",)
FEATURES = {
    # The forge policy (§5.3, issue #727): enforced by this daemon; and the
    # task's worktree without a private repository, for the Developer's tools
    # (§5.1.5, docs/design/first-attempt-in-task-worktree.md).
    "workspace.console.prepare": _FORGE + ("private_git",),
    # An emptied file deleted by a delivery (docs/design/move-and-delete-files.md).
    "workspace.generate": _FORGE + ("delete",),
    "workspace.edit": ("delete", "move"),
    "workspace.git.base_status": _FORGE,
    # The agents' read-only views of a task's repository (git_view.py, §5.6),
    # and the blame view, announced apart so an older daemon is never asked it.
    "workspace.git.diff": _FORGE + ("view", "blame"),
    "workspace.git.integrate": _FORGE,
    "workspace.git.merge": _FORGE,
    "workspace.git.publish": _FORGE,
    "workspace.git.reviews": _FORGE,
    "workspace.git.sync_base": _FORGE,
    # A document's text instead of its bytes (document_text.py, §5.8).
    "workspace.read": ("document_text",),
    "workspace.execute": ("full_log", "purpose_assistant", "sandbox_environment", "sandbox_floor"),
    # Where a name is defined and who uses it (code_lookup.py, §5.7), for
    # the agents' navigation tools.
    "workspace.related": ("lookup",),
    "workspace.app.check": ("browser_probe", "cache", "full_log", "sandbox_environment", "security_probe"),
}


def announcement() -> Dict[str, Any]:
    """The ``abilities`` field of ``workspace.status`` and the heartbeat."""
    return {
        "abilities_version": ABILITIES_VERSION,
        "subjects": list(SUBJECTS),
        "features": {subject: list(names) for subject, names in sorted(FEATURES.items())},
    }
