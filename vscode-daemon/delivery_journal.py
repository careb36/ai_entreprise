"""A queued delivery is applied once, even when it is delivered again.

``process_queued_delivery`` naks a job when its result cannot be published,
and JetStream hands the same job over again. Writing a whole file twice is
harmless; applying SEARCH/REPLACE edits twice is not: the search text is
usually gone (a success would come back as ``rejected_edit``), or, when the
replacement keeps it, the edit lands twice. So the first response is kept on
disk by ``job_id`` until the result is published, and a redelivery replays it
instead of touching the files again.

The journal lives in ``FABRIQUE_DAEMON_STATE`` (default
``~/.fabrique-daemon/deliveries``), one small JSON file per job, removed once
the job is acknowledged. A job without an id is simply run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("fabrique-daemon")


def _folder() -> Path:
    base = os.getenv("FABRIQUE_DAEMON_STATE") or os.path.expanduser("~/.fabrique-daemon")
    return Path(base) / "deliveries"


def _entry(job_id: Any) -> Path:
    # Hashed: a job id from the bus never becomes a path of its own.
    return _folder() / (hashlib.sha256(str(job_id).encode()).hexdigest() + ".json")


def once(job_id: Any, deliver: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """The response of *deliver* for *job_id*: replayed when recorded, else run and recorded."""
    if not job_id:
        return deliver()
    entry = _entry(job_id)
    try:
        return json.loads(entry.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logger.warning("Delivery journal entry unreadable (%s); delivering again", exc)
    response = deliver()
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        tmp = entry.with_suffix(".tmp")
        tmp.write_text(json.dumps(response), encoding="utf-8")
        tmp.replace(entry)
    except OSError as exc:
        logger.warning("Delivery journal not written (%s); a redelivery would apply again", exc)
    return response


def forget(job_id: Any) -> None:
    """Drop the entry of an acknowledged job."""
    if job_id:
        try:
            _entry(job_id).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Delivery journal entry not removed: %s", exc)
