"""Console producers: a terminal coding agent produces a role's task instead of the LLM call.

The idea is Aixle Flow's (Apache 2.0): run Claude Code, OpenAI Codex CLI,
Gemini CLI or Cursor CLI headless, each in a workspace of its own, and take
what it changed there. What is taken, and from where:

- the headless invocations (``console_clis.CLIS``), from Flow's adapters
  (``app/services/agents/*_adapter.rb``, ``session_command``): Claude in
  ``bypassPermissions`` mode, Codex with approvals and its own sandbox
  bypassed (``--yolo`` in Flow, the long flag here), Gemini with ``--yolo``
  and ``-p``, Cursor's agent with ``--force`` and ``-p``; the prompt as the
  command's last argument (Flow: ``"$(cat /tmp/.aixle-prompt)"``; here the
  task's prompt shell-quoted, as ``console_assistants`` quotes it);
- the quota messages each CLI prints when its provider stops it, from Flow's
  ``app/services/quota_error_detector.rb`` (``console_clis.QUOTA_PATTERNS``), feeding the
  per-company cooldown ``console_assistants`` keeps in ``AssistantRuntimeState``;
- the isolation: Flow runs each session in a container with ``/workspace``
  and bounded memory, CPU and processes; here the CLI runs in the task's Git
  worktree through ``workspace.execute`` with the company's sandbox
  (``services/sandbox.py``: ``container`` is Flow's shape) and a command
  policy that allows only the CLI's own command.

What La Fabrique adds is the evidence rule (``docs/POSITIONING.md``): the
delivery is what the daemon observes, never what the CLI says. The daemon
prepares the task's worktree (``workspace.console.prepare``,
``vscode-daemon/console_run.py``), the CLI runs, the daemon reads back every
file that differs from the worktree's ``HEAD`` with its sha256 and puts the
worktree back (``workspace.console.collect``); those texts become the task's
files (``agent["tool_edits"]``, the path the edit tool's files take), so
``workspace.generate`` writes and commits them on the task's branch and the
Verifier, the tests, the acceptance guard, the standards, the risk tier and
QA see them as any delivery's. The CLI's output is a claim, kept in the
deliverable's text; the command, exit status, duration, sandbox and files
are observations on the evidence (``console_run``, the ``console_run`` check).
A CLI that fails, times out, is refused or changes nothing is a refuted or
unverifiable run with the reason, and the task is blocked with it; one its
provider stops for quota, or that is not installed where it runs, starts a
cooldown and the model produces the task, the attempt on the board and on the
model's run (``console_producer_skipped``).

The run keeps the task worker's producer (``le-fabrique-task-worker``) with
``via: console:<cli>`` on its evidence: the review loop, the inbox, the
history checker and every reader of the company's own runs keep working on
it, and the Verifier's ``console_run`` check says who produced it.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from config import settings
from logging_config import setup_logger

from services import console_assistants, console_clis, task_projection
from services.console_clis import CLIS, check_console_run  # noqa: F401 - re-exported.
from services.glassroom import publish_glassroom

logger = setup_logger("fabrique-console-producer")


def _engine() -> Any:
    """The task worker's engine, read when used: the worker's database."""
    from services import task_worker

    return task_worker.engine


class ConsoleProducerError(RuntimeError):
    """A console producer that delivered nothing; its text goes on the board."""


def _configured(company_id: int, role: str | None) -> tuple[str | None, list[str]]:
    """The CLI the company named for *role* and the names in its ``console_env``."""
    from models import Company
    from sqlmodel import Session

    with Session(_engine()) as session:
        company = session.get(Company, company_id)
        return console_clis.producer_for(company, role), console_clis.console_env(
            company
        )


def build_prompt(
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    requirements_context: str | None = None,
    architect_advice: str | None = None,
    procedures_context: str | None = None,
) -> str:
    """The task's prompt as the CLI's instruction: what to build and how it is judged."""
    from services import edit_plan
    from services.carry_over import previous_attempt_context

    role = agent.get("role") or "Developer"
    prompt = (
        f"You are the {role} of a software team, working on one task of the "
        f"sprint '{sprint.get('name', '')}'. The current directory is a Git "
        "worktree of the task's own branch. Make the change by editing files "
        "here. Do not commit, push, change Git settings or hooks, or write "
        "outside this folder. La Fabrique reads the files you changed and "
        "verifies them (tests, standards, review); your report is a claim, "
        "not evidence.\n\n"
        f"## Task: {item.get('title', '')}\n\n{item.get('description') or ''}\n\n"
        f"Priority: {item.get('priority', 'medium')}\n"
    )
    prompt += procedures_context or ""  # The standards, then the procedures.
    prompt += requirements_context or ""
    prompt += previous_attempt_context(item)
    prompt += edit_plan.prompt_section(architect_advice)
    feedback = task_projection.record_from_dict(item).pending_rework_feedback()
    if feedback:
        prompt += (
            "\n\n## Review feedback to address\nYour previous delivery was sent "
            "back. Fix every point:\n"
            + "\n".join(f"- {text[:1500]}" for text in feedback)
        )
    return prompt[: console_clis.PROMPT_MAX_CHARS]


def start_cooldown(
    company_id: int, cli: str, result: dict[str, Any], output: str
) -> str | None:
    """The cooldown a quota stop or a missing binary starts, by kind (an
    ``AssistantRuntimeState`` row under ``console_clis.cooldown_key``, apart
    from the host adviser's)."""
    if console_clis.quota_hit(cli, output):
        minutes, kind = console_assistants.CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES, "quota"
        reset = console_assistants.parse_claude_reset_time(output)
    elif console_clis.missing_binary(result):
        minutes, kind = (
            console_assistants.ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES,
            "missing",
        )
        reset = None
    else:
        return None
    deadline = reset or datetime.now(UTC) + timedelta(minutes=max(1, minutes))
    console_assistants.set_runtime_state(
        company_id,
        console_clis.cooldown_key(cli),
        cooldown_until=deadline,
        last_error=output,
    )
    return kind


async def _telemetry(
    nats_client: Any, sprint: dict, item: dict, cli: str, **say: str
) -> None:
    await publish_glassroom(
        nats_client,
        sprint["id"],
        {
            "type": "status",
            "semantic_type": "producer.console",
            "source": "task_worker",
            "agent": "System",
            "role": "System",
            "task_id": item.get("id"),
            "task": item.get("title", "Task"),
            "producer_cli": cli,
            **say,
        },
    )


# What the daemon observed of the base when it started the task's branch
# (``vscode-daemon/git_base.py``), kept on ``console_run`` as observed.
BASE_OBSERVATIONS = (
    "base_ref",
    "base_commit",
    "base_remote",
    "base_remote_commit",
    "base_behind_remote",
    "base_ahead_of_remote",
    "base_local_commit",
    "base_not_on_remote",
    "started_from",
    "remote_unreachable",
)


async def _ask(nats_client: Any, subject: str, payload: dict, timeout: float) -> dict:
    """The daemon's answer, or one that says why there is none."""
    try:
        reply = await nats_client.request(subject, payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - No answer: said so, never a pass.
        reply = {"error": f"the workspace daemon did not reply ({type(exc).__name__})"}
    return reply if isinstance(reply, dict) else {"error": "no answer"}


async def _fail(nats_client, item, agent, sprint, run, output, root) -> NoReturn:
    """Record the run that delivered nothing (refuted or unverifiable) and stop
    the task with the reason."""
    from services.task_worker import _record_task_run

    agent.update(console_run=run, tool_edits=[], served_model=None, via=run["via"])
    evidence = {"workspace_root": root, "reason": run["reason"]}
    text = console_clis.deliverable_text(run, output)
    recorded = await asyncio.to_thread(
        _record_task_run, item, agent, sprint, text, [], evidence
    )
    message = f"{run['label']} delivered nothing: {run['reason']}"
    await _telemetry(
        nats_client,
        sprint,
        item,
        run["cli"],
        message=message,
        producer_decision="failed",
    )
    note = f" — evidence run #{recorded[0]} ({recorded[1]})" if recorded else ""
    raise ConsoleProducerError(
        f"console producer {run['cli']} delivered nothing: {run['reason']}{note}"
    )


async def _skip(nats_client, item, agent, sprint, cli: str, why: str, **extra) -> None:
    """The model produces the task: the board, the model's run (its evidence's
    ``console_producer_skipped``) and the Glassroom say why."""
    from services.task_worker import _update_item_status

    label = CLIS[cli]["label"]
    agent["console_skipped"] = {
        "cli": cli,
        "via": f"console:{cli}",
        "reason": why,
        **extra,
    }
    note = f"Console producer {label} not used: {why}. The model produces this task."
    await asyncio.to_thread(
        _update_item_status,
        item["id"],
        "in_progress",
        comment_text=note,
        comment_author="System",
        kind="note",
    )
    await _telemetry(
        nats_client, sprint, item, cli, message=note, producer_decision="skipped"
    )


async def _sandbox_for(
    nats_client, item, agent, sprint, cli: str, extra_env: list[str]
) -> dict | None:
    """The company's sandbox for this task when a console producer may run in
    it (``container`` or ``bwrap``, network on), with the names in its
    ``console_env`` added, else ``None``: the model produces the task, and the
    board, the run's evidence and the Glassroom say why. The CLIs run with
    their permission prompts off (``console_clis.CLIS``), so the sandbox is
    the only boundary: fail closed, never on the host."""
    from services import sandbox

    try:
        box = await asyncio.to_thread(sandbox.request_for_sprint, sprint, _engine())
        why = console_clis.sandbox_refusal(box)
    except sandbox.PolicyUnavailable as exc:
        box, why = None, f"console producers need a known sandbox: {exc}"
    if why is None:
        return console_clis.run_sandbox(box, extra_env)
    await _skip(nats_client, item, agent, sprint, cli, why)
    return None


async def _where(nats_client, item, sprint, cli: str) -> tuple | None:
    """The task's Git spec and project, or ``None`` (said in the Glassroom)
    when the CLI cools down, the Git workflow is off or there is no project."""
    from services import git_workflow
    from services.task_worker import _resolve_workspace_root

    label, company_id = CLIS[cli]["label"], sprint["company_id"]
    deadline = await asyncio.to_thread(
        console_assistants.cooldown_until, company_id, console_clis.cooldown_key(cli)
    )
    spec = await asyncio.to_thread(git_workflow.delivery_spec, _engine(), sprint, item)
    root = await asyncio.to_thread(_resolve_workspace_root, company_id)
    why = (
        f"it cools down until {deadline.isoformat()}"
        if deadline
        else "the Git workflow is off"
        if not spec
        else "the company has no project folder"
        if not root
        else None
    )
    if why is None:
        return spec, root
    message = f"{label} not used: {why}; the model produces this task."
    await _telemetry(
        nats_client, sprint, item, cli, message=message, producer_decision="skipped"
    )
    return None


async def produce(
    nats_client: Any,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    requirements_context: str | None = None,
    architect_advice: str | None = None,
) -> str | None:
    """The deliverable a console producer made for *item*, or ``None`` when the
    model produces it (no CLI named for the role, the CLI cooling down, no Git
    workflow, no project, or a sandbox that is not ``container`` or ``bwrap``
    with network on, or a quota stop or a missing CLI just now). Raises
    ``ConsoleProducerError`` after recording a run that delivered nothing."""
    from services.task_worker import _load_procedures_context

    company_id, role = sprint["company_id"], agent.get("role")
    cli, extra_env = await asyncio.to_thread(_configured, company_id, role)
    where = await _where(nats_client, item, sprint, cli) if cli else None
    if where is None:
        return None
    git_spec, workspace_root = where
    box = await _sandbox_for(nats_client, item, agent, sprint, cli, extra_env)
    if box is None:
        return None
    procedures = await asyncio.to_thread(_load_procedures_context, company_id, role)
    prompt = build_prompt(
        item, agent, sprint, requirements_context, architect_advice, procedures
    )
    sent = console_clis.sent_prompt(cli, prompt)
    run: dict[str, Any] = {
        "via": f"console:{cli}",
        "cli": cli,
        "label": CLIS[cli]["label"],
        "command": console_clis.command_prefix(cli),
        "branch": git_spec["branch"],
        # What the CLI is given, after the command line's bound: what was sent.
        "prompt_sha256": hashlib.sha256(sent.encode()).hexdigest(),
        "prompt_chars": len(sent),
        "prompt_cut": sent.endswith(console_clis.CUT_NOTE),
        "exit_code": None,
        "duration_seconds": None,
    }
    message = f"{run['label']} is producing: {item.get('title', 'task')}"
    await _telemetry(
        nats_client, sprint, item, cli, message=message, producer_decision="requested"
    )
    io_timeout = settings.WORKSPACE_WRITE_TIMEOUT_SECONDS * 2
    prepared = await _ask(
        nats_client,
        "workspace.console.prepare",
        {"workspace_root": workspace_root, "git": git_spec},
        io_timeout,
    )
    if not prepared.get("ok"):
        run.update(
            kind="not_run",
            outcome="failed",
            reason=f"the task's worktree could not be prepared: {prepared.get('error')}",
        )
        await _fail(nats_client, item, agent, sprint, run, "", workspace_root)
    worktree, head = prepared["worktree"], prepared["head"]
    run.update(worktree=worktree, head=head, merged=prepared.get("merged") or [])
    run.update({k: prepared[k] for k in BASE_OBSERVATIONS if k in prepared})

    timeout = int(settings.CONSOLE_PRODUCER_TIMEOUT_SECONDS)
    started = time.monotonic()
    result = await _ask(
        nats_client,
        "workspace.execute",
        {
            "workspace_root": worktree,
            "command": console_clis.render_command(cli, sent),
            "sprint_id": sprint["id"],
            "sandbox": box,
            "command_policy": console_clis.command_policy(cli),
            "timeout_seconds": timeout,
        },
        timeout + 60,
    )
    run["duration_seconds"] = round(time.monotonic() - started, 1)
    run.update(
        {k: result[k] for k in ("exit_code", "timed_out", "sandbox") if k in result}
    )
    output = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".strip()

    # Whatever the run did, read back what changed and put the worktree back.
    collected = await _ask(
        nats_client,
        "workspace.console.collect",
        {"workspace_root": worktree, "head": head, "guard": prepared.get("guard")},
        io_timeout,
    )
    observed, deliver = console_clis.observed_files(collected)
    run.update(
        files=observed,
        committed_by_cli=bool(collected.get("committed_by_cli")),
        truncated=bool(collected.get("truncated")),
        daemon_refused=list(collected.get("refused") or []),
    )
    failure = console_clis.failure(result, collected, deliver)
    if failure:
        # The daemon's alarm is never cooled down nor handed to the model.
        cooldown = None
        if not run["daemon_refused"]:
            cooldown = await asyncio.to_thread(
                start_cooldown, company_id, cli, result, output
            )
        run.update(
            kind=failure[0], outcome="failed", reason=failure[1], cooldown=cooldown
        )
        if cooldown:
            # What the cooldown is for: the model produces this task too.
            why = (
                "its provider stopped it for quota"
                if cooldown == "quota"
                else "it is not installed where it runs"
            )
            await _skip(
                nats_client,
                item,
                agent,
                sprint,
                cli,
                f"{why} ({failure[1]}); it cools down",
                attempt=run,
            )
            return None
        await _fail(nats_client, item, agent, sprint, run, output, worktree)

    run["outcome"] = "changed"
    edits = [
        {
            "path": f["path"],
            "content": f["content"],
            "sha256": f.get("sha256"),
            "changed": True,
            "source": "console_producer",
        }
        for f in deliver
    ]
    agent.update(
        console_run=run,
        via=run["via"],
        served_model=None,
        finish_reason=None,
        tool_edits=edits,
    )
    message = f"{run['label']} changed {len(deliver)} file(s) for: {item.get('title', 'task')}"
    await _telemetry(
        nats_client, sprint, item, cli, message=message, producer_decision="used"
    )
    return console_clis.deliverable_text(run, output)
