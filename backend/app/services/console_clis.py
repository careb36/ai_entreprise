"""The console producers' catalogue and their record: what runs, and how it is judged.

Each CLI's headless command is taken from Aixle Flow's adapters (Apache 2.0,
``app/services/agents/*_adapter.rb``, ``session_command``) and the quota
messages from its ``app/services/quota_error_detector.rb``. Pure functions:
the run itself is ``services/console_producer.py``.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from services import console_assistants

# The CLIs a company may name, headless (Flow's adapters, ``session_command``).
CLIS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code",
        "argv": ["claude", "-p", "--permission-mode", "bypassPermissions"],
        "provider": "anthropic",
    },
    "codex": {
        "label": "OpenAI Codex CLI",
        "argv": [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ],
        "provider": "openai",
    },
    "gemini": {
        "label": "Gemini CLI",
        "argv": ["gemini", "--yolo", "-p"],
        "provider": "gemini",
    },
    "cursor": {
        "label": "Cursor CLI",
        "argv": ["cursor-agent", "--print", "--force", "--disable-auto-update"],
        "provider": "cursor",
    },
}
# The roles whose tasks deliver files through the task worker.
ROLES = ("Developer", "QA", "SecOps", "TechWriter")
# One argument may hold 128 KiB on Linux; the prompt stays under it.
PROMPT_MAX_CHARS = 100_000
CLAIM_EXCERPT_CHARS = 4000
MISSING_EXIT_CODES = {126, 127}
# Flow's quota_error_detector.rb, per provider (Apache 2.0).
QUOTA_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    provider: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
    for provider, patterns in {
        "anthropic": (
            r"credit balance(?:\s+is)?\s+too low",
            r"insufficient_quota",
            r"billing_hard_limit",
            r"individual spend limit",
            r"workspace API usage limits",
            r"You will regain access on",
            r"You['’]ve hit your (?:5-hour|session|weekly|usage credit) limit",
        ),
        "gemini": (
            r"Usage limit reached",
            r"RESOURCE_EXHAUSTED",
            r"Quota exceeded for metric:.*generativelanguage",
            r"You exceeded your current quota.*generativelanguage",
            r"\[API Error: got status: 429",
        ),
        "openai": (
            r"You exceeded your current quota",
            r"insufficient_credits",
            r"quota_exceeded",
            r"You['’]ve hit your usage limit",
        ),
        "cursor": (
            r"reached your normal usage limit",
            r"you'?re out of usage",
            r"usage limit has been reached",
        ),
    }.items()
}


def validate(producers: Any, git_workflow: bool) -> dict[str, str]:
    """The company's ``{role: cli}``, empty values dropped; a ``ValueError``
    (or ``TypeError`` for a body that is not an object) says why not."""
    if not isinstance(producers, dict):
        raise TypeError("producers must be an object of role to CLI")
    chosen: dict[str, str] = {}
    for role, cli in producers.items():
        value = str(cli or "").strip().lower()
        if not value:
            continue
        if role not in ROLES:
            raise ValueError(f"not a role a console producer can work for: {role}")
        if value not in CLIS:
            raise ValueError(f"not a console producer: {cli}")
        chosen[role] = value
    if chosen and not git_workflow:
        raise ValueError(
            "a console producer needs the Git workflow: it works in the task's "
            "worktree, never in the person's checkout"
        )
    return chosen


def producer_for(company: Any, role: str | None) -> str | None:
    """The CLI the company named for *role*, else ``None``."""
    from services.company_models import role_key

    producers = getattr(company, "console_producers", None)
    if not isinstance(producers, dict) or not role:
        return None
    cli = str(producers.get(role_key(role)) or "").strip().lower()
    return cli if cli in CLIS else None


def command_prefix(cli: str) -> str:
    return shlex.join(CLIS[cli]["argv"])


# What the daemon's command policy refuses to read anywhere in a command,
# quotes or not (``vscode-daemon/command_policy.py``, ``_unsupported``): a
# word that starts with ``#``, ``<<``, ``$'`` and ``$"``. The prompt carries
# them as look-alikes the CLI's model reads the same.
_POLICY_UNREADABLE = (
    (re.compile(r"(^|[\s;&|()])#"), "\\1\uff03"),
    (re.compile(r"<<"), "\u00ab"),
    (re.compile(r"\$(['\"])"), "\uff04\\1"),
)


def policy_readable(prompt: str) -> str:
    for pattern, replacement in _POLICY_UNREADABLE:
        prompt = pattern.sub(replacement, prompt)
    return prompt


def render_command(cli: str, prompt: str) -> str:
    """The CLI's headless command with the prompt shell-quoted as its last
    argument (as ``console_assistants`` quotes it), readable by the policy."""
    text = policy_readable(prompt[:PROMPT_MAX_CHARS])
    return f"{command_prefix(cli)} {shlex.quote(text)}"


def command_policy(cli: str) -> dict[str, Any]:
    """Only the CLI's own command runs (``vscode-daemon/command_policy.py``)."""
    return {"mode": "allow", "allow": [command_prefix(cli)]}


def quota_hit(cli: str, output: str) -> bool:
    patterns = QUOTA_PATTERNS.get(CLIS[cli]["provider"], ())
    return any(p.search(output or "") for p in patterns) or (
        cli == "claude"
        and any(
            t in (output or "").lower()
            for t in console_assistants.CLAUDE_QUOTA_PATTERNS
        )
    )


def missing_binary(result: dict[str, Any]) -> bool:
    if result.get("exit_code") in MISSING_EXIT_CODES:
        return True
    text = f"{result.get('stderr') or ''}{result.get('error') or ''}".lower()
    return any(t in text for t in console_assistants.ASSISTANT_UNAVAILABLE_PATTERNS)


def failure(
    result: dict[str, Any], collected: dict[str, Any], files: list
) -> tuple[str, str] | None:
    """``(kind, reason)`` when the run delivers nothing: ``not_run`` (unverifiable) or ``failed`` (refuted)."""
    if result.get("refused"):
        return "not_run", f"the command policy refused the CLI: {result.get('error')}"
    if result.get("timed_out"):
        return (
            "failed",
            f"the CLI ran past its {result.get('timeout_seconds')} s timeout",
        )
    if missing_binary(result):
        return "not_run", "the CLI is not installed on the daemon's machine"
    if result.get("exit_code") is None and not result.get("success"):
        return "not_run", f"the daemon could not run the CLI: {result.get('error')}"
    if not result.get("success"):
        return "failed", f"the CLI failed (exit {result.get('exit_code')})"
    if collected.get("refused"):
        return "failed", "; ".join(collected["refused"])
    if not collected.get("ok"):
        return (
            "not_run",
            f"the daemon could not read what the CLI changed: {collected.get('error')}",
        )
    if not files:
        return (
            "failed",
            "the CLI reported success but changed no file La Fabrique delivers",
        )
    return None


def check_console_run(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's record of a console producer's run: who ran, how it ended, what it changed."""
    run = (evidence or {}).get("console_run")
    if not isinstance(run, dict):
        return None
    files = [f for f in run.get("files") or [] if isinstance(f, dict)]
    delivered = [f["path"] for f in files if f.get("delivered")]
    how = f"{run.get('label') or run.get('cli')} (exit {run.get('exit_code')}, {run.get('duration_seconds')} s)"
    if run.get("outcome") == "changed":
        outcome, text = (
            "pass",
            f"{how} changed {len(delivered)} file(s): {', '.join(delivered)}",
        )
    else:
        outcome = "unverifiable" if run.get("kind") == "not_run" else "refuted"
        text = f"{how}: {run.get('reason')}"
    return {
        "name": "console_run",
        "outcome": outcome,
        "observation": text,
        "via": run.get("via"),
        "command": run.get("command"),
        "exit_code": run.get("exit_code"),
        "duration_seconds": run.get("duration_seconds"),
    }


def claim(output: str) -> str:
    """The CLI's own report, quoted, with no fence a manifest parser could read as a file."""
    text = (output or "").strip()[-CLAIM_EXCERPT_CHARS:].replace("```", "'''")
    return "\n".join(f"> {line}" for line in text.splitlines()) or "> (no output)"


def deliverable_text(run: dict[str, Any], output: str) -> str:
    files = run.get("files") or []
    lines = [
        (
            f"Console producer: {run['label']} (`{run['cli']}`) ran headless in the "
            f"task's worktree on {run.get('branch')} (exit {run.get('exit_code')}, "
            f"{run.get('duration_seconds')} s)."
        )
    ]
    if run.get("outcome") != "changed":
        lines.append(f"\nNothing was delivered: {run.get('reason')}.")
    lines.append("\n## Files the daemon observed it change")
    for f in files:
        state = (
            "delivered"
            if f.get("delivered")
            else f"not delivered: {f.get('reason') or f.get('status')}"
        )
        lines.append(f"- {f.get('path')} ({f.get('change')}, {state})")
    if not files:
        lines.append("- none")
    lines.append("\n## The CLI's own report (a claim, not evidence)")
    lines.append(claim(output))
    return "\n".join(lines)


def observed_files(collected: dict[str, Any]) -> tuple[list, list]:
    """Each file the daemon read back, with whether it is delivered; the ones that are."""
    from services import workspace_edits

    suffixes = workspace_edits.delivery_suffixes()
    observed, deliver = [], []
    for f in collected.get("files") or []:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str):
            continue
        entry = {
            k: f.get(k)
            for k in ("path", "change", "status", "bytes", "sha256", "reason")
        }
        name = f["path"].rsplit("/", 1)[-1]
        suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        ok = f.get("status") == "changed" and isinstance(f.get("content"), str)
        if ok and suffix not in suffixes:
            ok, entry["reason"] = False, "a delivery does not write such files"
        entry["delivered"] = ok
        observed.append(entry)
        if ok:
            deliver.append(f)
    return observed, deliver
