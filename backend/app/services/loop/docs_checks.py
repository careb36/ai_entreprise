"""A documentation delivery's observations: its build and its snippets' names (issue #766).

After the daemon wrote a delivery that changes documentation (a path
``contracts/tool_steps.is_doc_path`` accepts among the files it reported
written), the loop asks it for what the Verifier's ``docs_build`` and
``docs_snippet_names`` checks then judge (``evidence/doc_build.py``,
``evidence/doc_snippets.py``):

- **The build.** The stack profile's ``docs_build`` command (the company's,
  or the one a detected MkDocs, Sphinx or Docusaurus configuration names,
  ``workspace/stack_profiles.docs_build``) runs through ``workspace.execute``
  in the company's sandbox, under the daemon's command policy
  (``delivery_commands.run_in_folder``). Kept as ``evidence["docs_build"]``:
  the command, the exit status, the sha256 the backend computed over the
  output it received, the first error lines (redacted) and whether the builder
  was there at all. No command: ``applicable: false``, and no check. Tests
  off for the deployment (``DELIVERY_TESTS_ENABLED``): not run, since a docs
  build runs project code (a Sphinx ``conf.py``, MkDocs plugins).
- **The snippets' names.** The project names the delivered documents' fenced
  blocks import or call (``doc_snippets.wanted``), each looked up through the
  daemon's code graph (``workspace.related`` with ``lookup.kind:
  "definition"``), only when every daemon announces the ``lookup`` feature;
  each answer kept with the files and sha256 the daemon read. Snippets are
  never run.

Every failure is recorded as such and never stops the delivery: these are
observations, the evidence judges them.
"""

from __future__ import annotations

import logging
from typing import Any

from config import settings

from services.contracts import tool_steps
from services.evidence import doc_build, doc_snippets
from services.evidence.context_read import redact_secrets
from services.workspace import daemon_liveness, delivery_commands, workspace_daemon
from services.workspace.project_profile import Context

logger = logging.getLogger(__name__)

SUBJECT = "workspace.related"
FEATURE = "lookup"
LOOKUP_TIMEOUT_SECONDS = 10.0
NO_COMMAND = (
    "the project names no documentation build (no docs_build in its stack "
    "profile, no mkdocs.yml, Sphinx conf.py or docusaurus.config.*)"
)
TESTS_OFF = "commands are disabled for this deployment (DELIVERY_TESTS_ENABLED is off)"
_KEPT = (
    "exit_code",
    "timed_out",
    "timeout_seconds",
    "output_sha256",
    "output_bytes",
    "reason",
    "refused",
    "sandbox",
)


def changes_documentation(evidence: dict[str, Any] | None) -> bool:
    """Whether the daemon reported writing a documentation file."""
    writes = (evidence or {}).get("workspace_writes")
    return isinstance(writes, list) and any(
        isinstance(w, dict)
        and w.get("status") == "written"
        and tool_steps.is_doc_path(w.get("path"))
        for w in writes
    )


async def observe(
    nats_client: Any,
    sprint: dict[str, Any],
    evidence: dict[str, Any] | None,
    declared: list[dict[str, Any]],
    engine: Any = None,
) -> None:
    """Record ``docs_snippets`` and ``docs_build`` on *evidence* for a
    delivery that changes documentation; nothing for any other."""
    if evidence is None or not changes_documentation(evidence):
        return
    await snippet_names(nats_client, evidence, declared)
    await build(nats_client, sprint, evidence, engine)


async def build(
    nats_client: Any, sprint: dict[str, Any], evidence: dict[str, Any], engine: Any
) -> dict[str, Any]:
    """Run the documentation build and keep ``evidence["docs_build"]``."""
    ctx = Context(nats_client, sprint, evidence, engine)
    try:
        profile = await ctx.profile()
    except Exception as exc:  # noqa: BLE001 - No profile read: no command known.
        logger.warning("Could not read the stack profile for the docs build: %s", exc)
        profile = {}
    command = str(profile.get("docs_build") or "").strip()
    record: dict[str, Any]
    if not command:
        record = {"applicable": False, "reason": NO_COMMAND}
    elif not settings.DELIVERY_TESTS_ENABLED:
        record = {
            "applicable": True,
            "command": command,
            "exit_code": None,
            "reason": TESTS_OFF,
        }
    else:
        box, unavailable = await delivery_commands.sandbox_box(ctx)
        ran, output = await delivery_commands.run_in_folder(
            ctx, doc_build.KIND, None, command, box, unavailable
        )
        record = {"applicable": True, "command": command}
        record.update({k: ran[k] for k in _KEPT if k in ran})
        record["errors"] = doc_build.first_errors(redact_secrets("docs_build", output))
        record["available"] = not doc_build.unavailable(ran.get("exit_code"), output)
    record["source"] = (profile.get("source") or {}).get("docs_build")
    evidence["docs_build"] = record
    return record


async def _definitions(
    nats_client: Any, root: str, name: str
) -> list[dict[str, Any]] | None:
    """The definitions of *name* the daemon's graph names, or ``None`` when its
    reply cannot settle it (not served, or partial and empty)."""
    reply = await workspace_daemon.daemon_request(
        nats_client,
        SUBJECT,
        {
            "workspace_root": root,
            "seeds": [],
            "lookup": {"kind": "definition", "name": name},
            "max_files": 3,
        },
        LOOKUP_TIMEOUT_SECONDS,
    )
    lookup = reply.get("lookup")
    if not isinstance(lookup, dict) or lookup.get("kind") != "definition":
        return None
    rows = [
        {k: d.get(k) for k in ("path", "sha256", "line")}
        for d in lookup.get("definitions") or []
        if isinstance(d, dict)
    ]
    if not rows and reply.get("partial"):
        return None
    return rows


async def snippet_names(
    nats_client: Any, evidence: dict[str, Any], declared: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Look up the snippets' project names and keep ``evidence["docs_snippets"]``."""
    asked = doc_snippets.wanted(evidence, declared)
    rows = asked["names"]
    if not rows:
        return None
    record: dict[str, Any] = {"names": rows, "capped": asked["capped"]}
    pending = [r for r in rows if r.get("found") is None]
    root = evidence.get("workspace_root")
    if pending and not root:
        record.update(observed=False, reason="no project folder")
    elif pending and daemon_liveness.announces(SUBJECT, FEATURE) is not True:
        record.update(
            observed=False,
            reason=f"the daemons do not all announce {FEATURE} on {SUBJECT}",
        )
    else:
        answers: dict[str, list[dict[str, Any]] | None] = {}
        for row in pending:
            name = row["name"]
            if name not in answers:
                try:
                    answers[name] = await _definitions(nats_client, root, name)
                except (
                    workspace_daemon.DaemonUnavailable,
                    workspace_daemon.DaemonError,
                ) as exc:
                    record.update(observed=False, reason=f"{SUBJECT}: {exc}"[:300])
                    break
            found = answers[name]
            row["found"] = None if found is None else bool(found)
            row["files"] = found or []
        record.setdefault("observed", True)
    evidence["docs_snippets"] = record
    return record
