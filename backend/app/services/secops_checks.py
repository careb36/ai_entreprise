"""SecOps as verification: dependency, secret and SAST scans that can block.

The daemon (or a CI step) runs the scans on the delivered files and records
their result in the run's evidence; here the Verifier turns that result into
a check. A clean scan passes; a finding refutes the run, the same way a
secret in the deliverable does. Nothing is asked of a model, and the scans
are never run by the backend itself.

The evidence keys are:
- ``dependency_audit``: ``{"tool": ..., "vulnerabilities": [{"id", "package",
  "severity", "summary"}]}``
- ``secret_scan``: ``{"tool": ..., "findings": [{"path", "rule", "line"}]}``
- ``sast``: ``{"tool": ..., "findings": [{"path", "rule", "severity", "line"}]}``
"""

from __future__ import annotations

from typing import Any

CHECK_NAME = "secops"


def _vulns(
    evidence: dict[str, Any] | None, key: str, field: str
) -> list[dict[str, Any]]:
    block = (evidence or {}).get(key)
    if not isinstance(block, dict):
        return []
    items = block.get(field)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def check_dependencies(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if (evidence or {}).get("dependency_audit") is None:
        return None
    vulns = _vulns(evidence, "dependency_audit", "vulnerabilities")
    tool = ((evidence or {}).get("dependency_audit") or {}).get(
        "tool"
    ) or "dependency audit"
    if not vulns:
        return {
            "name": CHECK_NAME,
            "outcome": "pass",
            "observation": f"{tool}: no vulnerabilities",
            "scan": "dependencies",
            "findings": [],
        }
    ids = [str(v.get("id") or v.get("package") or "vuln") for v in vulns[:5]]
    return {
        "name": CHECK_NAME,
        "outcome": "refuted",
        "observation": f"{tool}: " + "; ".join(ids),
        "scan": "dependencies",
        "findings": vulns,
    }


def check_secrets_scan(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if (evidence or {}).get("secret_scan") is None:
        return None
    findings = _vulns(evidence, "secret_scan", "findings")
    tool = ((evidence or {}).get("secret_scan") or {}).get("tool") or "secret scan"
    if not findings:
        return {
            "name": CHECK_NAME,
            "outcome": "pass",
            "observation": f"{tool}: no secrets",
            "scan": "secrets",
            "findings": [],
        }
    labels = [f"{f.get('path')}:{f.get('line')} {f.get('rule')}" for f in findings[:5]]
    return {
        "name": CHECK_NAME,
        "outcome": "refuted",
        "observation": f"{tool}: " + "; ".join(labels),
        "scan": "secrets",
        "findings": findings,
    }


def check_sast(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if (evidence or {}).get("sast") is None:
        return None
    findings = _vulns(evidence, "sast", "findings")
    tool = ((evidence or {}).get("sast") or {}).get("tool") or "sast"
    if not findings:
        return {
            "name": CHECK_NAME,
            "outcome": "pass",
            "observation": f"{tool}: no findings",
            "scan": "sast",
            "findings": [],
        }
    labels = [f"{f.get('path')}:{f.get('line')} {f.get('rule')}" for f in findings[:5]]
    return {
        "name": CHECK_NAME,
        "outcome": "refuted",
        "observation": f"{tool}: " + "; ".join(labels),
        "scan": "sast",
        "findings": findings,
    }


def check_all(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Run every scan that reported a result; skip the ones that did not."""
    out = []
    for fn in (check_dependencies, check_secrets_scan, check_sast):
        r = fn(evidence)
        if r is not None:
            out.append(r)
    return out
