"""The ``docs_build`` check: the project's documentation builds (issue #766).

Documenting a whole project asks that "the project's docs build (mkdocs,
Sphinx, Docusaurus) passes, as an observation" (``docs/design/agent-toolsets.md``,
"Next changes" item 10). For a delivery that changes documentation the loop
runs the stack profile's ``docs_build`` command (a company's own, or the one
a detected ``mkdocs.yml``, Sphinx ``conf.py`` or ``docusaurus.config.*``
names, ``workspace/stack_profiles.docs_build``) through the daemon in the
company's sandbox (``loop/docs_checks.py``) and keeps the reply as
``evidence["docs_build"]``: the command, the exit status, whether it timed
out, the first error lines and the sha256 of the output, computed by the
backend over what the daemon sent.

- Exit 0: ``pass``.
- Any other exit: ``refuted``, with the first errors; unless the builder is
  not installed where the project runs (exit 127, "No module named
  mkdocs"…): ``unverifiable``, since nothing was built.
- Not run (no sandbox, refused by the command policy, timed out, the daemon
  silent, tests off for the deployment): ``unverifiable`` with the reason.
- No build command (``applicable: false``) or a delivery that changed no
  documentation: no check at all. Not applicable is never a pass.

``first_errors`` and ``unavailable`` are shared with the agents'
``workspace__docs_build`` tool (``services/tools/workspace_docs_build.py``),
so the tool shows what the check would read. Pure: no I/O.
"""

from __future__ import annotations

import re
from typing import Any

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.evidence import doc_snippets

CHECK_NAME = "docs_build"
KIND = "docs_build"
MAX_ERRORS = 10
ERROR_CHARS = 300
_ERROR = re.compile(
    r"\b(error|errors|exception|traceback|failed|fatal|aborted)\b|^\s*E\s",
    re.IGNORECASE,
)
UNAVAILABLE_MARKERS = (
    "command not found",
    "no module named",
    "not recognized as an internal or external command",
    "is not installed",
    "executable file not found",
    "could not determine executable to run",
    "missing script",
)


def first_errors(output: str) -> list[str]:
    """The first lines of *output* that read as errors, cut, at most ``MAX_ERRORS``."""
    found: list[str] = []
    for line in (output or "").splitlines():
        if line.strip() and _ERROR.search(line):
            found.append(line.strip()[:ERROR_CHARS])
            if len(found) >= MAX_ERRORS:
                break
    return found


def unavailable(exit_code: Any, output: str) -> bool:
    """Whether the builder itself is missing where the command ran."""
    if exit_code == 127:
        return True
    lowered = (output or "").lower()
    return exit_code not in (0, None) and any(m in lowered for m in UNAVAILABLE_MARKERS)


def check(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """``docs_build`` from ``evidence["docs_build"]``; ``None`` when there is
    none or the project names no build command (module docstring)."""
    record = (evidence or {}).get("docs_build")
    if not isinstance(record, dict) or not record.get("applicable"):
        return None
    command = record.get("command") or "?"
    digest = record.get("output_sha256")
    tail = f" (output sha256 {digest[:12]})" if digest else ""
    exit_code = record.get("exit_code")
    result: dict[str, Any] = {"name": CHECK_NAME, "command": command}
    if record.get("refused"):
        outcome, why = VERDICT_UNVERIFIABLE, f"refused: {record.get('reason')}"
    elif record.get("timed_out"):
        outcome, why = VERDICT_UNVERIFIABLE, "ran past its time limit and was stopped"
    elif exit_code is None:
        outcome = VERDICT_UNVERIFIABLE
        why = f"not run: {record.get('reason') or 'no exit status from the daemon'}"
    elif record.get("available") is False:
        outcome, why = VERDICT_UNVERIFIABLE, "the builder is not installed where it ran"
    elif exit_code == 0:
        outcome, why = "pass", "built (exit 0)"
    else:
        errors = [e for e in record.get("errors") or [] if isinstance(e, str)]
        outcome = VERDICT_REFUTED
        why = f"exited {exit_code}" + (f": {'; '.join(errors[:3])}" if errors else "")
        result["errors"] = errors
    result.update(outcome=outcome, observation=f"`{command}` {why}{tail}")
    return result


def checks(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The checks of a documentation delivery beyond its links, for the
    Verifier: ``docs_build`` and ``docs_snippet_names``
    (``doc_snippets.check``), each only when applicable."""
    return [c for c in (check(evidence), doc_snippets.check(evidence)) if c]
