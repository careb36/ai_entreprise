"""A run's full log read back for a person, and checked against its record (#315).

``run_logs.observe`` recorded, per stream of an install, a test run or an
application check, the object it read back from the object store with the
size and sha256 the backend computed (``full_log: {observed, observed_by:
backend, bucket, objects: [{stream, object, size, sha256, ...}]}``). This
module finds those records in a verification's evidence and reads an object
again for the run log view (``routers/run_logs.py``):

- ``recorded_logs`` lists every full log a run's verifications recorded, in a
  stable order, each with an id (``<verification id>-<ordinal>``), where it
  sits in the evidence and the recorded size and sha256. Nothing is read.
- ``read`` reads one object into a spool (in memory up to a MiB, then on disk)
  and computes its size and sha256 over exactly those bytes; the route then
  serves the spool, so the hash describes the bytes the person receives.
- ``integrity`` compares that hash with the recorded one: ``matched`` or
  ``mismatched``. An object the store no longer holds is ``missing``
  (``LogMissing``); a store that could not be reached is ``unavailable``
  (``LogUnavailable``): unknown is not absent (rule 7 of
  ``docs/POSITIONING.md``).

- ``parse_range`` and ``read_range`` serve one byte range (``Range: bytes=a-b``
  or ``?offset=&limit=``). JetStream's object store has no byte-range read, so
  the object's chunks are listed headers only (an ordered consumer with
  ``headers_only``, which carries each chunk's size and stream sequence) and
  only the chunks the range overlaps are fetched by sequence, into a spool of
  the range's size: neither memory nor transfer follows the log's length. A
  piece is never hashed against the record: it is ``unverified`` with the
  reason ``partial``, and only the full read can be ``matched``.

No hash is taken from the daemon, the client or the object store's metadata
(rule 6): the recorded sha256 is the one the backend computed when it first
read the object, and the observed one is computed here. Only the backend's own
bucket is read, and only names of the shape ``run_logs.request_spec`` gives
(``run-<32 hex>/<stream>``), whatever the record says.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO, Any

from config import settings

MIB = 1024 * 1024
# The spool stays in memory up to this size, then moves to a temporary file.
SPOOL_MEMORY_BYTES = MIB
# How long reading one object back may take before it is given up.
READ_SECONDS = 120.0
CHUNK_BYTES = 64 * 1024
# How deep the evidence is searched for ``full_log`` records.
MAX_DEPTH = 6

_OBJECT_NAME = re.compile(r"^run-[0-9a-f]{32}/[A-Za-z0-9_.-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NOT_FOUND = {"ObjectNotFoundError", "NotFoundError", "ObjectDeletedError"}

MATCHED = "matched"
MISMATCHED = "mismatched"
MISSING = "missing"
UNAVAILABLE = "unavailable"
# A piece of a log: it cannot be checked against the whole log's sha256.
UNVERIFIED = "unverified"
PARTIAL = "partial"

_RANGE = re.compile(r"^bytes=(\d{0,18})-(\d{0,18})$")


class LogMissing(Exception):
    """The object store no longer holds the object (expired or dropped)."""


class LogUnavailable(Exception):
    """The object could not be read: no store, an error, a timeout or its size."""


class LogChanged(Exception):
    """The store holds a size other than the recorded one: a range of it
    would not be a range of the log that was recorded."""


class RangeNotSatisfiable(Exception):
    """The range starts at or past the end of the log (HTTP 416)."""


@dataclass
class ReadLog:
    """The bytes read back, spooled, with the size and sha256 computed here."""

    spool: IO[bytes]
    size: int
    sha256: str

    def chunks(self) -> Iterator[bytes]:
        """The spooled bytes from the start, in chunks; closes the spool."""
        return _drain(self.spool)


@dataclass
class ReadPiece:
    """Bytes ``start``..``end`` (inclusive) of a log of ``total`` bytes, spooled."""

    spool: IO[bytes]
    start: int
    end: int
    total: int

    def chunks(self) -> Iterator[bytes]:
        return _drain(self.spool)


def _drain(spool: IO[bytes]) -> Iterator[bytes]:
    try:
        spool.seek(0)
        while chunk := spool.read(CHUNK_BYTES):
            yield chunk
    finally:
        spool.close()


class _HashingSpool:
    """Where the object store writes an object's bytes: hashed, counted and
    spooled, so the bytes served are the bytes hashed."""

    def __init__(self, cap: int) -> None:
        # Outlives this call: ReadLog.chunks closes it once served.
        self.spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES)  # noqa: SIM115
        self.digest = hashlib.sha256()
        self.size = 0
        self.cap = cap

    def write(self, data: bytes) -> int:
        self.size += len(data)
        if self.size > self.cap:
            raise LogUnavailable(
                f"over DELIVERY_FULL_LOG_MAX_MB ({self.cap // MIB} MiB)"
            )
        self.digest.update(data)
        self.spool.write(data)
        return len(data)


def _servable(full_log: dict[str, Any], obj: dict[str, Any]) -> str | None:
    """Why the object cannot be opened, or ``None`` when it can."""
    if not full_log.get("observed") or full_log.get("observed_by") != "backend":
        return full_log.get("reason") or "the full log was not observed"
    if not obj.get("observed"):
        return obj.get("reason") or "the object was not read back"
    if not isinstance(obj.get("sha256"), str) or not _SHA256.match(obj["sha256"]):
        return "no sha256 was recorded"
    if not isinstance(obj.get("object"), str) or not _OBJECT_NAME.match(obj["object"]):
        return "not an object name the backend asks for"
    return None


def _full_logs(node: Any, path: str, depth: int) -> Iterator[tuple[str, dict]]:
    """Every ``full_log`` record under *node*, with where it sits, in a stable
    order (keys sorted, lists in order)."""
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        for key in sorted(node, key=str):
            value = node[key]
            here = f"{path}.{key}" if path else str(key)
            if key == "full_log" and isinstance(value, dict):
                yield path or "evidence", value
            else:
                yield from _full_logs(value, here, depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _full_logs(value, f"{path}.{index}", depth + 1)


def logs_of(verification_id: int, evidence: Any) -> list[dict[str, Any]]:
    """The full logs one verification's evidence recorded, numbered in order."""
    entries: list[dict[str, Any]] = []
    for source, full_log in _full_logs(evidence or {}, "", 0):
        objects = full_log.get("objects")
        if not isinstance(objects, list) or not objects:
            entries.append(
                {
                    "source": source,
                    "stream": None,
                    "object": None,
                    "openable": False,
                    "reason": full_log.get("reason") or "no object was recorded",
                }
            )
            continue
        for obj in objects:
            obj = obj if isinstance(obj, dict) else {}
            why = _servable(full_log, obj)
            entries.append(
                {
                    "source": source,
                    "stream": obj.get("stream"),
                    "object": obj.get("object"),
                    "recorded_size": obj.get("size"),
                    "recorded_sha256": obj.get("sha256") if why is None else None,
                    "truncated_by_daemon": bool(obj.get("truncated")),
                    "openable": why is None,
                    **({"reason": why} if why else {}),
                }
            )
    for ordinal, entry in enumerate(entries):
        entry["id"] = f"{verification_id}-{ordinal}"
        entry["verification_id"] = verification_id
    return entries


def recorded_logs(verifications: list[Any]) -> list[dict[str, Any]]:
    """Every full log a run's verifications recorded, oldest verification first."""
    logs: list[dict[str, Any]] = []
    for verification in verifications:
        for entry in logs_of(verification.id, verification.evidence):
            observed_at = getattr(verification, "observed_at", None)
            entry["observed_at"] = observed_at.isoformat() if observed_at else None
            logs.append(entry)
    return logs


def parse_id(log_id: str) -> tuple[int, int] | None:
    """``(verification id, ordinal)`` from ``<verification id>-<ordinal>``."""
    match = re.fullmatch(r"(\d{1,18})-(\d{1,6})", log_id or "")
    return (int(match[1]), int(match[2])) if match else None


def integrity(recorded_sha256: str, observed_sha256: str) -> str:
    """``matched`` or ``mismatched``: the recorded hash against the bytes served."""
    return MATCHED if recorded_sha256 == observed_sha256 else MISMATCHED


def parse_range(
    header: str | None, offset: int | None, limit: int | None, total: int
) -> tuple[int, int] | None:
    """The inclusive ``(start, end)`` asked for, or ``None`` for the whole log.

    ``offset``/``limit`` (the frontend's form) or one ``Range: bytes=a-b``,
    ``bytes=a-`` or ``bytes=-n`` against the recorded size *total*. A header
    that is not one byte range is ignored (RFC 9110 §14.2), so the answer is
    the full read; a range that covers the whole log is the full read too, so
    it is verified. ``RangeNotSatisfiable`` when it starts at or past the end."""
    if offset is not None or limit is not None:
        start = offset or 0
        end = total - 1 if limit is None else min(start + limit, total) - 1
    else:
        match = _RANGE.match((header or "").strip())
        if not match or not (match[1] or match[2]):
            return None
        if not match[1]:
            suffix = int(match[2])
            if suffix == 0:
                raise RangeNotSatisfiable("an empty suffix")
            start, end = max(total - suffix, 0), total - 1
        else:
            start = int(match[1])
            if match[2] and int(match[2]) < start:
                return None  # not a valid range: ignored
            end = min(int(match[2]), total - 1) if match[2] else total - 1
    if start >= total:
        raise RangeNotSatisfiable(f"the log has {total} bytes")
    if start == 0 and end == total - 1:
        return None
    return start, end


def _missing(exc: BaseException) -> bool:
    return type(exc).__name__ in _NOT_FOUND


async def read(nats_client: Any, name: str) -> ReadLog:
    """Read *name* from the backend's own full-log bucket into a spool, hashed.

    Raises ``LogMissing`` when the store no longer holds it and
    ``LogUnavailable`` when it cannot be read (no store, an error, a timeout,
    over the cap)."""
    js, bucket = _store_of(nats_client, name)
    cap = max(int(settings.DELIVERY_FULL_LOG_MAX_MB or 0), 1) * MIB
    try:
        return await asyncio.wait_for(_read(js, bucket, name, cap), READ_SECONDS)
    except (LogMissing, LogUnavailable):
        raise
    except TimeoutError as exc:
        raise LogUnavailable(f"reading it took over {READ_SECONDS:.0f} s") from exc
    except Exception as exc:
        if _missing(exc):
            raise LogMissing("the object store no longer holds it") from exc
        raise LogUnavailable(f"{type(exc).__name__}: {exc}"[:300]) from exc


def _store_of(nats_client: Any, name: str) -> tuple[Any, str]:
    if not _OBJECT_NAME.match(name or ""):
        raise LogUnavailable("not an object name the backend asks for")
    bucket = getattr(nats_client, "run_logs_bucket", None)
    js = getattr(nats_client, "js", None)
    if not isinstance(bucket, str) or js is None:
        raise LogUnavailable(
            "no object store for full logs (JetStream is not enabled, or the "
            "bucket could not be created)"
        )
    return js, bucket


async def read_range(
    nats_client: Any, name: str, total: int, start: int, end: int
) -> ReadPiece:
    """Bytes *start*..*end* of *name*, whose recorded size is *total*.

    Raises ``LogMissing``, ``LogUnavailable`` as ``read`` does, and
    ``LogChanged`` when the store's size is not the recorded one."""
    js, bucket = _store_of(nats_client, name)
    try:
        return await asyncio.wait_for(
            _read_range(js, bucket, name, total, start, end), READ_SECONDS
        )
    except (LogMissing, LogUnavailable, LogChanged):
        raise
    except TimeoutError as exc:
        raise LogUnavailable(f"reading it took over {READ_SECONDS:.0f} s") from exc
    except Exception as exc:
        if _missing(exc):
            raise LogMissing("the object store no longer holds it") from exc
        raise LogUnavailable(f"{type(exc).__name__}: {exc}"[:300]) from exc


async def _read_range(
    js: Any, bucket: str, name: str, total: int, start: int, end: int
) -> ReadPiece:
    store = await js.object_store(bucket)
    info = await store.get_info(name)
    if getattr(info, "deleted", False):
        raise LogMissing("the object store no longer holds it")
    if getattr(info, "size", None) != total:
        raise LogChanged(
            f"the store holds {getattr(info, 'size', None)} bytes, the record "
            f"{total}; read the whole log to check it"
        )
    nuid = getattr(info, "nuid", None)
    if not isinstance(nuid, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", nuid):
        raise LogUnavailable("the object has no chunk subject")
    # Listing the chunks: headers only, so no chunk's bytes travel here.
    sub = await js.subscribe(
        f"$O.{bucket}.C.{nuid}", ordered_consumer=True, headers_only=True
    )
    # Outlives this call: ReadPiece.chunks closes it once served.
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES)  # noqa: SIM115
    try:
        at = 0
        async for msg in sub.messages:
            size = int((msg.headers or {}).get("Nats-Msg-Size", -1))
            if size < 0:
                raise LogUnavailable("a chunk was listed without its size")
            if at + size > start and at <= end:
                seq = msg.metadata.sequence.stream
                raw = await js.get_msg(f"OBJ_{bucket}", seq=seq)
                data = raw.data or b""
                if len(data) != size:
                    raise LogUnavailable(
                        f"chunk {seq} has {len(data)} bytes, listed as {size}"
                    )
                spool.write(data[max(start - at, 0) : end - at + 1])
            at += size
            if at > end or msg.metadata.num_pending == 0:
                break
        if spool.tell() != end - start + 1:
            raise LogUnavailable("the object ended before the range")
    except BaseException:
        spool.close()
        raise
    finally:
        await sub.unsubscribe()
    return ReadPiece(spool=spool, start=start, end=end, total=total)


async def _read(js: Any, bucket: str, name: str, cap: int) -> ReadLog:
    store = await js.object_store(bucket)
    info = await store.get_info(name)
    if getattr(info, "deleted", False):
        raise LogMissing("the object store no longer holds it")
    size = getattr(info, "size", None)
    if isinstance(size, int) and size > cap:
        raise LogUnavailable(
            f"{size} bytes, over DELIVERY_FULL_LOG_MAX_MB ({cap // MIB} MiB)"
        )
    sink = _HashingSpool(cap)
    try:
        await store.get(name, writeinto=sink)
    except Exception as exc:
        # Every byte was read; the uploader's metadata digest disagrees with
        # them. Ours is computed from the bytes either way.
        if type(exc).__name__ != "DigestMismatchError":
            sink.spool.close()
            raise
    return ReadLog(spool=sink.spool, size=sink.size, sha256=sink.digest.hexdigest())
