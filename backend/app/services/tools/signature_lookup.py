"""The signatures the Verifier's ``grounded`` check compares calls with.

After a delivery is written, the backend reads the delivered Python files'
calls (``services.evidence.signatures.wanted``) and asks the daemon
(``workspace.signatures``, ``vscode-daemon/signatures.py``) for the
signatures of the names they import from project files the delivery did not
write, in the folder the files went to (the task's worktree with the Git
workflow, else the project). The reply is recorded on the evidence as
``signatures``: an observation, with the daemon's sha256 per file.
Re-verification reads that record and never asks the daemon again.

The wait is bounded (``TIMEOUT_SECONDS``). Daemons that announce they do not
serve the subject (``daemon_liveness.announces``, their claim) are not asked;
without an announcement a daemon without the subject is read as an older one.
Either, a timeout or an error is recorded as ``{"observed": false,
"reason": …}`` and never fails or holds the delivery.
"""

from __future__ import annotations

from typing import Any

from logging_config import setup_logger

from services.evidence import signatures
from services.workspace import daemon_liveness
from services.workspace.workspace_daemon import (
    DaemonError,
    DaemonUnavailable,
    daemon_request,
)

logger = setup_logger(__name__)

SUBJECT = "workspace.signatures"
TIMEOUT_SECONDS = 10.0


async def observe(
    nats_client: Any, evidence: dict[str, Any] | None, files: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Record ``signatures`` on *evidence* for the delivered Python *files*."""
    if evidence is None or not signatures.has_python(files):
        return None
    try:
        asked, capped = signatures.wanted(evidence, files)
    except Exception as exc:  # noqa: BLE001 - The delivery is not the reading's hostage.
        logger.warning("Could not read a delivery's calls: %s", exc)
        return None
    record: dict[str, Any] = {"asked": asked, "capped": capped}
    root = evidence.get("workspace_root")
    if evidence.get("workspace_writes") is None:
        record.update(observed=False, reason="the delivery was not written")
    elif not asked:
        record.update(observed=True, files=[])
    elif not root:
        record.update(observed=False, reason="no project folder")
    elif daemon_liveness.announces(SUBJECT) is False:
        record.update(
            observed=False, reason=daemon_liveness.not_announced_reason(SUBJECT)
        )
    else:
        try:
            reply = await daemon_request(
                nats_client,
                SUBJECT,
                {"workspace_root": root, "wanted": asked},
                TIMEOUT_SECONDS,
            )
            rows = reply.get("files")
            if isinstance(rows, list):
                record.update(observed=True, files=rows)
            else:
                record.update(observed=False, reason="the daemon sent no files")
        except DaemonUnavailable as exc:
            record.update(
                observed=False,
                reason=(
                    _absent_reason()
                    if exc.absent
                    else f"the daemon did not answer {SUBJECT} ({exc})"
                ),
            )
        except DaemonError as exc:
            record.update(observed=False, reason=f"the daemon refused: {exc}")
        except Exception as exc:  # noqa: BLE001 - Never fails the delivery.
            record.update(observed=False, reason=f"{type(exc).__name__}")
    evidence["signatures"] = record
    return record


def _absent_reason() -> str:
    """Why nobody answered: a guess only when the daemons announced nothing."""
    if daemon_liveness.announces(SUBJECT):
        return f"no daemon answers {SUBJECT}, although the daemons announce it"
    return f"no daemon answers {SUBJECT} (an older daemon)"
