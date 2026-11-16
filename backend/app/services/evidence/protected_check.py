"""The Verifier's ``protected_paths`` check (``docs/design/rsi-loop.md``,
section 3.3, layer 1; REQ-10).

For a company that hosts the factory (``companies.protected_paths_enabled``,
``services/platform/protected_paths_setting.py``), the paths a delivery
changed are matched against the protected list
(``services/contracts/protected_paths.py``): the graders, the evidence, CI,
the rules and the list itself. The paths are the ones the platform observed:
the daemon's report of what it wrote and read back (``workspace_writes``,
status ``written``) and the files the daemon saw a console producer change
(``console_run.files``). The paths the producer declared are added: a claim
may add to a match, never remove one.

The list in force is the running backend's: the tuple in contracts, plus the
tests whose contexts include ``evidence`` when the backend's own checkout has
the test map (``docs/architecture/contexts.json``; a container built from
``backend/`` alone has only the tuple). It is never read from the company's
project, which is what the sprint's agents can write.

A match is a reason for a person, not a fault: the check's outcome is
``pass`` with the matched paths in the observation and in ``matches``, and
the ``risk_tier`` check rises to ``high`` with the area ``grader``
(``risk.classify``), so the task waits for a person at every autonomy level
and no Manager rule accepts it (``manager_rules._accept_high_risk``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.contracts import protected_paths

CHECK_NAME = "protected_paths"
# backend/app/services/evidence/protected_check.py -> the repository's root.
_TEST_MAP = Path(__file__).resolve().parents[4] / "docs/architecture/contexts.json"
_QUOTED = 20


@lru_cache(maxsize=1)
def patterns_in_force() -> tuple[str, ...]:
    """The protected list of the running backend, with the evidence's tests when known."""
    try:
        files = json.loads(_TEST_MAP.read_text(encoding="utf-8"))["tests"]["files"]
    except (OSError, ValueError, KeyError, TypeError):
        files = None
    if not isinstance(files, dict):
        files = None
    return protected_paths.protected_patterns(files)


def _strings(values: Iterable[Any]) -> list[str]:
    return [v for v in values if isinstance(v, str) and v]


def observed_paths(evidence: dict[str, Any] | None) -> list[str] | None:
    """The paths the daemon saw change, or ``None`` when it reported none at all."""
    evidence = evidence or {}
    writes = evidence.get("workspace_writes")
    console = evidence.get("console_run")
    if not isinstance(writes, list) and not isinstance(console, dict):
        return None
    paths: list[str] = []
    if isinstance(writes, list):
        paths += _strings(
            w.get("path")
            for w in writes
            if isinstance(w, dict) and w.get("status") == "written"
        )
    if isinstance(console, dict):
        paths += _strings(
            f.get("path") for f in console.get("files") or [] if isinstance(f, dict)
        )
    return paths


def check(
    evidence: dict[str, Any] | None,
    declared: Iterable[Any],
    patterns: Sequence[str],
) -> dict[str, Any]:
    """The protected paths a delivery touched, from what was observed and declared."""
    observed = observed_paths(evidence)
    seen = set(observed or [])
    found = protected_paths.matches([*(observed or []), *_strings(declared)], patterns)
    matched = [
        {
            "path": m.path,
            "pattern": m.pattern,
            "observed": any(protected_paths.normalize(p) == m.path for p in seen),
        }
        for m in found
    ]
    if matched:
        shown = ", ".join(f"{m['path']} ({m['pattern']})" for m in matched[:_QUOTED])
        more = f" (and {len(matched) - _QUOTED} more)" if len(matched) > _QUOTED else ""
        observation = f"protected paths touched, a person decides: {shown}{more}"
    else:
        declared_count = len(_strings(declared))
        observation = (
            f"no protected path among {len(seen)} observed and "
            f"{declared_count} declared path(s)"
        )
    if observed is None:
        observation += "; the daemon reported no writes"
    return {
        "name": CHECK_NAME,
        "outcome": "pass",
        "observation": observation,
        "matches": matched,
        "observed": observed is not None,
        "patterns": len(patterns),
    }
