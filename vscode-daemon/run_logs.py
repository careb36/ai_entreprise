"""The whole output of a run, uploaded to a JetStream object store (issue #315).

The replies keep a bounded tail (``sandbox.MAX_OUTPUT_BYTES`` while a command
runs, a few thousand characters in an application check's record). When the
backend asks for it with ``full_log: {"bucket": ..., "name": ...}`` on
``workspace.execute`` or ``workspace.app.check``, the daemon also keeps every
byte of each stream, up to ``FABRIQUE_FULL_LOG_MAX_MB`` (50 MiB by default),
in a temporary file while the command runs, and uploads each stream after it
ended as the object ``<name>/<stream>`` in the bucket. The reply's
``full_log`` says which objects it wrote, how many bytes each holds, how many
the stream printed in all and whether the object was cut at the cap
(``truncated``), or ``error`` when the upload failed.

It never reports a hash: the backend reads each object back and computes the
sha256 itself (``backend/app/services/workspace/run_logs.py``; rule 6 of
``docs/POSITIONING.md``). The broker lets the daemon's user write to that one
bucket and read an object's metadata, never purge or delete
(``scripts/nats_config.py``). A daemon without this module ignores the field,
and the backend records the full log as not observed.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any

logger = logging.getLogger("fabrique-daemon.run_logs")

MIB = 1024 * 1024
DEFAULT_MAX_MB = 50
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Internal key of a result: the streams kept for upload, removed before the reply.
KEY = "_full_logs"


def max_bytes() -> int:
    """The cap per stream: ``FABRIQUE_FULL_LOG_MAX_MB`` (1 to 1024), else 50 MiB."""
    try:
        megabytes = int(os.getenv("FABRIQUE_FULL_LOG_MAX_MB", str(DEFAULT_MAX_MB)))
    except ValueError:
        megabytes = DEFAULT_MAX_MB
    return min(max(megabytes, 1), 1024) * MIB


class Spool:
    """Every byte of one stream up to the cap, in a temporary file; ``total``
    counts what the stream printed in all."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = max_bytes() if limit is None else limit
        self.file = tempfile.TemporaryFile()
        self.kept = 0
        self.total = 0

    def write(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self.limit - self.kept
        if room > 0:
            part = chunk[:room]
            self.file.write(part)
            self.kept += len(part)

    @property
    def truncated(self) -> bool:
        return self.total > self.kept

    def close(self) -> None:
        try:
            self.file.close()
        except OSError:
            pass


def spool_of(data: Any) -> Spool:
    """A spool holding *data* (bytes or text) as the command printed it."""
    spool = Spool()
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    if data:
        spool.write(bytes(data))
    return spool


def spool_of_file(handle: Any) -> Spool:
    """A spool holding a whole file (an application's log), read from the start."""
    spool = Spool()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(65536), b""):
        spool.write(chunk)
    return spool


def wanted(request: Any) -> dict[str, str] | None:
    """The request's ``full_log`` when it names a bucket and a run name this
    daemon accepts (letters, digits, ``_`` and ``-``), else ``None``."""
    spec = request.get("full_log") if isinstance(request, dict) else None
    if not isinstance(spec, dict):
        return None
    bucket, name = spec.get("bucket"), spec.get("name")
    if not (isinstance(bucket, str) and _SAFE.match(bucket) and isinstance(name, str) and _SAFE.match(name)):
        return None
    return {"bucket": bucket, "name": name}


def keep(result: dict[str, Any], stream: str, spool: Spool | None) -> None:
    """Add *spool* to the streams *result* uploads as ``<name>/<stream>``."""
    if spool is not None:
        result.setdefault(KEY, {})[stream] = spool


def discard(result: dict[str, Any]) -> None:
    for spool in (result.pop(KEY, None) or {}).values():
        spool.close()


async def upload(nc: Any, spec: dict[str, str] | None, result: dict[str, Any]) -> None:
    """Upload the kept streams of *result* and say so in ``result["full_log"]``;
    the internal key is always removed, whatever happens."""
    spools: dict[str, Spool] = result.pop(KEY, None) or {}
    if spec is None:
        for spool in spools.values():
            spool.close()
        return
    out: dict[str, Any] = {"bucket": spec["bucket"], "max_bytes": max_bytes(), "objects": []}
    if not spools:
        out["reason"] = "the run printed nothing the daemon kept (it did not run)"
        result["full_log"] = out
        return
    try:
        store = await nc.jetstream().object_store(spec["bucket"])
        for stream, spool in spools.items():
            name = f"{spec['name']}/{stream}"
            spool.file.seek(0)
            await store.put(name, spool.file)
            out["objects"].append({
                "stream": stream,
                "object": name,
                "bytes": spool.kept,
                "total_bytes": spool.total,
                "truncated": spool.truncated,
            })
    except Exception as exc:  # noqa: BLE001 - No store, no permission, a full bucket: said in the reply.
        logger.warning("Full log %s not uploaded: %s", spec["name"], exc)
        out["error"] = f"{type(exc).__name__}: {exc}"[:300] or type(exc).__name__
    finally:
        for spool in spools.values():
            spool.close()
    result["full_log"] = out
