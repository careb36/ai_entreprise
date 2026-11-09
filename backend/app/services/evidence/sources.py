"""The Verifier's ``sources`` check: what a run consulted through the research tools.

``sources_of`` keeps the recorded fields of every research call,
``packages_in`` reads the dependencies a delivery's manifests name, and
``check_sources`` reports the sources with their hashes and the dependencies
named without a registry lookup. Pure functions of the observations, moved out
of ``services/research_tools.py`` (tools), which re-exports them, so the
evidence never imports the tools (ADR 0012, issue #469 step 5).
"""

from __future__ import annotations

import json
import re
from typing import Any

from services.contracts import edit_blocks
from services.contracts.tool_calls import (
    RESEARCH_ADVISORIES,
    RESEARCH_PACKAGE,
    RESEARCH_SERVER,
)


def sources_of(tool_calls: Any) -> list[dict[str, Any]]:
    """The sources a run consulted: every research call, as recorded."""
    return [
        {
            k: c.get(k)
            for k in (
                "tool",
                "url",
                "host",
                "allowed",
                "fetched_at",
                "status",
                "sha256",
                "bytes",
                "package",
                "ecosystem",
                "version",
                "is_error",
            )
            if k in c
        }
        for c in tool_calls or []
        if isinstance(c, dict) and c.get("server") == RESEARCH_SERVER
    ]


_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:[=<>!~]=?|$)"
)


def packages_in(files: list[dict[str, Any]]) -> dict[str, set[str]]:
    """The dependencies the delivered manifests name, by ecosystem:
    ``requirements*.txt`` and ``pyproject.toml`` (pypi), ``package.json`` (npm)."""
    found: dict[str, set[str]] = {"pypi": set(), "npm": set()}
    for file in files:
        path = str(file.get("path") or "")
        content = edit_blocks.scanned_text(file)
        base = path.rsplit("/", 1)[-1].lower()
        if base.startswith("requirements") and base.endswith(".txt"):
            for line in content.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith(("-", "git+", "http://", "https://")):
                    continue
                match = _REQUIREMENT.match(line)
                if match:
                    found["pypi"].add(match.group(1).lower())
        elif base == "pyproject.toml":
            for match in re.finditer(
                r'^\s*"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*[=<>!~^]',
                content,
                re.MULTILINE,
            ):
                found["pypi"].add(match.group(1).lower())
        elif base == "package.json":
            try:
                doc = json.loads(content)
            except ValueError:  # An edit's fragment: the pairs that look like versions.
                doc = {"dependencies": edit_blocks.json_dependency_pairs(content)}
            for key in ("dependencies", "devDependencies"):
                for dep in (doc.get(key) or {}) if isinstance(doc, dict) else {}:
                    found["npm"].add(str(dep).lower())
    return found


def check_sources(
    evidence: dict[str, Any] | None, files: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """The Verifier's ``sources`` check: the sources a run consulted, with
    their hashes, and the dependencies its manifests name that it never
    looked up at a registry. A record, never the verdict; absent when the
    research tools were not used and no manifest was delivered."""
    if not evidence or "tool_calls" not in evidence:
        return None
    sources = sources_of(evidence.get("tool_calls"))
    named = packages_in(files or [])
    looked_up = {
        (s.get("ecosystem"), str(s.get("package") or "").lower())
        for s in sources
        if s.get("tool") in (RESEARCH_PACKAGE, RESEARCH_ADVISORIES)
        and not s.get("is_error")
    }
    unchecked = sorted(
        f"{eco}:{name}"
        for eco, names in named.items()
        for name in names
        if (eco, name) not in looked_up
    )
    if not sources and not any(named.values()):
        return None
    fetched = [s for s in sources if not s.get("is_error") and s.get("allowed")]
    refused = [s for s in sources if s.get("allowed") is False]
    parts = [f"{len(fetched)} source(s) consulted"]
    if refused:
        parts.append(f"{len(refused)} refused (host not allowed)")
    if unchecked:
        parts.append(
            "dependencies named without a registry lookup: " + ", ".join(unchecked[:8])
        )
    return {
        "name": "sources",
        "outcome": "pass",
        "observation": "; ".join(parts),
        "sources": sources,
        "unchecked_dependencies": unchecked,
    }
