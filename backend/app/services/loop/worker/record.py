"""AgentRun recording and re-verification (ADR 0014, the task worker's record stage).

A delivery is ingested as an ``AgentRun`` (``producer: le-fabrique-task-worker``,
``external_ref: task-<id>``) whose claims are what the agent produced and
whose evidence is what the daemon observed, then verified deterministically;
a queued delivery's run is verified again once the daemon reports. The gate
before QA, the risk tier and the latest verification are read here from
``delivery_gate``. Moved out of ``task_worker``, which still re-exports these
names.
"""

from datetime import UTC, datetime
from typing import Any

from database import engine
from logging_config import setup_logger
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from schemas import AgentRunCreate
from sqlmodel import Session

from services.contracts import program_metrics
from services.contracts.manifest import MANIFEST_BLOCK, parse_file_manifest
from services.evidence import agent_run_service, delivery_gate, protected_check, risk
from services.evidence.verification import verify_agent_run
from services.models import edit_format
from services.tools.workspace_edits import observe_tools

logger = setup_logger("fabrique-task-worker")


# The gate before QA, the risk tier and the latest verification: services/delivery_gate.
def _delivery_gate(company_id: int, item_id: int) -> tuple[str, str] | None:
    return delivery_gate.gate(engine, company_id, item_id)


def _delivery_risk(company_id: int, item_id: int) -> dict[str, Any] | None:
    return delivery_gate.risk(engine, company_id, item_id)


def _protected_reasons(company_id: int, item_id: int, deliverable: str) -> list[str]:
    """``grader: <path>`` for each protected path the task's latest recorded run
    or its deliverable touched, matched under the list in force now; the
    reason the list is missing when it cannot be read (fail closed)."""
    verification = delivery_gate.latest_verification(engine, company_id, item_id)
    declared = {"files": [f["path"] for f in parse_file_manifest(deliverable)]}
    try:
        paths = protected_check.touched(declared, None, None)
        if verification is not None:
            with Session(engine) as session:
                run = session.get(AgentRun, verification.agent_run_id)
                row = {"checks": verification.checks, "evidence": verification.evidence}
                if run is not None:
                    paths += protected_check.touched(run.claims, run.subject, row)
    except protected_check.ProtectedListUnavailable:
        return [f"{risk.GRADER_AREA}: {protected_check.UNAVAILABLE}"]
    return [f"{risk.GRADER_AREA}: {p}" for p in dict.fromkeys(paths)]


def _delivered_files(company_id: int, item_id: int, deliverable: str) -> list[str]:
    """The files the task's delivery wrote or declared: its manifest's paths and
    the ``files`` its latest recorded run claims (tool edits included)."""
    paths = [f["path"] for f in parse_file_manifest(deliverable)]
    verification = delivery_gate.latest_verification(engine, company_id, item_id)
    if verification is not None:
        with Session(engine) as session:
            run = session.get(AgentRun, verification.agent_run_id)
            if run is not None:
                paths += list((run.claims or {}).get("files") or [])
    return list(dict.fromkeys(p for p in paths if p))


def _latest_task_verification(company_id: int, item_id: int) -> Verification | None:
    return delivery_gate.latest_verification(engine, company_id, item_id)


def _summarize_deliverable(deliverable: str, limit: int = 500) -> str:
    """The agent's prose outside the file blocks: its own account of the work."""
    prose = MANIFEST_BLOCK.sub("", deliverable or "").strip()
    return prose[:limit]


def _claimed_metrics(deliverable: str) -> dict[str, Any]:
    """``{"metrics": [...]}`` when the answer states metrics, else nothing."""
    metrics = program_metrics.parse_claims(MANIFEST_BLOCK.sub("", deliverable or ""))
    return {"metrics": metrics} if metrics else {}


def _record_task_run(
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
    targets: list[dict[str, str]],
    evidence: dict[str, Any] | None,
) -> tuple[int, str] | None:
    """
    Ingest the deliverable as an ``AgentRun`` and verify it deterministically.

    The claims are what the agent produced (declared paths, its summary); the
    evidence is what the daemon observed on disk.  Returns ``(run_id, verdict)``
    or ``None`` when ingestion fails — recording never breaks the task flow.
    """
    payload = AgentRunCreate(
        company_id=sprint["company_id"],
        external_ref=f"task-{item['id']}",
        producer=TASK_WORKER_PRODUCER,
        # Only the model the proxy reported as having answered: an observation.
        # When it reported none the field stays empty rather than defaulting
        # to the model that was requested (POSITIONING rule 7).
        model=agent.get("served_model") or None,
        agent_role=agent.get("role"),
        subject={
            "sprint_id": sprint["id"],
            "backlog_item_id": item["id"],
            "title": item.get("title"),
        },
        claims={
            "files": [target["path"] for target in targets],
            "summary": _summarize_deliverable(deliverable),
            # The metrics it states (``claimed-metric:`` lines): claims, never
            # beside the observed ones (``evidence/metric_matches``).
            **_claimed_metrics(deliverable),
        },
        artifact=deliverable or "",
        finished_at=datetime.now(UTC),
        # The id the outbox gave the task.assign that started this run (intake).
        correlation_id=agent.get("correlation_id"),
    )
    # Tool calls and edited files: observations beside the daemon's, not claims.
    evidence = observe_tools(evidence, agent)
    # The weight of each prompt section the backend sent, and what the budget
    # left out (prompt_budget, #443): the backend's observation, not a claim.
    if agent.get("prompt_sections"):
        evidence = evidence if isinstance(evidence, dict) else {}
        evidence["prompt_sections"] = agent["prompt_sections"]
    # The difficulty estimate and the routing it led to (#319): the plan and
    # the request, beside the served model above (the observation); and the
    # model's profile the backend applied, with each field's source (#766).
    # The paths a documentation task's answer named outside the docs (deliver).
    for key in ("difficulty", "routing", "model_profile", "docs_write"):
        if agent.get(key):
            evidence = evidence if isinstance(evidence, dict) else {}
            evidence[key] = agent[key]
    # The edit format asked for and the one the backend read in the answer's
    # blocks and tool calls (#689); never the model's account of it. Only a
    # run of the task worker's own model call has a requested format.
    if agent.get("edit_format"):
        evidence = evidence if isinstance(evidence, dict) else {}
        evidence["edit_format"] = edit_format.observe(
            parse_file_manifest(deliverable),
            agent.get("tool_calls"),
            agent["edit_format"],
        )
    try:
        with Session(engine) as session:
            run, _created = agent_run_service.ingest_agent_run(session, payload)
            session.commit()
            verification = verify_agent_run(session, run, evidence)
            return run.id, verification.verdict
    except Exception as e:  # noqa: BLE001 - Evidence recording must not block delivery.
        logger.warning("AgentRun recording failed for item=%s: %s", item.get("id"), e)
        return None


def _reverify_run(run_id: int, evidence: dict[str, Any]) -> tuple[int, str] | None:
    try:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            verification = verify_agent_run(session, run, evidence)
            return run.id, verification.verdict
    except Exception as e:  # noqa: BLE001 - Evidence recording must not stop the consumer.
        logger.warning("Re-verification failed for run=%s: %s", run_id, e)
        return None
