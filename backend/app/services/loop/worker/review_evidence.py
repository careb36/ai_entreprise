"""What the platform observed about a delivery, for QA's review (issue #766).

QA was never shown the Verifier's checks: it judged the agent's message and
the branch, and could approve a delivery whose tests had failed or never
run, or send back one the tests had passed for reasons the tests settle
(``docs/design/target/05-acceptance.md``). ``section`` gives the reviewer
the latest verification of the task's latest run: the verdict, each check's
outcome and observation, and the end of the output of the project's test run
and of each folder's suite, all as the platform recorded them.

They are observations, not the reviewer's to overrule: the text says a
refuted or unverifiable check is not settled by the review, so QA reviews
what the tests do not check (scope, the criteria, the design).
"""

from __future__ import annotations

from typing import Any

from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from sqlmodel import Session, select

OBSERVATION_CHARS = 300
TAIL_CHARS = 1500
MAX_CHECKS = 20

HEADER = "--- WHAT THE PLATFORM OBSERVED (the Verifier's checks, not a claim) ---"
NOTE = (
    "These checks were run by the platform, not reported by the agent. A "
    "refuted or unverifiable check is not settled by your review: say what "
    "it means for the task if it matters, and review what the checks do not "
    "cover (scope, the acceptance criteria, the design, tests that assert "
    "nothing)."
)


def latest(session: Session, company_id: int, item_id: int) -> Verification | None:
    """The latest verification of the task's latest task-worker run."""
    run = session.exec(
        select(AgentRun)
        .where(
            AgentRun.company_id == company_id,
            AgentRun.external_ref == f"task-{item_id}",
            AgentRun.producer == TASK_WORKER_PRODUCER,
        )
        .order_by(AgentRun.version.desc())
    ).first()
    if run is None:
        return None
    return session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run.id)
        .order_by(Verification.id.desc())
    ).first()


def _tail(text: Any) -> str:
    return str(text or "").strip()[-TAIL_CHARS:]


def render(verdict: str | None, checks: list[Any], evidence: dict[str, Any]) -> str:
    """The section's text from a verification's parts; empty without checks."""
    rows = [c for c in checks or [] if isinstance(c, dict)]
    if not rows and not evidence.get("test_run"):
        return ""
    lines = [HEADER, f"Verdict: {verdict or 'none'}"]
    for check in rows[:MAX_CHECKS]:
        observation = str(check.get("observation") or "")[:OBSERVATION_CHARS]
        lines.append(
            f"- {check.get('name')}: {check.get('outcome')}"
            + (f" — {observation}" if observation else "")
        )
    if len(rows) > MAX_CHECKS:
        lines.append(f"- … and {len(rows) - MAX_CHECKS} more")
    run = evidence.get("test_run") or {}
    if run.get("command"):
        lines.append(
            f"\nTest run `{run['command']}` exited {run.get('exit_code')}; "
            f"end of its output:\n{_tail(run.get('output_tail')) or '(no output)'}"
        )
    elif run.get("reason"):
        lines.append(f"\nNo test run at the root: {run['reason']}")
    for record in evidence.get("command_runs") or []:
        if not isinstance(record, dict) or record.get("kind") != "suite":
            continue
        folder = record.get("folder") or "."
        lines.append(
            f"\nSuite in {folder}/ `{record.get('command')}` exited "
            f"{record.get('exit_code')}; end of its output:\n"
            f"{_tail(record.get('output_tail')) or '(no output)'}"
        )
    lines += ["", NOTE, "--- END OF OBSERVATIONS ---"]
    return "\n".join(lines)


def section(session: Session, company_id: int, item_id: int) -> str:
    """The section for the review prompt, starting with blank lines; empty
    when the task has no verification yet."""
    verification = latest(session, company_id, item_id)
    if verification is None:
        return ""
    text = render(
        verification.verdict, verification.checks or [], verification.evidence or {}
    )
    return f"\n\n{text}" if text else ""
