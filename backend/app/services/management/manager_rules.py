"""The checks behind each rule a company's ``ManagerPolicy`` may name
(issues #331 and #394).

Each check reads only the facts the platform observed about an inbox item
(``services/management/manager_inbox.py`` and ``services/management/manager_inbox_extra.py``, plus
the probes of ``services/management/manager_observe.py``): the Verifier's verdict and
checks, the risk tier, the size, the criteria tests, the ScrumMaster's
triage, the specification and architecture checks, a remote, a credit call.
Never the model's judgement. A fact the platform could not observe (``None``)
never lets a rule hold: unknown is not absent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

LOW_TIERS = ("low", "medium")
# Sprint statuses a sent-back task can be worked in.
OPEN_SPRINTS = ("active", "needs_split", "planned", "paused")


def _size(files: Any, limit: int) -> str | None:
    if files is None or files > limit:
        shown = files if files is not None else "unknown"
        return f"{shown} files, over the limit of {limit}"
    return None


def _verified_low(facts: dict[str, Any], label: str = "latest run") -> str | None:
    if facts.get("verdict") != "verified":
        return f"the {label} is {facts.get('verdict') or 'not verified'}"
    if facts.get("risk_tier") not in LOW_TIERS:
        return f"the risk tier is {facts.get('risk_tier') or 'unknown'}"
    return None


def _protected(facts: dict[str, Any]) -> str | None:
    """A protected path (``docs/design/rsi-loop.md``, REQ-11): a person reviews
    it through the normal review; no rule accepts it."""
    touched = facts.get("protected_paths")
    if touched:
        shown = ", ".join(str(p) for p in touched[:10])
        return f"it touches protected paths ({shown}): a person reviews it"
    return None


def _accept_delivery(facts, policy) -> str | None:
    if facts.get("frozen"):
        return "an operator froze the task"
    why = _protected(facts)
    if why:
        return why
    if not facts.get("human_led_hold"):
        return "the hold is not only the autonomy level's"
    why = _verified_low(facts) or _size(facts.get("files"), policy.max_files)
    if why:
        return why
    if (facts.get("criteria") or 0) > policy.max_criteria:
        return f"{facts.get('criteria')} criteria, over the limit"
    return None


def _adopt_procedure(facts, policy) -> str | None:
    if facts.get("autonomy_level") != 3:
        return f"autonomy level {facts.get('autonomy_level')}, not 3"
    if facts.get("reverted_for_role"):
        return "the board reverted an earlier procedure for this role"
    return None


def _approve_run(facts, policy) -> str | None:
    return _verified_low(facts, "latest verification") or _size(
        facts.get("files"), policy.max_files
    )


def _done_branch(facts, policy) -> str | None:
    """A done task's branch: QA approved, verified, low or medium, small."""
    if not facts.get("has_branch"):
        return "the delivery was not made on a Git branch"
    if not facts.get("qa_approved"):
        return "QA did not approve the task"
    return _verified_low(facts) or _size(facts.get("files"), policy.max_files)


WAITING_FOR_INTEGRATION_REVIEW = (
    "waiting for a person's review of the integration before merging it locally"
)


WAITING_FOR_PROTECTED_ACK = (
    "the integration changes protected paths: waiting for an Owner to "
    "acknowledge each by name in an approving review"
)


def _merge_local(facts, policy) -> str | None:
    if facts.get("merged"):
        return "the branch is merged already"
    if facts.get("target") == "integration":
        status = facts.get("integration_status")
        if status != "tests_passed":
            return f"the integration is {status or 'unknown'}, not tests_passed"
        # The company's review gate (F7): observed from the review records,
        # never from the agent's answer.
        if facts.get("integration_review_required") and not facts.get(
            "integration_approved"
        ):
            return WAITING_FOR_INTEGRATION_REVIEW
        # The merge guard's refusal (rsi-loop REQ-13), kept on the integration.
        touched = facts.get("protected_paths")
        if touched and not facts.get("protected_paths_acked"):
            shown = ", ".join(str(p) for p in touched[:10])
            return f"{WAITING_FOR_PROTECTED_ACK} ({shown})"
        return None
    return _done_branch(facts, policy)


def _undo_delivery(facts, policy) -> str | None:
    if facts.get("verdict") != "refuted":
        return f"the latest verification is {facts.get('verdict') or 'missing'}"
    if facts.get("on_branch") is not False:
        return "the delivery was made on a Git branch: discard the branch"
    if not facts.get("has_snapshot"):
        return "the daemon kept no snapshot of the files before the delivery"
    if facts.get("person_touched") is not False:
        return "a person worked on the task after the verification"
    return None


def _blocked(facts) -> str | None:
    if not facts.get("blocked"):
        return "the task is not blocked"
    if facts.get("frozen"):
        return "an operator froze the task"
    if facts.get("carried"):
        return "the task was carried into another sprint"
    return None


def _send_back_blocked(facts, policy) -> str | None:
    why = _blocked(facts)
    if why:
        return why
    if facts.get("triage_decision") != "retry":
        return "the ScrumMaster's triage did not choose another attempt"
    if not facts.get("triage_brief"):
        return "the ScrumMaster's triage gave no brief for the next attempt"
    if facts.get("sprint_status") not in OPEN_SPRINTS:
        return f"the sprint is {facts.get('sprint_status') or 'unknown'}"
    return None


def _carry_over(facts, policy) -> str | None:
    why = _blocked(facts)
    if why:
        return why
    if facts.get("second_attempt"):
        return "the task is itself a second attempt: it stays for a person"
    if facts.get("triage_decision") == "person":
        return "the ScrumMaster's triage left the task for a person"
    if facts.get("marked_for_carry_over"):
        return "the task is marked for carry-over already"
    return None


def _answer_question(facts, policy) -> str | None:
    if not facts.get("open_question"):
        return "no open question"
    if not facts.get("answer_known"):
        return "no open question has its answer in an approved document"
    return None


def _publish(facts, policy) -> str | None:
    if not policy.publish_enabled or not facts.get("publishing_on"):
        return "the client has not turned publishing on"
    if facts.get("published"):
        return "the branch is published already"
    why = _done_branch(facts, policy)
    if why:
        return why
    if facts.get("has_remote") is not True:
        return "the repository has no remote to publish to"
    return None


def _resume_after_credit(facts, policy) -> str | None:
    if facts.get("sprint_status") != "paused" or not facts.get("paused_for_credit"):
        return "the sprint is not paused for lack of credit"
    if facts.get("balance_ok") is not True:
        return "the provider's credit check has not passed"
    return None


def _approve_specification(facts, policy) -> str | None:
    if facts.get("blocking_findings") != 0:
        return f"{facts.get('blocking_findings')} finding(s) block the approval"
    if facts.get("open_questions") != 0:
        return f"{facts.get('open_questions')} open question(s)"
    if facts.get("all_requirements_have_criteria") is not True:
        return "a requirement has no acceptance criterion, or there is none"
    return None


def _approve_architecture(facts, policy) -> str | None:
    if facts.get("blocking_findings") != 0:
        return f"{facts.get('blocking_findings')} finding(s) block the approval"
    if facts.get("open_questions") != 0:
        return f"{facts.get('open_questions')} open question(s)"
    if facts.get("specification_status") != "approved":
        return "its specification is not the approved one"
    if facts.get("covers_all_requirements") is not True:
        return "it does not cover every requirement of the specification"
    return None


def _accept_high_risk(facts, policy) -> str | None:
    if facts.get("frozen"):
        return "an operator froze the task"
    why = _protected(facts)
    if why:
        return why
    if facts.get("verdict") != "verified":
        return f"the latest run is {facts.get('verdict') or 'not verified'}"
    if not facts.get("criteria"):
        return "the task covers no criterion: no criterion test speaks for it"
    if facts.get("all_criterion_tests_pass") is not True:
        return "not every criterion test the task covers passed"
    if facts.get("standards_secret") is not False:
        return "the standards check found a secret, or did not run"
    return _size(facts.get("files"), policy.max_files_high_risk)


CHECKS: dict[str, Callable[[dict[str, Any], Any], str | None]] = {
    "accept_delivery": _accept_delivery,
    "adopt_procedure": _adopt_procedure,
    "approve_run": _approve_run,
    "merge_local": _merge_local,
    "undo_delivery": _undo_delivery,
    "send_back_blocked": _send_back_blocked,
    "carry_over": _carry_over,
    "answer_question": _answer_question,
    "publish": _publish,
    "resume_after_credit": _resume_after_credit,
    "approve_specification": _approve_specification,
    "approve_architecture": _approve_architecture,
    "accept_high_risk": _accept_high_risk,
}


def rule_holds(rule: str, facts: dict[str, Any], policy: Any) -> str | None:
    """Why *rule* does not hold for *facts*, or ``None`` when it does."""
    check = CHECKS.get(rule)
    if check is None:
        return "unknown rule"
    return check(facts, policy)
