"""The registries the research tools read (``services/research_tools.py``):
a package's record at PyPI, npm, crates.io or the Go proxy by the registries'
own APIs, and the rows of an OSV answer. Pure parsing over what the toolbox
fetched; the fetch itself, with its record, stays in the toolbox."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from config import settings

ECOSYSTEMS = ("pypi", "npm", "crates", "go")
OSV_ECOSYSTEMS = {"pypi": "PyPI", "npm": "npm", "crates": "crates.io", "go": "Go"}
VERSIONS_SHOWN = 10
ADVISORIES_SHOWN = 20


class ResearchError(RuntimeError):
    """A call that could not be served; the message is what the model sees."""


def package_record(
    get: Callable[..., tuple[int, dict[str, str], bytes]],
    name: str,
    ecosystem: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """A package's record at its registry, by *get* (an allowed GET with the
    record kept on *extra*): the latest version, when it was released, the
    last versions, the summary. Raises ``ResearchError`` when there is none."""
    if ecosystem == "pypi":
        url = f"{settings.RESEARCH_PYPI_URL.rstrip('/')}/{name}/json"
        status, _h, data = get(url, extra)
        if status == 404:
            raise ResearchError(f"PyPI has no package named {name!r}.")
        if status >= 400:
            raise ResearchError(f"PyPI answered {status}.")
        doc = json.loads(data.decode("utf-8", errors="replace"))
        info, releases = doc.get("info") or {}, doc.get("releases") or {}
        latest = info.get("version")
        dated = [
            (
                v,
                min(
                    (
                        f.get("upload_time_iso_8601") or f.get("upload_time") or ""
                        for f in files
                    ),
                    default="",
                ),
            )
            for v, files in releases.items()
            if files
        ]
        dated.sort(key=lambda item: item[1], reverse=True)
        return {
            "name": info.get("name") or name,
            "ecosystem": ecosystem,
            "latest": latest,
            "released": next((d for v, d in dated if v == latest), None),
            "versions": [v for v, _d in dated[:VERSIONS_SHOWN]],
            "requires_python": info.get("requires_python"),
            "summary": (info.get("summary") or "")[:300],
            "homepage": info.get("home_page")
            or (info.get("project_urls") or {}).get("Homepage"),
            "yanked_latest": bool(
                any(f.get("yanked") for f in releases.get(latest) or [])
            ),
        }
    if ecosystem == "npm":
        url = f"{settings.RESEARCH_NPM_URL.rstrip('/')}/{name}"
        status, _h, data = get(url, extra)
        if status == 404:
            raise ResearchError(f"npm has no package named {name!r}.")
        if status >= 400:
            raise ResearchError(f"npm answered {status}.")
        doc = json.loads(data.decode("utf-8", errors="replace"))
        latest = (doc.get("dist-tags") or {}).get("latest")
        times = {
            k: v
            for k, v in (doc.get("time") or {}).items()
            if k not in ("created", "modified")
        }
        ordered = sorted(times.items(), key=lambda item: item[1], reverse=True)
        return {
            "name": doc.get("name") or name,
            "ecosystem": ecosystem,
            "latest": latest,
            "released": times.get(latest),
            "versions": [v for v, _t in ordered[:VERSIONS_SHOWN]],
            "summary": (doc.get("description") or "")[:300],
            "homepage": doc.get("homepage"),
            "deprecated": bool(
                ((doc.get("versions") or {}).get(latest) or {}).get("deprecated")
            ),
        }
    if ecosystem == "crates":
        url = f"{settings.RESEARCH_CRATES_URL.rstrip('/')}/{name}"
        status, _h, data = get(url, extra)
        if status == 404:
            raise ResearchError(f"crates.io has no crate named {name!r}.")
        if status >= 400:
            raise ResearchError(f"crates.io answered {status}.")
        doc = json.loads(data.decode("utf-8", errors="replace"))
        crate, versions = doc.get("crate") or {}, doc.get("versions") or []
        return {
            "name": crate.get("name") or name,
            "ecosystem": ecosystem,
            "latest": crate.get("max_stable_version") or crate.get("max_version"),
            "released": next(
                (
                    v.get("created_at")
                    for v in versions
                    if v.get("num")
                    == (crate.get("max_stable_version") or crate.get("max_version"))
                ),
                None,
            ),
            "versions": [v.get("num") for v in versions[:VERSIONS_SHOWN]],
            "summary": (crate.get("description") or "")[:300],
            "homepage": crate.get("homepage") or crate.get("repository"),
        }
    url = f"{settings.RESEARCH_GO_URL.rstrip('/')}/{name}/@latest"
    status, _h, data = get(url, extra)
    if status == 404 or status == 410:
        raise ResearchError(f"The Go proxy has no module named {name!r}.")
    if status >= 400:
        raise ResearchError(f"The Go proxy answered {status}.")
    doc = json.loads(data.decode("utf-8", errors="replace"))
    return {
        "name": name,
        "ecosystem": ecosystem,
        "latest": doc.get("Version"),
        "released": doc.get("Time"),
        "versions": [doc.get("Version")],
    }


def advisory_rows(found: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """The rows the model sees for the vulnerabilities OSV returned."""
    return [
        {
            "id": v.get("id"),
            "summary": (v.get("summary") or v.get("details") or "")[:300],
            "severity": _severity(v),
            "fixed": _fixed_versions(v, name),
            "published": v.get("published"),
        }
        for v in found[:ADVISORIES_SHOWN]
    ]


def _severity(vuln: dict[str, Any]) -> str | None:
    specific = vuln.get("database_specific") or {}
    if isinstance(specific, dict) and specific.get("severity"):
        return str(specific["severity"])
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict) and entry.get("score"):
            return f"{entry.get('type')}: {entry['score']}"
    return None


def _fixed_versions(vuln: dict[str, Any], name: str) -> list[str]:
    fixed: list[str] = []
    for affected in vuln.get("affected") or []:
        if ((affected.get("package") or {}).get("name") or "").lower() != name.lower():
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if isinstance(event, dict) and event.get("fixed"):
                    fixed.append(str(event["fixed"]))
    return fixed[:5]


# ---------------------------------------------------------------------------
# The prompt, the evidence
# ---------------------------------------------------------------------------
