"""Sandbox as verification: the mode the daemon used to run agent-written code.

The daemon records ``evidence["sandbox"]`` with the mode it *reports* using (``host``,
``container`` or ``bwrap``) and the limits applied. The Verifier turns that
into a check: ``host`` is a finding a person sees (it runs on their machine),
never a refutation on its own; ``container``/``bwrap`` pass. The evidence
keeps the last word; nothing here decides a verdict beyond recording the
mode.
"""

from __future__ import annotations

from typing import Any

CHECK_NAME = "sandbox"


def check_sandbox(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(evidence, dict) or "sandbox" not in evidence:
        return None
    info = evidence.get("sandbox")
    if not isinstance(info, dict):
        return None
    mode = str(info.get("mode") or "host").strip().lower()
    limits = info.get("limits") if isinstance(info.get("limits"), dict) else {}
    if mode in ("container", "bwrap"):
        obs = f"{mode}: isolated"
        if limits:
            obs += " (" + ", ".join(f"{k}={v}" for k, v in sorted(limits.items())) + ")"
        return {
            "name": CHECK_NAME,
            "outcome": "pass",
            "observation": obs,
            "mode": mode,
            "limits": limits,
        }
    return {
        "name": CHECK_NAME,
        "outcome": "pass",
        "observation": "host: agent-written code ran on the daemon's machine",
        "mode": "host",
        "limits": limits,
        "finding": "host_mode",
    }
