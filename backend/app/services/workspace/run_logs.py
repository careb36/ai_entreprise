"""A run's whole output, read back from the object store and hashed here (#315).

The daemon's replies keep a bounded tail of each command's output. With
``DELIVERY_FULL_LOGS`` (on by default) and the bucket in place
(``nats_client.NATSClient.run_logs_bucket``, created with JetStream), the
install, the test run and the application check ask for the whole output:
``full_log: {"bucket", "name"}`` on the request, a fresh ``run-<hex>`` name
per run. The daemon uploads each stream as ``<name>/<stream>``
(``vscode-daemon/run_logs.py``) and its reply lists the objects.

The backend then reads every object back and computes its size and sha256
itself: the evidence records ``{object, size, sha256, truncated}`` per
stream, ``observed_by: backend``. A hash is never taken from the daemon's
reply (rule 6 of ``docs/POSITIONING.md``); the daemon's own counts
(``daemon_bytes``, ``total_bytes``, ``truncated``: it cut the stream at its
cap) are kept as what it said. Anything that prevents it (the setting off,
no JetStream, a daemon from before full logs, an upload or a read that
failed) is ``{observed: false, reason}``: never a hash of nothing. A reply
without ``full_log`` is worded from what the daemons announce
(``daemon_liveness.announces``, their claim): "does not announce full logs",
"announced full logs but sent none", or, when nothing was announced, the guess
"a daemon from before full logs".

Retention is the bucket's: objects expire after ``FULL_LOG_RETENTION_DAYS``
and the oldest go first beyond ``FULL_LOG_BUCKET_MAX_MB``. The sha256 on the
evidence stays, so a log read later can still be checked against it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any

from config import settings

from services.workspace import daemon_liveness

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
# How long reading back one run's objects may take before it is given up.
READ_SECONDS = 120.0
OLDER_DAEMON = (
    "the daemon reported no full log (a daemon from before full logs ignores "
    "the request)"
)


class _Hasher:
    """Where the object store writes an object's bytes: counted and hashed,
    never held in memory."""

    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.size += len(data)
        return len(data)


def request_spec(nats_client: Any) -> dict[str, Any]:
    """``{"bucket", "name"}`` for the next run's request, or ``{"reason"}``
    when no full log can be asked for."""
    if not settings.DELIVERY_FULL_LOGS:
        return {"reason": "full logs are turned off (DELIVERY_FULL_LOGS=false)"}
    bucket = getattr(nats_client, "run_logs_bucket", None)
    if not isinstance(bucket, str) or getattr(nats_client, "js", None) is None:
        return {
            "reason": "no object store for full logs (JetStream is not enabled, "
            "or the bucket could not be created)"
        }
    return {"bucket": bucket, "name": f"run-{uuid.uuid4().hex}"}


def request_field(spec: dict[str, Any]) -> dict[str, Any]:
    """The request's ``full_log`` field, or nothing when none is asked for."""
    if not spec.get("bucket"):
        return {}
    return {"full_log": {"bucket": spec["bucket"], "name": spec["name"]}}


def _not_observed(reason: str) -> dict[str, Any]:
    return {"observed": False, "reason": reason}


def _missing_reason(subject: str) -> str:
    """Why a reply to *subject* carries no ``full_log``, from the announcement."""
    announced = daemon_liveness.announces(subject, "full_log")
    if announced is None:
        return OLDER_DAEMON
    if announced is False:
        return daemon_liveness.not_announced_reason(subject, "full_log")
    return (
        f"the daemon reported no full log, although it announces full_log on {subject}"
    )


async def observe(
    nats_client: Any,
    spec: dict[str, Any],
    reply: Any,
    subject: str = "workspace.execute",
) -> dict[str, Any]:
    """The full log's record for the evidence: every object the daemon says it
    wrote for this run, read back and hashed here."""
    if not spec.get("bucket"):
        return _not_observed(spec.get("reason") or "no full log was asked for")
    full = reply.get("full_log") if isinstance(reply, dict) else None
    if not isinstance(full, dict):
        return _not_observed(_missing_reason(subject))
    if full.get("error"):
        return _not_observed(f"the daemon could not upload it: {full['error']}")
    entries = [e for e in full.get("objects") or [] if isinstance(e, dict)]
    if not entries:
        return _not_observed(full.get("reason") or "the daemon uploaded no object")
    try:
        objects = await asyncio.wait_for(
            _read_all(nats_client, spec, entries), timeout=READ_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - The run's evidence stands; the full log is said not observed.
        logger.warning("Full log %s not read back: %s", spec["name"], exc)
        return _not_observed(
            f"the backend could not read it back ({type(exc).__name__}: {exc})"[:300]
        )
    return {
        "observed": True,
        "observed_by": "backend",
        "bucket": spec["bucket"],
        "objects": objects,
    }


async def _read_all(
    nats_client: Any, spec: dict[str, Any], entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    store = await nats_client.js.object_store(spec["bucket"])
    cap = max(int(settings.DELIVERY_FULL_LOG_MAX_MB or 0), 1) * MIB
    prefix = f"{spec['name']}/"
    objects = []
    for entry in entries:
        name = entry.get("object")
        said = {
            "stream": entry.get("stream"),
            "object": name,
            "truncated": bool(entry.get("truncated")),
            "daemon_bytes": entry.get("bytes"),
            "total_bytes": entry.get("total_bytes"),
        }
        if not isinstance(name, str) or not name.startswith(prefix):
            # Only the names this run asked for: never another run's object.
            objects.append(
                {**said, "observed": False, "reason": "not an object of this run"}
            )
            continue
        read = {**said, **await _read_one(store, name, cap)}
        if read.get("observed") and read["size"] != said["daemon_bytes"]:
            read["size_differs_from_daemon"] = True
        objects.append(read)
    return objects


async def _read_one(store: Any, name: str, cap: int) -> dict[str, Any]:
    info = await store.get_info(name)
    size = getattr(info, "size", None)
    if isinstance(size, int) and size > cap:
        return {
            "observed": False,
            "reason": f"{size} bytes, over DELIVERY_FULL_LOG_MAX_MB ({cap // MIB} MiB)",
        }
    hasher = _Hasher()
    out: dict[str, Any] = {}
    try:
        await store.get(name, writeinto=hasher)
    except Exception as exc:
        if type(exc).__name__ != "DigestMismatchError":
            raise
        # Every byte was read; the metadata's digest (written by the uploader)
        # disagrees with them. Ours is computed from the bytes either way.
        out["metadata_digest_mismatch"] = True
    return {
        "observed": True,
        "size": hasher.size,
        "sha256": hasher.digest.hexdigest(),
        **out,
    }
