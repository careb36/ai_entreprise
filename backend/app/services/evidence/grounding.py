"""The Verifier's ``grounded`` check: does the delivery name things that exist?

A model can invent a package (an attacker may register the name, slopsquatting
research calls it), a module of the project, or a version of a package. The
check reads the manifests and the imports of the files a delivery wrote or
changed (``services.evidence.manifests``, ``services.evidence.imports``)
and judges them against observations, never against the agent's word:

- a registry's answer, recorded on the evidence when the delivery was made
  (``services.tools.registry_lookup``), and any lookup the run itself made
  through the research tools;
- the files the daemon reported, its project listing and its writes.

A package no registry has, an exact version that does not exist, and an
import of the project's own modules that names no observed file **refute**
the delivery: the gate sends it back with the name. A dependency nobody
looked up, an exact version no registry answer addressed, and a third-party
import no delivered whole manifest declares are findings the reviewer reads,
not refutations. When the daemon's project listing was cut, an import that is
not found stays a finding too — the file may exist past the cut. So does an
unresolved import from a test file: QA writes its acceptance tests before the
module they name, and a test that names no file yet is the contract the next
task must meet (``criteria_tests.is_test_file``), not an invented import.

The symbol check (``services.evidence.signatures``) compares each call a
delivered Python file makes to a project function or class with its
signature, as the daemon reported it at delivery time (``signatures`` on the
evidence) or as the delivered text defines it. A mismatch is a finding,
never a refutation: the registry and import checks are the ones that gate.
``docs/POSITIONING.md``
keeps the rule: what this check reads is an observation; what the model says
about it is a claim.
"""

from __future__ import annotations

from typing import Any

from services.contracts.tool_calls import RESEARCH_PACKAGE, RESEARCH_SERVER
from services.contracts.verdicts import VERDICT_REFUTED
from services.evidence import imports as import_graph
from services.evidence import manifests
from services.evidence import signatures as symbol_check

CHECK_NAME = "grounded"


def _key(ecosystem: str, name: str) -> tuple[str, str]:
    return ecosystem, manifests.normalize_name(ecosystem, name)


def _answers(
    evidence: dict[str, Any] | None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Every registry answer about a package: the backend's, then the run's."""
    evidence = evidence or {}
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in evidence.get("registry_lookups") or []:
        if not isinstance(row, dict) or not row.get("package"):
            continue
        ecosystem = str(row.get("ecosystem") or "").lower()
        found.setdefault(_key(ecosystem, str(row["package"])), []).append(
            {
                "exists": row.get("exists"),
                "version": row.get("version"),
                "requested_exists": row.get("requested_exists"),
                "versions": [str(version) for version in row.get("versions") or []],
                "versions_complete": bool(row.get("versions_complete")),
                "by": "verifier",
            }
        )
    for call in evidence.get("tool_calls") or []:
        if (
            not isinstance(call, dict)
            or call.get("server") != RESEARCH_SERVER
            or call.get("tool") != RESEARCH_PACKAGE
            or not call.get("package")
        ):
            continue
        ecosystem = str(call.get("ecosystem") or "").lower()
        exists: bool | None
        if not call.get("is_error"):
            exists = True
        elif call.get("status") == 404:
            exists = False
        else:
            exists = None
        found.setdefault(_key(ecosystem, str(call["package"])), []).append(
            {
                "exists": exists,
                "version": call.get("version"),
                "requested_exists": None,
                "versions": [],
                "versions_complete": False,
                "by": "agent",
            }
        )
    return found


def _evaluate(
    dependency: manifests.Dependency,
    answers: list[dict[str, Any]],
) -> tuple[str, str | None]:
    """``("ok" | "missing" | "missing_version" | "unchecked", detail)``."""
    if not answers:
        return "unchecked", None
    if any(answer.get("exists") is False for answer in answers):
        return "missing", None
    if not any(answer.get("exists") is True for answer in answers):
        return "unchecked", None
    if not dependency.version:
        return "ok", None
    for answer in answers:
        if answer.get("requested_exists") is False:
            return "missing_version", str(answer.get("version") or dependency.version)
        if answer.get("requested_exists") is True:
            return "ok", None
        if answer.get("versions_complete") and answer.get("versions"):
            if any(
                manifests.version_matches(
                    dependency.ecosystem, dependency.version, version
                )
                for version in answer["versions"]
            ):
                return "ok", None
            return "missing_version", dependency.version
    if dependency.ecosystem == manifests.GO:
        # The Go proxy answered ``@latest``; it cannot speak to a version.
        return "ok", None
    # The package exists but no answer addressed the exact version: a finding,
    # not a pass we did not observe.
    return "unchecked", None


def _declared(dependencies: list[manifests.Dependency]) -> dict[str, set[str]]:
    declared: dict[str, set[str]] = {
        manifests.PYPI: set(),
        manifests.NPM: set(),
        manifests.CRATES: set(),
        manifests.GO: set(),
    }
    for dependency in dependencies:
        declared[dependency.ecosystem].add(dependency.key[1])
    return declared


def _whole_manifests(files: list[dict[str, Any]]) -> set[str]:
    """The ecosystems whose whole manifest the delivery wrote (not just an edit)."""
    whole: set[str] = set()
    for file in files:
        if "content" not in file:
            continue
        base = str(file.get("path") or "").rsplit("/", 1)[-1].lower()
        if (
            base.startswith("requirements")
            and base.endswith(".txt")
            or base == "pyproject.toml"
        ):
            whole.add(manifests.PYPI)
        elif base == "package.json":
            whole.add(manifests.NPM)
        elif base == "cargo.toml":
            whole.add(manifests.CRATES)
        elif base == "go.mod":
            whole.add(manifests.GO)
    return whole


def _import_ecosystem(entry: import_graph.ImportRef) -> str:
    if entry.kind in (import_graph.PYTHON, import_graph.PYTHON_RELATIVE):
        return manifests.PYPI
    if entry.kind == import_graph.ES:
        return manifests.NPM
    if entry.kind == import_graph.GO:
        return manifests.GO
    return manifests.CRATES


def _names(entries: list[import_graph.ImportRef], limit: int = 8) -> str:
    return ", ".join(
        f"{entry.path} imports {entry.specifier}" for entry in entries[:limit]
    )


def check_grounded(
    evidence: dict[str, Any] | None, files: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """The Verifier's ``grounded`` check; absent when no manifest or import was delivered."""
    evidence = evidence or {}
    files = [file for file in files or [] if isinstance(file, dict)]
    dependencies = manifests.dependencies_in(files)
    found_imports = import_graph.imports_in(files)
    symbols = symbol_check.audit(evidence, files)
    if not dependencies and not found_imports and not symbols["mismatches"]:
        return None

    answers = _answers(evidence)
    missing_packages: list[str] = []
    missing_versions: list[str] = []
    unchecked_dependencies: list[str] = []
    for dependency in dependencies:
        verdict, detail = _evaluate(dependency, answers.get(dependency.key, []))
        label = f"{dependency.ecosystem}:{dependency.name}"
        if dependency.version:
            label += f"=={dependency.version}"
        if verdict == "missing":
            missing_packages.append(label)
        elif verdict == "missing_version":
            missing_versions.append(f"{label} (asked {detail})")
        elif verdict == "unchecked":
            unchecked_dependencies.append(label)

    declared = _declared(dependencies)
    index = import_graph.ProjectIndex.from_evidence(evidence, files)
    audit = import_graph.audit(
        found_imports,
        index,
        declared,
        go_module=manifests.go_module(files),
    )
    # A third-party import is only judged against a manifest the delivery
    # wrote whole: an edit's fragment does not list the project's other
    # dependencies, and calling those undeclared would be a false finding.
    whole = _whole_manifests(files)
    undeclared = [
        entry for entry in audit.undeclared if _import_ecosystem(entry) in whole
    ]

    refuted = bool(missing_packages or missing_versions or audit.unresolved)
    parts: list[str] = []
    if missing_packages:
        parts.append("no registry has " + ", ".join(missing_packages[:6]))
    if missing_versions:
        parts.append("no such version: " + ", ".join(missing_versions[:6]))
    if audit.unresolved:
        parts.append(
            f"{len(audit.unresolved)} import(s) name no file: "
            + _names(audit.unresolved)
        )
    if not parts:
        if dependencies:
            parts.append(
                f"{len(dependencies)} dependency(ies) answered by a registry"
                if not unchecked_dependencies
                else f"{len(dependencies)} dependency(ies) named, "
                f"{len(unchecked_dependencies)} without a registry answer"
            )
        if found_imports:
            parts.append(f"{audit.resolved} project import(s) resolve")
    if undeclared:
        parts.append(f"{len(undeclared)} third-party import(s) undeclared")
    if symbols["mismatches"]:
        parts.append(
            f"{len(symbols['mismatches'])} call(s) contradict a signature: "
            + ", ".join(
                f"{m['path']}:{m['line']} {m['callee']} ({m['problem']})"
                for m in symbols["mismatches"][:4]
            )
        )
    if symbols["unobserved"]:
        parts.append(
            f"{symbols['unobserved']} call(s) to project code not compared: "
            f"signatures not observed ({symbols['reason'] or 'no daemon reply recorded'})"
        )
    if audit.skipped:
        parts.append(audit.skipped)
    else:
        if audit.test_first:
            parts.append(
                f"{len(audit.test_first)} test import(s) name no module yet: "
                + _names(audit.test_first)
            )
        if audit.unchecked:
            parts.append(
                f"{len(audit.unchecked)} import(s) not found in a cut project listing"
            )

    return {
        "name": CHECK_NAME,
        "outcome": VERDICT_REFUTED if refuted else "pass",
        "observation": "; ".join(parts),
        "dependencies": [
            {
                "ecosystem": dependency.ecosystem,
                "name": dependency.name,
                "version": dependency.version,
            }
            for dependency in dependencies[:40]
        ],
        "missing_packages": missing_packages[:20],
        "missing_versions": missing_versions[:20],
        "missing_imports": [
            {
                "path": entry.path,
                "specifier": entry.specifier,
                "kind": entry.kind,
                "line": entry.line,
            }
            for entry in audit.unresolved[:20]
        ],
        "undeclared_imports": [
            {
                "path": entry.path,
                "specifier": entry.specifier,
                "kind": entry.kind,
                "line": entry.line,
            }
            for entry in undeclared[:20]
        ],
        "unchecked_dependencies": unchecked_dependencies[:20],
        "unchecked_imports": len(audit.unchecked),
        "test_first_imports": [
            {
                "path": entry.path,
                "specifier": entry.specifier,
                "kind": entry.kind,
                "line": entry.line,
            }
            for entry in audit.test_first[:20]
        ],
        "imports_resolved": audit.resolved,
        "project_files_complete": index.complete,
        "imports_skipped": audit.skipped,
        "registry_answers": sum(len(rows) for rows in answers.values()),
        # Calls against signatures: findings, never the verdict.
        "signatures_observed": symbols["observed"],
        "signature_calls_checked": symbols["checked"],
        "signature_mismatches": symbols["mismatches"][: symbol_check.MISMATCH_LIMIT],
    }
