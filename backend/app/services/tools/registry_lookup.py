"""The registry answers the Verifier's ``grounded`` check reads.

After a delivery writes a manifest, the backend looks each new dependency up
at its registry through the same code the research tools use
(``services.tools.research_registries``), and records the answer on the
evidence as an observation: the URL, the status, the sha256 of the body, the
published versions, whether the exact version asked for exists. Verification
never goes to the network; it reads these rows, and any lookup the run itself
made through the research tools, beside the daemon's files.

The lookups are bounded (``GROUNDING_MAX_LOOKUPS``) and turned off by
``GROUNDING_LOOKUPS_ENABLED``; a registry that cannot be reached records
``exists: null`` and never refutes a delivery. A package the run already
looked up successfully is not looked up again unless the manifest pins an
exact version, whose answer the tool call did not keep.
"""

from __future__ import annotations

from typing import Any

from config import settings

from services.evidence import manifests
from services.tools import research_tools


def _already_answered(evidence: dict[str, Any]) -> set[tuple[str, str, bool]]:
    """Keys already answered, and whether an exact version is still open."""
    answered: set[tuple[str, str, bool]] = set()
    for row in evidence.get("registry_lookups") or []:
        if (
            isinstance(row, dict)
            and row.get("package")
            and row.get("exists") is not None
        ):
            ecosystem = str(row.get("ecosystem") or "").lower()
            answered.add(
                (
                    ecosystem,
                    manifests.normalize_name(ecosystem, str(row["package"])),
                    bool(row.get("version")),
                )
            )
    for call in evidence.get("tool_calls") or []:
        if (
            isinstance(call, dict)
            and call.get("server") == research_tools.SERVER
            and call.get("tool") == research_tools.PACKAGE
            and call.get("package")
            and (not call.get("is_error") or call.get("status") == 404)
        ):
            ecosystem = str(call.get("ecosystem") or "").lower()
            answered.add(
                (
                    ecosystem,
                    manifests.normalize_name(ecosystem, str(call["package"])),
                    False,
                )
            )
    return answered


def _unique(dependencies: list[manifests.Dependency]) -> list[manifests.Dependency]:
    """One entry per package, preferring the one that pins an exact version."""
    found: dict[tuple[str, str], manifests.Dependency] = {}
    for dependency in dependencies:
        current = found.get(dependency.key)
        if current is None or (dependency.version and not current.version):
            found[dependency.key] = dependency
    return list(found.values())


def observe(
    evidence: dict[str, Any],
    files: list[dict[str, Any]],
    *,
    http: Any = None,
    hosts: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Record the registry's answer for each new dependency on *evidence*."""
    if not settings.GROUNDING_LOOKUPS_ENABLED:
        return []
    dependencies = _unique(manifests.dependencies_in(files))
    if not dependencies:
        return []
    answered = _already_answered(evidence)
    todo = [
        dependency
        for dependency in dependencies
        if (dependency.ecosystem, dependency.key[1], bool(dependency.version))
        not in answered
    ][: max(0, settings.GROUNDING_MAX_LOOKUPS)]
    if not todo:
        return []
    box = research_tools.ResearchToolbox(
        hosts=hosts if hosts is not None else research_tools.allowed_hosts(),
        http=http,
    )
    rows = [
        box.lookup_package(dependency.name, dependency.ecosystem, dependency.version)
        for dependency in todo
    ]
    evidence.setdefault("registry_lookups", []).extend(rows)
    return rows
