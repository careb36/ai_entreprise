"""SecOps as verification: dependency, secret and SAST scans that can block."""

from __future__ import annotations

from typing import Any

CHECK_NAME = "secops"


def _block(evidence: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    block = (evidence or {}).get(key)
    if block is None:
        return None
    if not isinstance(block, dict):
        return {"_malformed": True}
    if "error" in block:
        return {"_error": block.get("error")}
    return block


def _findings(block: dict[str, Any] | None, field: str) -> list[dict[str, Any]]:
    if block is None:
        return []
    if block.get("_malformed") or block.get("_error"):
        return [
            {
                "id": "malformed",
                "summary": str(block.get("_error") or "malformed result"),
            }
        ]
    items = block.get(field)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def check_dependencies(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    block = _block(evidence, "dependency_audit")
    if block is None:
        return None
    vulns = _findings(block, "vulnerabilities")
    tool = (
        block.get("tool") if isinstance(block.get("tool"), str) else None
    ) or "dependency audit"
    if block.get("_malformed") or block.get("_error"):
        return {
            "name": CHECK_NAME,
            "outcome": "refuted",
            "observation": f"{tool}: malformed result",
            "scan": "dependencies",
            "findings": vulns,
        }
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
    block = _block(evidence, "secret_scan")
    if block is None:
        return None
    findings = _findings(block, "findings")
    tool = (
        block.get("tool") if isinstance(block.get("tool"), str) else None
    ) or "secret scan"
    if block.get("_malformed") or block.get("_error"):
        return {
            "name": CHECK_NAME,
            "outcome": "refuted",
            "observation": f"{tool}: malformed result",
            "scan": "secrets",
            "findings": findings,
        }
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
    block = _block(evidence, "sast")
    if block is None:
        return None
    findings = _findings(block, "findings")
    tool = (block.get("tool") if isinstance(block.get("tool"), str) else None) or "sast"
    if block.get("_malformed") or block.get("_error"):
        return {
            "name": CHECK_NAME,
            "outcome": "refuted",
            "observation": f"{tool}: malformed result",
            "scan": "sast",
            "findings": findings,
        }
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
    out = []
    for fn in (check_dependencies, check_secrets_scan, check_sast):
        r = fn(evidence)
        if r is not None:
            out.append(r)
    return out
