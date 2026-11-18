"""What the workspace daemons announce they can do, and what the backend makes of it (#315).

A daemon sends ``abilities`` in ``workspace.status`` and in every heartbeat
(``vscode-daemon/abilities.py``, ``PROTOCOL.md`` §2.1): the request subjects it
serves and, per subject, the optional request fields it honours. Before it,
the backend guessed: a subject nobody answered was read as "an older daemon",
a reply without ``full_log`` as "a daemon from before full logs".

The announcement is the daemon's **claim** about itself. ``parse`` bounds it
and ``daemon_liveness`` keeps it under the daemon's ``reported`` fields, shown
as ``reported_by_daemon``, never beside what the backend observed. It decides
only whether a request is worth sending and how a missing field is worded; a
run's evidence is still read from that run's reply.

``announces`` answers ``True``, ``False`` or ``None``. ``None`` (no daemon heard
recently, one of them announced nothing, or they disagree) means "not
announced": the caller keeps today's fallback, so an older daemon is served
exactly as before.
"""

from __future__ import annotations

from typing import Any

# The announcement shapes this backend reads.
SUPPORTED_ABILITIES_VERSION = 1
SOURCE = "announced by the daemon (workspace.status / workspace.heartbeat)"
_MAX_SUBJECTS = 64
_MAX_FEATURES = 32
_MAX_NAME = 64


def _names(value: Any, limit: int) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names = [v for v in value if isinstance(v, str) and 0 < len(v) <= _MAX_NAME]
    return sorted(set(names))[:limit]


def parse(value: Any) -> dict[str, Any] | None:
    """The announcement, bounded, or None when there is none this backend can read.

    A version above ``SUPPORTED_ABILITIES_VERSION`` is unreadable here and
    treated as no announcement: the fallback, never a guess at its meaning."""
    if not isinstance(value, dict):
        return None
    version = value.get("abilities_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not 1 <= version <= SUPPORTED_ABILITIES_VERSION
    ):
        return None
    subjects = _names(value.get("subjects"), _MAX_SUBJECTS)
    if subjects is None:
        return None
    raw = value.get("features") if isinstance(value.get("features"), dict) else {}
    features: dict[str, list[str]] = {}
    for subject in sorted(k for k in raw if isinstance(k, str))[:_MAX_SUBJECTS]:
        names = _names(raw[subject], _MAX_FEATURES)
        if names is not None and len(subject) <= _MAX_NAME:
            features[subject] = names
    return {"abilities_version": version, "subjects": subjects, "features": features}


def _has(announcement: dict[str, Any], subject: str, feature: str | None) -> bool:
    if subject not in announcement["subjects"]:
        return False
    return feature is None or feature in announcement["features"].get(subject, [])


def announces(
    announcements: list[dict[str, Any] | None], subject: str, feature: str | None = None
) -> bool | None:
    """Whether every daemon in *announcements* announces *subject* (and *feature*).

    ``True`` when all of them do, ``False`` when none does, ``None`` when there
    are none, one announced nothing or they disagree (a request may reach
    any of them: the queue group picks)."""
    if not announcements or any(a is None for a in announcements):
        return None
    verdicts = {_has(a, subject, feature) for a in announcements if a is not None}
    return verdicts.pop() if len(verdicts) == 1 else None


def not_announced_reason(subject: str, feature: str | None = None) -> str:
    """The reason recorded when a daemon announces it cannot serve a request."""
    what = f"{feature} on {subject}" if feature else subject
    return f"the workspace daemon does not announce {what} ({SOURCE})"
