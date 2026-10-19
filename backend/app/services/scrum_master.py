"""
The ScrumMaster answers escalations: the triage of a blocked task.

When a gate or a reviewer gives up on a task it is blocked and escalated to
the ScrumMaster. Here the ScrumMaster answers, as in a company: read what
blocked it and decide whether the task deserves another attempt in the next
sprint, with a brief saying what that attempt must do differently, or whether
it is better left for a person. The answer is asked for in a fixed format a
parser checks; anything else is no decision.

The decision is a message (``AgentMessage``, kind ``decision``), so it is a
claim: it changes what the company does next, never what the evidence says
about the task. ``carry_over`` reads it: a task the ScrumMaster left for a
person is not carried over on the company's own initiative, and the brief
of one it sent back travels with the new attempt into the agent's prompt.
Without a decision (the call failed, the answer was unusable) the company
keeps the deterministic default: one more attempt, then a person.
"""

import os
import re
from typing import Any

from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from models import Agent, AgentMessage, BacklogItem
from sqlmodel import Session, select

from llm.model_factory import default_model_for_role
from services import procedures
from services.board import SCRUM_MASTER_ROLE, comment_list, is_rework_request

logger = setup_logger("fabrique-scrum-master")

DECISION_RETRY = "retry"
DECISION_PERSON = "person"
TRIAGE_PREFIX = "Triage decision: "
RETRY_LINE = TRIAGE_PREFIX + "another attempt in the next sprint."
PERSON_LINE = TRIAGE_PREFIX + "leave it for a person."
WHY_PREFIX = "Why: "
BRIEF_PREFIX = "Brief for the next attempt: "
BRIEF_LIMIT = 600
WHY_LIMIT = 300
FEEDBACK_SHOWN = 3
FEEDBACK_CHARS = 400
TIMEOUT_SECONDS = 90

_ANSWER = re.compile(
    r"DECISION:\s*(?P<decision>retry|person)\b"
    r"(?P<rest>.*?)(?:^|\n)\s*END\b",
    re.IGNORECASE | re.DOTALL,
)
_FIELD = re.compile(r"^(?P<key>BRIEF|WHY):\s*(?P<value>.*?)\s*$", re.MULTILINE)


class ScrumMasterError(RuntimeError):
    """The ScrumMaster's call produced no usable answer."""


# ---------------------------------------------------------------------------
# The question and its answer
# ---------------------------------------------------------------------------


def answer_format() -> str:
    """The format the ScrumMaster is asked to answer in; ``parse_answer`` reads it."""
    return (
        "Answer exactly in this form and nothing else:\n\n"
        "DECISION: <retry or person>\n"
        "BRIEF: <only for retry: what the next attempt must do differently, concrete "
        f"and short (at most {BRIEF_LIMIT} characters); one line>\n"
        "WHY: <one line>\n"
        "END\n\n"
        "Choose retry when the brief can realistically fix what blocked the task "
        "(a format or scope problem, a missing part, a misread requirement). Choose "
        "person when the task needs a decision, access or information an agent does "
        "not have, or when the attempts show the task itself is wrong."
    )


def triage_prompt(item: BacklogItem, reason: str, procedures_context: str = "") -> str:
    """What the ScrumMaster is told about a blocked task."""
    feedback = [
        f"{c.get('author') or 'Reviewer'}: {str(c.get('text', '')).strip()[:FEEDBACK_CHARS]}"
        for c in comment_list(item.comments)
        if is_rework_request(c)
    ][-FEEDBACK_SHOWN:]
    return (
        "You are the ScrumMaster. A task was blocked after the agents' attempts and "
        "escalated to you. Decide whether it gets another attempt in the next sprint "
        "or stays for a person.\n\n"
        f"## Task: {item.title}\n"
        f"Assigned to: {item.assignee or 'Developer'}\n"
        f"Priority: {item.priority or 'medium'}\n"
        f"Description: {item.description or '(none)'}\n\n"
        f"## What blocked it\n{reason.strip() or '(no reason recorded)'}\n\n"
        "## Rework the reviewers asked for\n"
        + ("\n".join(f"- {line}" for line in feedback) if feedback else "(none)")
        + f"{procedures_context}\n\n"
        + answer_format()
    )


def parse_answer(text: str) -> dict[str, str] | None:
    """The decision in the expected form, or ``None`` when the answer is unusable."""
    match = _ANSWER.search(text or "")
    if not match:
        return None
    decision = match["decision"].lower()
    fields = {
        m["key"].upper(): m["value"].strip() for m in _FIELD.finditer(match["rest"])
    }
    brief = fields.get("BRIEF", "")[:BRIEF_LIMIT] if decision == DECISION_RETRY else ""
    return {
        "decision": decision,
        "brief": brief,
        "why": fields.get("WHY", "")[:WHY_LIMIT],
    }


def ask(session: Session, company_id: int, prompt: str) -> str:
    """Ask the company's ScrumMaster agent (its model, or the role's default)."""
    scrum_master = session.exec(
        select(Agent).where(
            Agent.company_id == company_id, Agent.role == SCRUM_MASTER_ROLE
        )
    ).first()
    configured = (scrum_master.model if scrum_master else "") or ""
    model = configured.strip() or default_model_for_role(SCRUM_MASTER_ROLE)
    messages = [{"role": "user", "content": prompt}]
    if scrum_master and scrum_master.system_prompt:
        messages.insert(0, {"role": "system", "content": scrum_master.system_prompt})
    client = LLMProxyClient(
        base_url=os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100")
    )
    client.timeout = TIMEOUT_SECONDS
    result = client.chat(messages=messages, model=model, role=SCRUM_MASTER_ROLE)
    if not isinstance(result, dict) or result.get("error"):
        raise ScrumMasterError(f"LLM call failed: {result!r}"[:500])
    choices = result.get("choices") or [{}]
    content = ((choices[0] or {}).get("message") or {}).get("content") or ""
    if not str(content).strip():
        raise ScrumMasterError("LLM call failed: empty response")
    return str(content).strip()


# ---------------------------------------------------------------------------
# The decision, recorded and read back
# ---------------------------------------------------------------------------


def decision_body(decision: dict[str, str]) -> str:
    retry = decision["decision"] == DECISION_RETRY
    lines = [RETRY_LINE if retry else PERSON_LINE]
    if decision.get("why"):
        lines.append(WHY_PREFIX + decision["why"])
    if retry and decision.get("brief"):
        lines.append(BRIEF_PREFIX + decision["brief"])
    return "\n".join(lines)


def parse_decision_body(body: str) -> dict[str, str] | None:
    """Read a recorded triage back; ``None`` for any other message."""
    lines = (body or "").split("\n")
    if lines[0] == RETRY_LINE:
        decision = DECISION_RETRY
    elif lines[0] == PERSON_LINE:
        decision = DECISION_PERSON
    else:
        return None
    why = brief = ""
    for line in lines[1:]:
        if line.startswith(WHY_PREFIX):
            why = line[len(WHY_PREFIX) :]
        elif line.startswith(BRIEF_PREFIX):
            brief = line[len(BRIEF_PREFIX) :]
    return {"decision": decision, "why": why, "brief": brief}


def triage(
    session: Session, company_id: int, sprint_id: int, item: BacklogItem, reason: str
) -> AgentMessage | None:
    """Ask the ScrumMaster about a blocked task and record its decision.

    Returns the recorded message, or ``None`` when the call failed or the
    answer was unusable: the company then keeps its deterministic default.
    The caller commits.
    """
    try:
        context = procedures.procedures_prompt(
            procedures.active_procedures(session, company_id, item.assignee)
        )
        answer = ask(session, company_id, triage_prompt(item, reason, context))
    except Exception as exc:  # noqa: BLE001 - The triage is advice; the task stays blocked for a person either way.
        logger.warning("ScrumMaster triage failed for item=%s: %s", item.id, exc)
        return None
    decision = parse_answer(answer)
    if decision is None:
        logger.info(
            "ScrumMaster triage of item=%s was unusable: %r", item.id, answer[:200]
        )
        return None
    message = AgentMessage(
        company_id=company_id,
        sprint_id=sprint_id,
        backlog_item_id=item.id,
        from_name=SCRUM_MASTER_ROLE,
        from_role=SCRUM_MASTER_ROLE,
        to_role=item.assignee if decision["decision"] == DECISION_RETRY else None,
        kind="decision",
        body=decision_body(decision),
    )
    session.add(message)
    return message


def decision_for(session: Session, item_id: int) -> dict[str, Any] | None:
    """The ScrumMaster's latest triage of *item_id*, or ``None``."""
    rows = session.exec(
        select(AgentMessage.body)
        .where(
            AgentMessage.backlog_item_id == item_id,
            AgentMessage.from_role == SCRUM_MASTER_ROLE,
            AgentMessage.kind == "decision",
        )
        .order_by(AgentMessage.id.desc())
    ).all()
    for body in rows:
        decision = parse_decision_body(body)
        if decision:
            return decision
    return None
