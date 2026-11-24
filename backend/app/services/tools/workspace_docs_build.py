"""``workspace__docs_build``: the project's documentation build on demand (issue #766).

The Verifier builds a delivery's documentation (``docs_build``,
``evidence/doc_build.py``, run by ``loop/docs_checks.py``); an agent writing
documentation could not see what the builder says before it delivered. This
tool runs the same command: the stack profile's ``docs_build`` (the
company's, or the one a detected ``mkdocs.yml``, Sphinx ``conf.py`` or
``docusaurus.config.*`` names, ``workspace/stack_profiles.docs_build``):

- through ``workspace.execute``, in the company's sandbox and under the
  daemon's command policy, bounded by the test run's time
  (``sandbox.test_seconds``), as ``workspace__lint`` runs the lint;
- with no argument at all: the model never gives command text, paths or
  options; the detected commands write the site to ``/tmp``, never into the
  project.

Family X (``contracts/tool_steps``): offered where the step allows runs,
beside ``workspace__lint`` (``workspace_reads.offer_runs``), never to a
read-only toolbox, and only when the profile read for the lint names a docs
build command. Each call is an observation (``docs_build``: the command, the
exit status, whether it timed out or the builder was missing, the first
error lines, the sha256 of the output as received); what the model sees is
redacted (``context_read.redact_secrets``) and cut. The tool's result is
never the verdict: the delivery's own build is.
"""

from __future__ import annotations

import hashlib
from typing import Any

from services.evidence import doc_build
from services.evidence.context_read import redact_secrets
from services.tools import mcp_tools
from services.tools.developer_tool_schemas import SERVER
from services.workspace import sandbox

TOOL = "docs_build"
NAME = f"{SERVER}{mcp_tools.TOOL_NAME_SEP}{TOOL}"
TAIL_CHARS = 1500
REPLY_MARGIN_SECONDS = 30.0

SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Build the project's documentation (its MkDocs, Sphinx or Docusaurus "
            "build, as the project configures it) in the sandbox and see whether "
            "it builds and the first errors. Nothing in the project is written. "
            "Use it before you deliver documentation."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def offer(box: Any, profile: dict[str, Any] | None) -> None:
    """Set *box*'s ``docs_build_command`` from *profile* (the one the lint read)."""
    if box is None:
        return
    command = str((profile or {}).get("docs_build") or "").strip()
    box.docs_build_command = command or None


def schemas(box: Any) -> list[dict[str, Any]]:
    return [SCHEMA] if getattr(box, "docs_build_command", None) else []


def run(box: Any, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
    """One call: the text the model sees; *extra* gets the observation."""
    command = getattr(box, "docs_build_command", None)
    if not command:
        raise RuntimeError("the documentation build is not offered in this turn")
    record: dict[str, Any] = {"command": command, "ran": False}
    extra[TOOL] = record
    if getattr(box, "sandbox_error", None):
        record["reason"] = box.sandbox_error
        raise RuntimeError(f"docs build not run: {box.sandbox_error}")
    seconds = sandbox.test_seconds()
    request: dict[str, Any] = {
        "workspace_root": box.workspace_root,
        "command": command,
        "timeout_seconds": seconds,
    }
    if getattr(box, "sandbox", None) is not None:
        request["sandbox"] = box.sandbox
    reply = box.requester("workspace.execute", request, seconds + REPLY_MARGIN_SECONDS)
    output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    exit_code = reply.get("exit_code")
    shown = redact_secrets(TOOL, output)
    record.update(
        ran=not reply.get("refused") and exit_code is not None,
        exit_code=exit_code,
        timed_out=bool(reply.get("timed_out")),
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        output_chars=len(output),
        errors=doc_build.first_errors(shown),
        available=not doc_build.unavailable(exit_code, output),
    )
    if reply.get("refused"):
        record["refused"] = True
        return f"Docs build refused by the command policy: {reply.get('error')}"
    if record["timed_out"]:
        return f"Docs build (`{command}`) ran past {seconds} s and was stopped."
    if exit_code is None:
        record["reason"] = str(reply.get("error") or "no exit status")[:300]
        return f"Docs build could not run: {record['reason']}"
    if not record["available"]:
        return f"Docs build unavailable: `{command}` is not installed where the project runs.\n{shown[-400:]}"
    if exit_code == 0:
        return f"Docs build (`{command}`): built, exit 0."
    errors = "\n".join(record["errors"]) or "(no line reads as an error)"
    return (
        f"Docs build (`{command}`) exited {exit_code}.\nFirst errors:\n{errors}\n"
        f"Output tail:\n{shown[-TAIL_CHARS:]}"
    )
