"""Source references: every statement in a pack names the record it came from.

A reference is plain data, ``{"kind", "id", "sha256"?, "label"?, "claim"?}``:

- ``kind`` is the record's kind (``specification``, ``requirement``,
  ``architecture``, ``module``, ``interface``, ``adr``, ``discovery``,
  ``sprint``, ``task``, ``event``, ``verification``, ``message``,
  ``procedure``, ``integration``, ``membership``, ``rule``);
- ``sha256`` is a digest the backend computed or observed, never one a producer
  handed in: a record's canonical JSON (``digest``), a task event's chain hash,
  a run's artifact digest, a commit the daemon reported;
- ``claim`` marks a reference to something a party said about itself (an agent
  message, a retrospective): the document cites it as what was said, not as
  what happened.

``Sources`` numbers the references of one document (``[S1]``, ``[S2]``…) in
the order they are first cited, so the same snapshot always gives the same
numbers, and renders the list that closes the document.

Pure; no I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from services.planning.project_docs.sanitize import md_id, md_inline

NO_SOURCE = "No source recorded"
# A digest shows only its hex digits and its prefix (``hmac-sha256:``).
_HASH_DROP = re.compile(r"[^A-Za-z0-9:\-]")

_KIND_NAMES = {
    "specification": "Specification",
    "requirement": "Requirement",
    "architecture": "Architecture",
    "module": "Module",
    "interface": "Interface",
    "adr": "Decision",
    "discovery": "Stack discovery",
    "sprint": "Sprint",
    "task": "Task",
    "event": "Task event",
    "verification": "Verification",
    "message": "Agent message",
    "procedure": "Procedure",
    "integration": "Integration",
    "membership": "Company membership",
    "rule": "Factory rule",
}


def digest(value: Any) -> str:
    """The sha256 of *value*'s canonical JSON (sorted keys, no spaces)."""
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def ref(
    kind: str,
    ident: Any,
    *,
    sha256: str | None = None,
    label: str = "",
    claim: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "id": str(ident)}
    if sha256:
        out["sha256"] = str(sha256)
    if label:
        out["label"] = str(label)
    if claim:
        out["claim"] = True
    return out


def describe(reference: dict[str, Any]) -> str:
    """One reference as a line of literal Markdown."""
    kind = reference.get("kind", "")
    ident = reference.get("id")
    shown = md_inline(ident, 120) if kind == "rule" else md_id(ident)
    text = f"{_KIND_NAMES.get(kind, md_inline(kind))} {shown}"
    if reference.get("label"):
        text += f": {md_inline(reference['label'], 120)}"
    if reference.get("sha256"):
        text += f" · `{_HASH_DROP.sub('', str(reference['sha256']))[:100]}`"
    if reference.get("claim"):
        text += " · a claim by the party recorded, not an observation"
    return text


class Sources:
    """The numbered references of one document."""

    def __init__(self) -> None:
        self._refs: list[dict[str, Any]] = []
        self._index: dict[str, int] = {}

    def cite(self, *references: dict[str, Any]) -> str:
        """``[S1]`` (or ``[S1, S3]``) for *references*, numbering new ones."""
        marks = []
        for reference in references:
            key = digest(reference)
            if key not in self._index:
                self._refs.append(reference)
                self._index[key] = len(self._refs)
            marks.append(f"S{self._index[key]}")
        return f"\\[{', '.join(marks)}\\]" if marks else f"({NO_SOURCE})"

    def as_list(self) -> list[dict[str, Any]]:
        return [{"mark": f"S{i}", **r} for i, r in enumerate(self._refs, 1)]

    def section(self) -> str:
        if not self._refs:
            return (
                f"## Sources\n\n{NO_SOURCE}: nothing in this document cites a record.\n"
            )
        lines = [f"- S{i}: {describe(r)}" for i, r in enumerate(self._refs, 1)]
        return "## Sources\n\n" + "\n".join(lines) + "\n"
