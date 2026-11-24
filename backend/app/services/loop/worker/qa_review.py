"""QA's review of one delivered task (ADR 0014, the task worker's review stage).

The review cycle (``review``) hands ``_qa_review`` each ``in_review`` task that
passed its checks and the delivery gate: the review prompt (``review_prompt.build``),
the QA model's answer after its reads (``_call_llm_for_review``; only ``APPROVED:``
or ``NEEDS WORK:`` is a verdict), the hold for a person on an approved delivery
(``_person_hold``: a high risk tier, or the company's autonomy level), the
rework budget (``_rework_budget``), the verdict's events, messages and
escalation (``_record_verdict``) and what it leads to on Glassroom and at the
sprint's close (``_announce_verdict``). A review that fails is recorded for a
retry, or pauses the sprint when the provider has no credit (``_review_failed``).
The task's one rework budget (``MAX_REWORKS``), which the delivery gate and
the quality gate read here too, is defined here. Moved out of
``task_worker``, which still re-exports these names.
"""

import asyncio
import functools
from typing import Any

from config import settings
from database import engine
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from models import AgentMessage, BacklogItem, Company, Sprint
from nats_client import NATSClient
from sqlmodel import Session

from services.contracts import company_limits
from services.contracts.manifest import parse_file_manifest
from services.evidence import risk
from services.loop import sprint_control
from services.loop.answers import REVIEW_MAX_TOKENS
from services.loop.answers import llm_content as _llm_content
from services.loop.answers import llm_failure as _llm_failure
from services.loop.worker import close, execute, messages, record, review_prompt
from services.loop.worker.qa_review_reads import answer as review_answer
from services.models import model_routing
from services.platform import company_limits_setting, protected_paths_setting
from services.platform.glassroom import publish_glassroom
from services.platform.orchestration_metrics import (
    ACTION_QA_REVIEW,
    RESULT_NEEDS_WORK,
    classify_review_result,
    record_agent_turn,
)
from services.tasks import task_events
from services.tasks.board import (
    DELIVERY_GATE_AUTHOR,
    RISK_HOLD_PREFIX,
    SCRUM_MASTER_ROLE,
)

logger = setup_logger("fabrique-task-worker")

AUTOPILOT_QA_REWORK_ENABLED = settings.AUTOPILOT_QA_REWORK_ENABLED
DEFAULT_MAX_REWORKS = company_limits.DEFAULT_MAX_REWORKS


def _max_reworks() -> int:
    """One budget per task across every cause (issue #766): the quality
    gate, the delivery gate and QA each counted their own two, and sprint 3's
    task 18 was sent back nine times. The deployment's ``MAX_REWORKS``."""
    if (
        settings.MAX_REWORKS is None
        and settings.AUTOPILOT_MAX_REWORK_CYCLES is not None
    ):
        logger.warning("AUTOPILOT_MAX_REWORK_CYCLES is deprecated; set MAX_REWORKS.")
    return company_limits.deployment_rework_limit(
        settings.MAX_REWORKS, settings.AUTOPILOT_MAX_REWORK_CYCLES
    )


MAX_REWORKS = _max_reworks()


def rework_limit(session: Session, company_id: int | None) -> int:
    """A task's rework budget: the company's ``max_reworks`` when it set one
    (``services/platform/company_limits_setting.py``), else the deployment's."""
    own = company_limits_setting.stored(session, company_id).get("max_reworks")
    return MAX_REWORKS if own is None else own


def rework_limit_of(company_id: int | None) -> int:
    """``rework_limit`` in a session of its own (for a thread)."""
    with Session(engine) as session:
        return rework_limit(session, company_id)


# What a rework asks for (issue #766): the earlier attempts are on the task
# branch, so the agent changes what the problem needs instead of writing every
# file again, which lost earlier work and grew each attempt.
EDIT_INSTRUCTION = (
    "Your earlier attempts are on the task branch: read what is there, change "
    "only what this problem needs (with workspace__edit where it is offered), "
    "and deliver only the files you change, each as a path= block."
)


async def _qa_review(
    session: Session,
    nats_client: NATSClient,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
    task_record: Any,
    deliverable: str,
    qa_review_failures: int,
    quality_gate_blocked_ids: set[int],
) -> None:
    """QA reviews one delivery; a task QA leaves blocked joins ``quality_gate_blocked_ids``."""
    # The task branch as the daemon reads it: every attempt, not only this one.
    branch = await review_prompt.task_branch(session, nats_client, item, sprint)
    prompt_text = review_prompt.build(
        session, item, sprint, reviewer_dict, deliverable, qa_review_failures, branch
    )

    try:
        await publish_glassroom(
            nats_client,
            sprint.id,
            {
                "type": "agent_start",
                "semantic_type": "task.review_started",
                "source": "task_worker",
                "agent": reviewer_dict["name"],
                "role": reviewer_dict["role"],
                "task_id": item.id,
                "task_status": "in_review",
                "phase": "qa_review",
                "task": f"Reviewing: {item.title}",
                "message": f"🔍 {reviewer_dict['name']} is reviewing: {item.title}",
                "message_key": "glassroom.events.review_started",
                "message_params": {"agent": reviewer_dict["name"], "task": item.title},
            },
        )

        review_result = await asyncio.to_thread(
            functools.partial(
                _call_llm_for_review,
                reviewer_dict,
                prompt_text,
                company_id=sprint.company_id,
                sprint_id=sprint.id,
                backlog_item_id=item.id,
            )
        )

        normalized_review = (review_result or "").strip()
        verdict = classify_review_result(normalized_review)
        if verdict is None:
            # Only an explicit APPROVED: closes a task; anything else
            # is a failed review, retried and then blocked below.
            raise ValueError(
                "reviewer answered without APPROVED:/NEEDS WORK: — "
                f"{normalized_review[:120]!r}"
            )
        needs_work = verdict == RESULT_NEEDS_WORK
        risk_hold, risk_check = await _person_hold(
            session, item, sprint, deliverable, needs_work
        )
        limit = rework_limit(session, sprint.company_id)
        previous_reworks, autopilot_rework_allowed, review_status = _rework_budget(
            task_record, reviewer_dict, needs_work, risk_hold, limit
        )
        escalation = _record_verdict(
            session,
            item,
            sprint,
            reviewer_dict,
            normalized_review=normalized_review,
            needs_work=needs_work,
            review_status=review_status,
            autopilot_rework_allowed=autopilot_rework_allowed,
            previous_reworks=previous_reworks,
            risk_hold=risk_hold,
            risk_check=risk_check,
            quality_gate_blocked_ids=quality_gate_blocked_ids,
            limit=limit,
        )
        if escalation is not None:
            await asyncio.to_thread(
                messages._triage_blocked_task,
                sprint.company_id,
                sprint.id,
                item.id,
                f"Still not accepted after {previous_reworks} rework(s). "
                f"Last review: {normalized_review[:500]}",
                escalation.id,
            )

        await _announce_verdict(
            nats_client,
            item,
            sprint,
            reviewer_dict,
            normalized_review=normalized_review,
            needs_work=needs_work,
            autopilot_rework_allowed=autopilot_rework_allowed,
            previous_reworks=previous_reworks,
            risk_hold=risk_hold,
            risk_check=risk_check,
            limit=limit,
        )

    except Exception as e:  # noqa: BLE001 - Isolate review failures to the affected item.
        await _review_failed(session, nats_client, item, sprint, reviewer_dict, e)


async def _person_hold(
    session: Session,
    item: BacklogItem,
    sprint: Sprint,
    deliverable: str,
    needs_work: bool,
) -> tuple[bool, dict[str, Any] | None]:
    """Whether an approved delivery waits for a person, and the check that says why."""
    # Approved, but a high-risk delivery waits for a person at
    # every autonomy level (services/risk.py).
    risk_check = (
        None
        if needs_work
        else await asyncio.to_thread(record._delivery_risk, sprint.company_id, item.id)
    )
    if risk_check is None and not needs_work:
        # No recorded check (the run was not recorded): unknown is
        # not low, so the paths the deliverable declares decide; a
        # claim, which can only raise the tier.
        risk_check = risk.classify(
            [f["path"] for f in parse_file_manifest(deliverable)]
        )
    if not needs_work and protected_paths_setting.enabled(session, sprint.company_id):
        # The company hosts the factory under the setting as it is now, which
        # may postdate the verification: the recorded run's paths and the
        # deliverable's are matched again (record._protected_reasons).
        grader = await asyncio.to_thread(
            record._protected_reasons, sprint.company_id, item.id, deliverable
        )
        if grader:
            earlier = (risk_check or {}).get("reasons") or []
            risk_check = {"tier": risk.RISK_HIGH, "reasons": [*grader, *earlier][:10]}
    risk_hold = risk_check is not None and risk.is_high(risk_check.get("tier"))
    company = session.get(Company, sprint.company_id)
    person_reason = sprint_control.person_acceptance_reason(
        company.autonomy_level, engine, sprint.company_id, item.id
    )
    if not needs_work and not risk_hold and person_reason:
        risk_hold = True
        risk_check = {"reasons": [person_reason]}
    return risk_hold, risk_check


def _rework_budget(
    task_record: Any,
    reviewer_dict: dict[str, Any],
    needs_work: bool,
    risk_hold: bool,
    limit: int | None = None,
) -> tuple[int, Any, str]:
    """QA's earlier rework requests, whether another is allowed, and the status it leads to."""
    previous_reworks = task_record.reworks()
    autopilot_rework_allowed = (
        needs_work
        and AUTOPILOT_QA_REWORK_ENABLED
        and previous_reworks < (MAX_REWORKS if limit is None else limit)
    )
    if needs_work:
        review_status = "todo" if autopilot_rework_allowed else "blocked"
    else:
        review_status = "blocked" if risk_hold else "done"
    return previous_reworks, autopilot_rework_allowed, review_status


def _record_verdict(
    session: Session,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
    *,
    normalized_review: str,
    needs_work: bool,
    review_status: str,
    autopilot_rework_allowed: Any,
    previous_reworks: int,
    risk_hold: bool,
    risk_check: dict[str, Any] | None,
    quality_gate_blocked_ids: set[int],
    limit: int | None = None,
) -> AgentMessage | None:
    """The verdict's events and messages, committed; the escalation to triage, if any."""
    # The verdict, then what it leads to: each an event and its comment.
    task_events.record(
        session,
        item,
        "reviewed",
        reviewer_dict["role"],
        author=reviewer_dict["name"],
        status=review_status,
        text=normalized_review[:2000],
        payload={"verdict": "needs_work" if needs_work else "approved"},
    )
    if needs_work and autopilot_rework_allowed:
        task_events.record(
            session,
            item,
            "sent_back",
            "system",
            text=f"Autopilot rework scheduled after QA feedback ({previous_reworks + 1}/{MAX_REWORKS if limit is None else limit}).",
        )
    elif needs_work:
        task_events.record(
            session,
            item,
            "blocked",
            "system",
            text="QA quality gate failed — task blocked pending manual triage.",
        )
        quality_gate_blocked_ids.add(item.id)

    risk_note = None
    if risk_hold:
        risk_reasons = "; ".join(risk_check.get("reasons") or [])
        risk_note = (
            f"{RISK_HOLD_PREFIX} ({risk_reasons or 'high tier'}). "
            "Mark it done to accept it, or send it back to work."
        )
        task_events.record(session, item, "risk_hold", "system", text=risk_note)

    session.add(item)
    session.add(
        messages._message(
            sprint.company_id,
            sprint.id,
            item.id,
            reviewer_dict["name"],
            reviewer_dict["role"],
            item.assignee or "Developer",
            "request" if needs_work else "decision",
            normalized_review[:2000],
        )
    )
    if risk_note is not None:
        # To a person, not the ScrumMaster: its triage decides
        # retries, and this delivery was approved.
        session.add(
            messages._message(
                sprint.company_id,
                sprint.id,
                item.id,
                DELIVERY_GATE_AUTHOR,
                DELIVERY_GATE_AUTHOR,
                "Manager",
                "escalation",
                risk_note,
            )
        )
    escalation = None
    if needs_work and not autopilot_rework_allowed:
        escalation = messages._message(
            sprint.company_id,
            sprint.id,
            item.id,
            reviewer_dict["name"],
            reviewer_dict["role"],
            SCRUM_MASTER_ROLE,
            "escalation",
            f'"{item.title}" is blocked: still not accepted after '
            f"{previous_reworks} rework(s). Last review: {normalized_review[:500]}",
        )
        session.add(escalation)
    session.commit()
    return escalation


async def _announce_verdict(
    nats_client: NATSClient,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
    *,
    normalized_review: str,
    needs_work: bool,
    autopilot_rework_allowed: Any,
    previous_reworks: int,
    risk_hold: bool,
    risk_check: dict[str, Any] | None,
    limit: int | None = None,
) -> None:
    """The verdict on Glassroom; an approval, held or not, may close the sprint."""
    cap = MAX_REWORKS if limit is None else limit
    if needs_work:
        if autopilot_rework_allowed:
            await publish_glassroom(
                nats_client,
                sprint.id,
                {
                    "type": "status",
                    "semantic_type": "task.rework_scheduled",
                    "source": "task_worker",
                    "agent": "System",
                    "role": "System",
                    "task_id": item.id,
                    "task_status": "todo",
                    "phase": "rework",
                    "rework_count": previous_reworks + 1,
                    "rework_limit": cap,
                    "task": f"Reviewed: {item.title}",
                    "message": f"🔁 Autopilot accepted QA feedback for: {item.title}. Rework attempt {previous_reworks + 1}/{cap}.",
                    "message_key": "glassroom.events.rework_scheduled",
                    "message_params": {
                        "task": item.title,
                        "attempt": previous_reworks + 1,
                        "limit": cap,
                    },
                    "preview": normalized_review[:200],
                },
            )
            logger.info(
                "Reviewed item=%s → todo (autopilot rework) by %s [reworks=%s/%s]",
                item.id,
                reviewer_dict["name"],
                previous_reworks + 1,
                cap,
            )
        else:
            await publish_glassroom(
                nats_client,
                sprint.id,
                {
                    "type": "error",
                    "semantic_type": "task.blocked",
                    "source": "task_worker",
                    "agent": reviewer_dict["name"],
                    "role": reviewer_dict["role"],
                    "task_id": item.id,
                    "task_status": "blocked",
                    "phase": "qa_gate",
                    "rework_count": previous_reworks + 1,
                    "rework_limit": cap,
                    "task": f"Reviewed: {item.title}",
                    "message": f"❌ {reviewer_dict['name']} requested rework for: {item.title}. Blocked for manual triage.",
                    "message_key": "glassroom.events.rework_blocked",
                    "message_params": {
                        "agent": reviewer_dict["name"],
                        "task": item.title,
                    },
                    "preview": normalized_review[:200],
                },
            )
            logger.info(
                "Reviewed item=%s → blocked (needs work gate) by %s [reworks=%s/%s]",
                item.id,
                reviewer_dict["name"],
                previous_reworks + 1,
                cap,
            )
    elif risk_hold:
        await publish_glassroom(
            nats_client,
            sprint.id,
            {
                "type": "status",
                "semantic_type": "task.awaiting_person",
                "source": "task_worker",
                "agent": DELIVERY_GATE_AUTHOR,
                "role": "System",
                "task_id": item.id,
                "task_status": "blocked",
                "phase": "risk_hold",
                "risk_tier": risk_check.get("tier"),
                "task": f"Reviewed: {item.title}",
                "message": f"🛡️ {reviewer_dict['name']} approved {item.title}; "
                "high-risk delivery, waiting for a person to accept it.",
                "message_key": "glassroom.events.awaiting_person",
                "message_params": {"agent": reviewer_dict["name"], "task": item.title},
                "preview": "; ".join(risk_check.get("reasons") or [])[:200],
            },
        )
        logger.info(
            "Reviewed item=%s → blocked (high risk, awaiting a person)",
            item.id,
        )
        await close._finalize_sprint_if_ready(nats_client, sprint.id)
    else:
        await publish_glassroom(
            nats_client,
            sprint.id,
            {
                "type": "agent_complete",
                "semantic_type": "task.approved",
                "source": "task_worker",
                "agent": reviewer_dict["name"],
                "role": reviewer_dict["role"],
                "task_id": item.id,
                "task_status": "done",
                "phase": "qa_approved",
                "task": f"Reviewed: {item.title}",
                "message": f"✅ {reviewer_dict['name']} approved: {item.title} → done",
                "message_key": "glassroom.events.approved",
                "message_params": {"agent": reviewer_dict["name"], "task": item.title},
                "preview": normalized_review[:200],
            },
        )

        logger.info("Reviewed item=%s → done by %s", item.id, reviewer_dict["name"])
        await close._finalize_sprint_if_ready(nats_client, sprint.id)


async def _review_failed(
    session: Session,
    nats_client: NATSClient,
    item: BacklogItem,
    sprint: Sprint,
    reviewer_dict: dict[str, Any],
    e: Exception,
) -> None:
    """A failed review is retried; a provider out of credit pauses the sprint instead."""
    logger.warning("Review failed for item=%s: %s", item.id, e)
    credit = getattr(e, "credit_provider", None)
    if credit and sprint_control.pause_for_credit(session, sprint.id, credit):
        session.commit()
        await sprint_control.publish_credit_pause(nats_client, sprint.id, credit)
    if credit:
        return

    task_events.record(
        session,
        item,
        "review_failed",
        "system",
        text=f"QA review failed — will retry automatically. Reason: {str(e)[:300]}",
    )
    session.commit()

    await publish_glassroom(
        nats_client,
        sprint.id,
        {
            "type": "error",
            "semantic_type": "task.review_error",
            "source": "task_worker",
            "agent": reviewer_dict["name"],
            "role": reviewer_dict["role"],
            "task_id": item.id,
            "task_status": item.status,
            "phase": "qa_review",
            "task": f"Reviewing: {item.title}",
            "message": f"❌ {reviewer_dict['name']} review failed for: {item.title}. Will retry.",
            "message_key": "glassroom.events.review_error",
            "message_params": {"agent": reviewer_dict["name"], "task": item.title},
        },
    )


def _call_llm_for_review(
    agent: dict[str, Any],
    prompt: str,
    *,
    company_id: int | None = None,
    sprint_id: int | None = None,
    backlog_item_id: int | None = None,
) -> str:
    """Synchronous LLM call for code review (runs in thread).

    When ``company_id`` is provided the review is written to ``audit_log`` with
    ``role=QA`` and ``result_type`` (APPROVED / NEEDS_WORK) so the QA rate KPI
    reads from the audit trail instead of its comment-scan fallback.
    """
    client = LLMProxyClient(settings.LLM_PROXY_URL)
    client.max_tokens, client.company_id = REVIEW_MAX_TOKENS, company_id
    # The verdict word is parsed: thinking off (services/models/thinking.py).
    client.structured = True
    request = [{"role": "user", "content": prompt}]
    if agent.get("system_prompt"):
        request.insert(0, {"role": "system", "content": agent["system_prompt"]})
    model = model_routing.resolve_agent_model(agent, default_role="QA")
    # A look at the delivery first, where it can be read (``qa_review_reads``).
    result, reads = review_answer(
        client, request, model, agent, company_id, sprint_id, backlog_item_id
    )

    failure = _llm_failure(result, model)
    if failure:
        raise execute.LLMCallError(failure, sprint_control.credit_failure(result))

    content = _llm_content(result)

    if company_id is not None:
        record_agent_turn(
            company_id=company_id,
            sprint_id=sprint_id,
            backlog_item_id=backlog_item_id,
            agent={**agent, "model": result.get("served_model") or model},
            action=ACTION_QA_REVIEW,
            llm_result=result,
            result_type=classify_review_result(content),
            result_preview=content,
            tool_calls=reads,
        )
    return content
