"""
The daemon's identity, its protocol version and its heartbeat.

- ``workspace.status`` answers which machine this is, the daemon's version, the
  protocol it speaks (``protocol_version``, and ``protocol_min``, the oldest it
  still serves), its ``daemon_id``, whether it consumes the queued deliveries
  and its dependency cache.
- ``workspace.heartbeat`` is published every ``FABRIQUE_HEARTBEAT_SECONDS``
  (default 20, at most 25): the ``daemon_id``, a ``sequence`` that starts at 1
  when this process starts and grows by one per beat, also for a beat that could
  not be sent, and the protocol version.
- Both carry ``abilities``, the subjects and optional fields this daemon
  announces (``abilities.py``), so the backend need not guess them from
  replies.

Everything here is the daemon's report about itself, a claim. The backend
decides whether a daemon is online from when it observed a heartbeat, on its
own clock (``services/workspace/daemon_liveness.py``), and whether it can serve
it from the protocol range it supports. The wire is described in
``vscode-daemon/protocol/PROTOCOL.md``.
"""

import asyncio
import json
import logging
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import abilities
import sandbox_cache

logger = logging.getLogger("fabrique-daemon")

DAEMON_VERSION = "2026.10"
# The protocol this daemon speaks, and the oldest it still serves. A change a
# backend of the previous version cannot read raises PROTOCOL_VERSION.
PROTOCOL_VERSION = 1
PROTOCOL_MIN = 1
HEARTBEAT_SUBJECT = "workspace.heartbeat"
HEARTBEAT_DEFAULT_SECONDS = 20.0
HEARTBEAT_MAX_SECONDS = 25.0
# One per process: a restart shows as a new id whose sequence starts at 1.
DAEMON_ID = uuid.uuid4().hex[:16]


def heartbeat_seconds() -> float:
    """``FABRIQUE_HEARTBEAT_SECONDS``, between 1 and 25 (default 20)."""
    try:
        value = float(os.getenv("FABRIQUE_HEARTBEAT_SECONDS", "") or HEARTBEAT_DEFAULT_SECONDS)
    except ValueError:
        value = HEARTBEAT_DEFAULT_SECONDS
    return max(1.0, min(value, HEARTBEAT_MAX_SECONDS))


def host_info() -> Dict[str, Any]:
    """Which machine answered: the backend shows it next to host paths."""
    return {
        "hostname": platform.node() or "unknown",
        "platform": sys.platform,
        "home": str(Path.home()),
        "sep": os.sep,
    }


def status_body(jetstream: bool) -> Dict[str, Any]:
    """The reply to ``workspace.status``."""
    return {
        "host": host_info(),
        "version": DAEMON_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_min": PROTOCOL_MIN,
        "daemon_id": DAEMON_ID,
        "heartbeat_seconds": heartbeat_seconds(),
        "jetstream": bool(jetstream),
        "sandbox_cache": sandbox_cache.status(),
        "sandbox_environment": sandbox_cache.env_status(),
        "abilities": abilities.announcement(),
    }


def heartbeat_body(sequence: int, interval: float) -> Dict[str, Any]:
    """One heartbeat. ``sent_at`` is this machine's clock: the backend never uses it to judge liveness."""
    host = host_info()
    return {
        "daemon_id": DAEMON_ID,
        "sequence": sequence,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_min": PROTOCOL_MIN,
        "version": DAEMON_VERSION,
        "host": {"hostname": host["hostname"], "platform": host["platform"]},
        "interval_seconds": interval,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "abilities": abilities.announcement(),
    }


async def heartbeat_loop(nc, interval: Optional[float] = None, beats: Optional[int] = None,
                         sleep: Callable[[float], Any] = asyncio.sleep) -> None:
    """Publish a heartbeat now and every *interval* seconds (*beats* bounds it, for tests).

    A beat that cannot be sent (NATS reconnecting, a broker configuration that
    does not allow the subject yet) is logged once and skipped; its sequence
    number is still used, so the gap is visible to the backend.
    """
    seconds = interval or heartbeat_seconds()
    sequence = 0
    failing = False
    while beats is None or sequence < beats:
        sequence += 1
        try:
            await nc.publish(HEARTBEAT_SUBJECT, json.dumps(heartbeat_body(sequence, seconds)).encode())
            if failing:
                logger.info("Heartbeat sent again (sequence %d)", sequence)
            failing = False
        except Exception as exc:  # noqa: BLE001 - A missed beat must not stop the daemon.
            if not failing:
                logger.warning("Could not publish the heartbeat on %s (%s); retrying every %gs", HEARTBEAT_SUBJECT, exc, seconds)
            failing = True
        if beats is None or sequence < beats:
            await sleep(seconds)
