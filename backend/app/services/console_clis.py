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
#
# Flow runs every one of them inside a container, which is why it turns their
# permission prompts off. The same holds here: these commands run only in the
# company's ``container`` or ``bwrap`` sandbox (``sandbox_refusal``), which is
# the boundary; on the ``host`` sandbox they would act on the person's
# computer with their home folder, credentials and network, so the host is
# refused and the model produces the task instead.
# - claude: ``-p`` headless; ``bypassPermissions`` is acceptable only because
#   the sandbox is the boundary.
# - codex: ``exec --full-auto`` keeps Codex's own sandbox (workspace-write,
#   no approval prompts), inside ours; never the flag that bypasses both.
# - gemini ``--yolo`` and cursor ``--force``: auto-approval, acceptable only
#   inside the sandbox, for the same reason as claude.
CLIS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code",
        "argv": ["claude", "-p", "--permission-mode", "bypassPermissions"],
        "provider": "anthropic",
    },
    "codex": {
        "label": "OpenAI Codex CLI",
        "argv": ["codex", "exec", "--full-auto", "--skip-git-repo-check"],
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


# The sandbox modes a console producer may run in (``services/sandbox.py``).
SANDBOX_MODES = ("container", "bwrap")


def sandbox_refusal(box: dict[str, Any] | None) -> str | None:
    """Why a console producer may not run in the sandbox *box*, else ``None``."""
    mode = (box or {}).get("mode") or "host"
    if mode not in SANDBOX_MODES:
        return (
            "console producers need a container or bwrap sandbox "
            f"(the company's sandbox is {mode}, and the CLI runs with its "
            "permission prompts off)"
        )
    if not (box or {}).get("network"):
        return (
            "console producers need the sandbox's network on "
            "(DELIVERY_SANDBOX_NETWORK) to reach the CLI's provider"
        )
    return None


def validate_env(names: Any) -> list[str]:
    """The company's ``console_env``: the variable names only its console
    producers' runs see (the provider's key), never its delivery tests,
    installs or application checks; a ``ValueError`` (a ``TypeError`` for a body that is
    not a list) says why not."""
    from services.sandbox import valid_env_name

    if not isinstance(names, list):
        raise TypeError("env must be a list of environment variable names")
    chosen: list[str] = []
    for name in names:
        value = str(name or "").strip()
        if not value:
            continue
        if not valid_env_name(value):
            raise ValueError(f"not an environment variable name: {name}")
        if value not in chosen:
            chosen.append(value)
    return chosen


def console_env(company: Any) -> list[str]:
    """The names in the company's ``console_env``."""
    names = getattr(company, "console_env", None) or []
    return [str(n) for n in names if isinstance(n, str) and n.strip()]


def run_sandbox(box: dict[str, Any], extra: list[str]) -> dict[str, Any]:
    """The sandbox of a console producer's run: the company's, plus the names
    in its ``console_env`` (``sandbox_env`` alone reaches the other runs)."""
    return {**box, "env": list(dict.fromkeys([*(box.get("env") or []), *extra]))}


def cooldown_key(cli: str) -> str:
    """The ``AssistantRuntimeState`` key of a console producer's cooldown, apart
    from the host console adviser's (``console_assistants``): a CLI missing
    from the sandbox image, or a quota on the key the sandbox sees, says
    nothing about the CLI on the host."""
    return f"producer:{cli}"


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
    # A trailing ``$`` would meet the closing quote ``shlex.quote`` adds: ``$'``.
    (re.compile(r"\$\Z"), "\uff04"),
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
    """The shell could not find (126/127) the CLI, or the daemon could not
    spawn anything (no exit status) and said so; never a run that exited 0,
    whatever its output says."""
    code = result.get("exit_code")
    if code in MISSING_EXIT_CODES:
        return True
    if code is not None or result.get("success"):
        return False
    text = f"{result.get('stderr') or ''}{result.get('error') or ''}".lower()
    return any(t in text for t in console_assistants.ASSISTANT_UNAVAILABLE_PATTERNS)


def failure(
    result: dict[str, Any], collected: dict[str, Any], files: list
) -> tuple[str, str] | None:
    """``(kind, reason)`` when the run delivers nothing: ``not_run`` (unverifiable) or ``failed`` (refuted).

    The daemon's alarm (the metadata changed, the worktree's ``.git`` was
    rewritten) wins over every other outcome."""
    if collected.get("refused"):
        return "failed", "; ".join(str(r) for r in collected["refused"])
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
    if not collected.get("ok"):
        return (
            "not_run",
            f"the daemon could not read what the CLI changed: {collected.get('error')}",
        )
    partial = partial_reason(collected)
    if partial:
        return "failed", partial
    if not files:
        return (
            "failed",
            "the CLI reported success but changed no file La Fabrique delivers",
        )
    return None


def partial_reason(collected: dict[str, Any]) -> str | None:
    """Why what the daemon read back is not the CLI's result as it ran, else
    ``None``: a listing cut at its bound (nobody knows what was left out), a
    text past the reply's bound, or a deletion (the old path of a rename too),
    which a delivery cannot make. Delivering the rest would hand the Verifier
    and QA a tree the CLI never produced."""
    if collected.get("truncated"):
        return "the CLI changed more files than the daemon reads back; nothing is delivered from a partial listing"
    files = [f for f in collected.get("files") or [] if isinstance(f, dict)]
    cut = [str(f.get("path")) for f in files if f.get("status") == "past_bound"]
    if cut:
        return f"the changes exceed the reply's bound ({', '.join(cut[:5])}); nothing is delivered in part"
    gone = [str(f.get("path")) for f in files if f.get("change") == "deleted"]
    if gone:
        return f"the CLI deleted or renamed {', '.join(gone[:5])}, which a delivery cannot apply"
    return None


def check_console_run(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Verifier's record of a console producer's run: who ran, how it ended, what it changed."""
    run = (evidence or {}).get("console_run")
    if not isinstance(run, dict):
        return None
    files = [f for f in run.get("files") or [] if isinstance(f, dict)]
    delivered = [f["path"] for f in files if f.get("delivered")]
    how = f"{run.get('label') or run.get('cli')} (exit {run.get('exit_code')}, {run.get('duration_seconds')} s)"
    left = [
        f"{f.get('path')} ({f.get('reason') or f.get('status')})"
        for f in files
        if not f.get("delivered")
    ]
    if run.get("outcome") == "changed":
        outcome, text = (
            "pass",
            f"{how} changed {len(delivered)} file(s): {', '.join(delivered)}",
        )
        if left:
            text += f"; {len(left)} change(s) not delivered: {', '.join(left[:10])}"
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
        "not_delivered": left,
        "daemon_refused": run.get("daemon_refused") or [],
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
