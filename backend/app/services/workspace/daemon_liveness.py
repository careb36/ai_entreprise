"""
What the backend knows about the workspace daemon between requests.

Two things, kept apart as the positioning asks (a claim and an observation
never share a field):

- **The protocol.** The daemon reports the protocol version it speaks
  (``protocol_version``) and the oldest it still serves (``protocol_min``), in
  ``workspace.status`` and in every heartbeat (``vscode-daemon/protocol/
  PROTOCOL.md``). The backend serves a daemon whose range overlaps
  ``SUPPORTED_PROTOCOL``; one outside it is *incompatible*, with a reason that
  says which side to update, never a generic timeout. A daemon that reports no
  version is an older daemon: ``unknown``, and served as before.
- **Liveness.** The daemon publishes ``workspace.heartbeat`` every <=25 s. The
  backend records when it *observed* each one, on its own clock (``observed_at``,
  a monotonic age), never the daemon's ``sent_at``; a daemon is ``online`` up to
  ``DAEMON_HEARTBEAT_STALE_SECONDS`` after its last heartbeat, ``stale`` up to
  ``DAEMON_HEARTBEAT_OFFLINE_SECONDS`` and ``offline`` after. Without any
  heartbeat (an older daemon, or none yet since this process started) the
  request-based check (``workspace_daemon.daemon_presence``) is the fallback.

Everything the daemon says about itself is kept under ``reported_by_daemon``.
The state is this process's memory: each backend replica observes the
heartbeats itself (a plain subscription, not a queue group).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

HEARTBEAT_SUBJECT = "workspace.heartbeat"
# The protocol versions this backend reads and writes (inclusive).
SUPPORTED_PROTOCOL = (1, 1)
OLDER_DAEMON_REASON = "unknown protocol version (older daemon)"
MAX_DAEMONS = 16
_TEXT_LIMIT = 200

# daemon_id -> what was observed of it, and what it reported.
_daemons: dict[str, dict[str, Any]] = {}


def reset() -> None:
    """Forget every observation (tests, and a reconnect that wants a clean slate)."""
    _daemons.clear()


def _version(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _text(value: Any) -> str | None:
    return value[:_TEXT_LIMIT] if isinstance(value, str) else None


def _reported(payload: dict[str, Any]) -> dict[str, Any]:
    """The fields the daemon states about itself, bounded: a claim."""
    host = payload.get("host") if isinstance(payload.get("host"), dict) else {}
    reported: dict[str, Any] = {
        "protocol_version": payload.get("protocol_version"),
        "protocol_min": payload.get("protocol_min"),
        "version": _text(payload.get("version")),
        "host": {
            "hostname": _text(host.get("hostname")),
            "platform": _text(host.get("platform")),
        },
    }
    for key in ("sequence", "interval_seconds", "heartbeat_seconds"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            reported[key] = value
    if _text(payload.get("sent_at")):
        reported["sent_at"] = _text(payload.get("sent_at"))
    return reported


def compatibility(reported: dict[str, Any] | None) -> dict[str, Any]:
    """Whether a daemon that reports *reported* can be served.

    ``state`` is ``compatible`` (with the ``negotiated`` version: the highest
    both speak), ``incompatible`` (with the reason, naming the side to
    update) or ``unknown`` (no version reported: an older daemon, served).
    """
    low_ok, high_ok = SUPPORTED_PROTOCOL
    supports = {"min": low_ok, "max": high_ok}
    raw = (reported or {}).get("protocol_version")
    if raw is None:
        return {
            "state": "unknown",
            "reason": OLDER_DAEMON_REASON,
            "negotiated": None,
            "backend_supports": supports,
        }
    high = _version(raw)
    if high is None:
        return {
            "state": "incompatible",
            "reason": f"the daemon reports protocol version {raw!r}, which is not a version number",
            "negotiated": None,
            "backend_supports": supports,
        }
    low = min(_version((reported or {}).get("protocol_min")) or high, high)
    if max(low, low_ok) > min(high, high_ok):
        speaks = f"protocol {low}" if low == high else f"protocols {low} to {high}"
        accepts = f"{low_ok}" if low_ok == high_ok else f"{low_ok} to {high_ok}"
        update = (
            "update the backend"
            if low > high_ok
            else "update the daemon (restart it from this checkout: scripts/start-daemon.sh)"
        )
        return {
            "state": "incompatible",
            "reason": f"the workspace daemon speaks {speaks}; this backend supports {accepts}: {update}",
            "negotiated": None,
            "backend_supports": supports,
        }
    return {
        "state": "compatible",
        "reason": None,
        "negotiated": min(high, high_ok),
        "backend_supports": supports,
    }


def _entry(daemon_id: str) -> dict[str, Any]:
    entry = _daemons.get(daemon_id)
    if entry is None:
        if len(_daemons) >= MAX_DAEMONS:
            oldest = min(_daemons, key=lambda k: _daemons[k]["observed_monotonic"])
            _daemons.pop(oldest)
        entry = {"daemon_id": daemon_id, "heartbeats_observed": 0, "missed": 0}
        _daemons[daemon_id] = entry
    return entry


def _daemon_id(payload: dict[str, Any]) -> str:
    return _text(payload.get("daemon_id")) or "unknown"


def observe_heartbeat(
    payload: Any, now: float | None = None, wall: datetime | None = None
) -> dict[str, Any] | None:
    """Record a heartbeat as observed now; returns the daemon's entry, or None for a malformed one."""
    if not isinstance(payload, dict):
        return None
    entry = _entry(_daemon_id(payload))
    sequence = payload.get("sequence")
    previous = entry.get("sequence")
    if (
        isinstance(sequence, int)
        and isinstance(previous, int)
        and sequence > previous + 1
    ):
        # Beats this backend did not observe: the daemon could not send them,
        # or they were lost on the way.
        entry["missed"] += sequence - previous - 1
    entry.update(
        {
            "sequence": sequence if isinstance(sequence, int) else None,
            "heartbeats_observed": entry["heartbeats_observed"] + 1,
            "heartbeat_monotonic": time.monotonic() if now is None else now,
            "heartbeat_observed_at": (wall or datetime.now(UTC)).isoformat(),
            "observed_monotonic": time.monotonic() if now is None else now,
            "reported": _reported(payload),
        }
    )
    return entry


def observe_status(
    status: Any, now: float | None = None, wall: datetime | None = None
) -> None:
    """Record what a ``workspace.status`` reply reported (an older daemon's is keyed ``unknown``)."""
    if not isinstance(status, dict):
        return
    entry = _entry(_daemon_id(status))
    entry.update(
        {
            "status_observed_at": (wall or datetime.now(UTC)).isoformat(),
            "observed_monotonic": time.monotonic() if now is None else now,
            "reported": {**entry.get("reported", {}), **_reported(status)},
        }
    )


def _heartbeat_state(age: float | None) -> str:
    if age is None:
        return "not_observed"
    if age <= settings.DAEMON_HEARTBEAT_STALE_SECONDS:
        return "online"
    if age <= settings.DAEMON_HEARTBEAT_OFFLINE_SECONDS:
        return "stale"
    return "offline"


_RANK = {"online": 3, "stale": 2, "offline": 1, "not_observed": 0}


def liveness(now: float | None = None) -> dict[str, Any]:
    """The heartbeat-based state: the best of every daemon heard, with each one's detail."""
    clock = time.monotonic() if now is None else now
    daemons = []
    for entry in _daemons.values():
        seen = entry.get("heartbeat_monotonic")
        age = None if seen is None else max(0.0, clock - seen)
        if age is None:
            continue
        daemons.append(
            {
                "daemon_id": entry["daemon_id"],
                "state": _heartbeat_state(age),
                "last_observed_at": entry.get("heartbeat_observed_at"),
                "seconds_since_last": round(age, 1),
                "heartbeats_observed": entry["heartbeats_observed"],
                "missed": entry["missed"],
                "reported_by_daemon": entry.get("reported", {}),
            }
        )
    daemons.sort(key=lambda d: (-_RANK[d["state"]], d["seconds_since_last"]))
    best = daemons[0] if daemons else None
    state = best["state"] if best else "not_observed"
    reasons = {
        "online": None,
        "stale": "no heartbeat observed for {age:g} s",
        "offline": "no heartbeat observed for {age:g} s",
        "not_observed": "no heartbeat observed since the backend started",
    }
    reason = reasons[state]
    if reason and best:
        reason = reason.format(age=best["seconds_since_last"])
    return {
        "state": state,
        "reason": reason,
        "last_observed_at": best["last_observed_at"] if best else None,
        "seconds_since_last": best["seconds_since_last"] if best else None,
        "stale_after_seconds": settings.DAEMON_HEARTBEAT_STALE_SECONDS,
        "offline_after_seconds": settings.DAEMON_HEARTBEAT_OFFLINE_SECONDS,
        "observed_by": "backend",
        "daemons": daemons,
    }


def incompatibility(now: float | None = None) -> str | None:
    """Why the daemons heard recently cannot be served, or None.

    Only when every daemon observed within the offline window reports a
    protocol this backend does not speak; one compatible or older daemon
    among them is enough to keep serving.
    """
    clock = time.monotonic() if now is None else now
    recent = [
        entry
        for entry in _daemons.values()
        if clock - entry.get("observed_monotonic", clock)
        <= settings.DAEMON_HEARTBEAT_OFFLINE_SECONDS
    ]
    if not recent:
        return None
    verdicts = [
        (entry["observed_monotonic"], compatibility(entry.get("reported")))
        for entry in recent
    ]
    if any(v["state"] != "incompatible" for _, v in verdicts):
        return None
    return max(verdicts, key=lambda item: item[0])[1]["reason"]


async def listen(nats_client: Any) -> None:
    """Observe the daemons' heartbeats. Nothing is ever sent back: a heartbeat is not a request."""

    async def on_heartbeat(_msg: Any, data: Any = None) -> None:
        observe_heartbeat(data)

    await nats_client.subscribe(HEARTBEAT_SUBJECT, on_heartbeat)
