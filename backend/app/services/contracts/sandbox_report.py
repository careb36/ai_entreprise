"""The sandbox the daemon reports it applied to a test run, read as a check.

``check_sandbox`` reads ``evidence["test_run"]["sandbox"]``, the daemon's
observation, and writes the Verifier's ``sandbox`` record (ADR 0013): a pure
function of the reply's shape, so the evidence reads it without importing the
workspace context. ``services/sandbox.py`` (workspace) re-exports it.

An isolated run's record also carries ``image_identity``: the image's content
ID and registry digest as the daemon observed them before (or, for an image
the run pulled, after) the run (``vscode-daemon/sandbox_image.py``), or why
there is none. ``cache_text`` says what the dependency cache did
(``vscode-daemon/sandbox_cache.py``); ``app_check`` uses it for the install.
"""

from __future__ import annotations

import re
from typing import Any

_CONTENT_ID = re.compile(r"sha256:[0-9a-f]{64}")


def check_sandbox(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's ``sandbox`` check: where the tests ran, as the daemon
    reported it. A record, never the verdict; absent without a test run."""
    run = (evidence or {}).get("test_run") or {}
    if not run.get("command"):
        return None
    applied = run.get("sandbox")
    if (
        run.get("exit_code") is None
        and not run.get("timed_out")
        and not isinstance(applied, dict)
    ):
        return None  # Nothing ran: tests_run says why, there is nowhere to report.
    if not isinstance(applied, dict):
        return {
            "name": "sandbox",
            "outcome": "pass",
            "isolated": None,
            "finding": True,
            "observation": (
                "the daemon reported no sandbox, so where the tests ran and "
                "whether they were isolated is unknown"
            ),
        }
    mode = applied.get("mode")
    forced = " (the daemon's own floor)" if applied.get("forced_by_daemon") else ""
    if mode == "host" or not applied.get("isolated"):
        return {
            "name": "sandbox",
            "outcome": "pass",
            "isolated": False,
            "finding": True,
            "sandbox": applied,
            "observation": (
                "ran on the host, unisolated: the agent-written code had the "
                "daemon's user, files, network and environment"
            ),
        }
    parts = [f"ran in {mode}{forced}"]
    if mode == "container":
        parts.append(
            f"{applied.get('runtime') or 'container'} image {applied.get('image')}"
            f" ({image_text(applied)})"
        )
    parts.append(f"network {applied.get('network')}")
    if applied.get("cpus"):
        parts.append(f"{applied['cpus']:g} CPUs")
    if applied.get("memory_mb"):
        parts.append(f"{applied['memory_mb']} MB")
    if applied.get("pids_limit"):
        parts.append(f"{applied['pids_limit']} processes")
    if applied.get("capabilities"):
        parts.append(f"capabilities {applied['capabilities']}")
    if applied.get("limits"):
        parts.append(str(applied["limits"]))
    env = applied.get("env") or []
    parts.append(f"environment: {', '.join(env)}" if env else "no environment passed")
    cache = cache_text(applied.get("cache"))
    if cache:
        parts.append(cache)
    out = {
        "name": "sandbox",
        "outcome": "pass",
        "isolated": True,
        "finding": False,
        "sandbox": applied,
        "observation": "; ".join(parts),
    }
    # bwrap runs no image: the record carries the daemon's reason.
    out["image_identity"] = image_identity(applied)
    return out


def image_identity(applied: dict[str, Any] | None) -> dict[str, Any]:
    """The image the daemon reported it ran, as recorded: ``id`` only when it
    is a ``sha256:`` content ID, else ``None`` with the reason (the daemon's,
    or that it reported none). The daemon observed it; nothing here is taken
    from a producer."""
    seen = (applied or {}).get("image_identity")
    if not isinstance(seen, dict):
        return {
            "id": None,
            "digest": None,
            "reason": "the daemon reported no image identity",
        }
    image_id = seen.get("id")
    if not (isinstance(image_id, str) and _CONTENT_ID.fullmatch(image_id)):
        reason = seen.get("reason") if isinstance(seen.get("reason"), str) else None
        return {
            "id": None,
            "digest": None,
            "reason": reason or "the daemon reported no content ID for the image",
        }
    digest = seen.get("digest")
    out: dict[str, Any] = {
        "id": image_id,
        "digest": digest if isinstance(digest, str) and "@sha256:" in digest else None,
        "observed_by": "daemon",
        "observed": seen.get("observed"),
    }
    if out["digest"] is None:
        out["digest_reason"] = (
            seen.get("digest_reason") or "no registry digest reported"
        )
    return out


def image_text(applied: dict[str, Any] | None) -> str:
    """One phrase for an observation: the image's content ID and digest, or why unknown."""
    seen = image_identity(applied)
    if not seen["id"]:
        return f"image identity unknown: {seen['reason']}"
    digest = seen["digest"] or f"no registry digest: {seen['digest_reason']}"
    when = (
        " (observed after the run pulled it)"
        if seen.get("observed") == "after_run"
        else ""
    )
    return f"content {seen['id']}, {digest}{when}"


def cache_text(cache: Any) -> str | None:
    """One phrase on the dependency cache the daemon reported, or ``None``."""
    if not isinstance(cache, dict) or not cache.get("state"):
        return None
    state, volume = cache["state"], cache.get("volume") or "the cache volume"
    if cache.get("mounted"):
        text = f"dependency cache {volume} {state}"
    else:
        text = f"dependency cache not mounted ({state}: {cache.get('reason') or 'no reason given'})"
    after = cache.get("after")
    if after == "removed":
        text += "; reset (removed) after the failed install"
    elif after == "not removed":
        reason = (cache.get("reset") or {}).get("reason") or "no reason given"
        text += f"; it could not be reset after the failed install: {reason}"
    quarantine = cache.get("quarantined")
    if isinstance(quarantine, dict) and state != "quarantined":
        text += f"; now quarantined: {quarantine.get('reason')}"
    return text
