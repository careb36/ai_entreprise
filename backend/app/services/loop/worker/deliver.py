"""Artifacts to the daemon, queued deliveries and their results (ADR 0014).

The task worker's deliver stage: the agent's files (its ``path=`` manifest,
the tools' edits, or file names in the task text) are sent to the workspace
daemon on ``workspace.generate``; without a daemon they are queued on
``workspace.deliver`` and ``_on_delivery_result`` records what the daemon
wrote once it reports on ``workspace.delivered``. Recording and
re-verification are the record stage's (``record._record_task_run``,
``record._reverify_run``). Moved out of ``task_worker``, which still
re-exports these names.
"""

import asyncio
import os
import re
from typing import Any
from uuid import uuid4

from config import settings
from database import engine
from logging_config import setup_logger
from models import ProjectDiscovery
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from nats_client import NATSClient
from sqlmodel import Session, select, text

from services.contracts.manifest import (
    ALLOWED_ARTIFACT_EXTENSIONS,
    expected_hashes,
    parse_file_manifest,
)
from services.contracts.manifest import (
    sanitize_relative_path as _sanitize_relative_path,
)
from services.evidence import acceptance
from services.evidence.verification import evaluate_workspace_writes
from services.loop import delivery_checks, review_digest
from services.loop.worker import board_writes, record
from services.platform.glassroom import publish_glassroom
from services.platform.orchestration_metrics import VERDICT_REFUTED
from services.tools import registry_lookup
from services.tools.workspace_edits import with_tool_edits
from services.workspace import git_workflow, workspace_daemon
from services.workspace.workspace_daemon import detect_test_command

logger = setup_logger("fabrique-task-worker")


def _resolve_workspace_root(company_id: int) -> str | None:
    """
    Return where this company's artifacts should be written.

    The company's discovered project wins; ``AGENT_WORKSPACE_ROOT`` is only a
    fallback for companies without one (e.g. self-improvement sprints that
    work on this repository).
    """
    with Session(engine) as session:
        discovery = session.exec(
            select(ProjectDiscovery)
            .where(ProjectDiscovery.company_id == company_id)
            .order_by(text("created_at DESC"))
        ).first()
        if discovery and discovery.project_path:
            return discovery.project_path

    return os.getenv("AGENT_WORKSPACE_ROOT") or None


def _normalize_artifact_content(path: str, deliverable: str) -> str:
    """Trim wrapper text so persisted files contain only final artifact content."""
    text_content = (deliverable or "").strip().replace("\r\n", "\n")

    fenced = re.search(
        r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)```", text_content, flags=re.DOTALL
    )
    if fenced and len(fenced.group(1).strip()) > 40:
        text_content = fenced.group(1).strip()

    if path.lower().endswith(".md"):
        text_content = re.sub(r"(?m)^\*\*\*\s*$", "", text_content).strip()
        heading = re.search(r"(?m)^#\s+", text_content)
        if heading and heading.start() > 0:
            text_content = text_content[heading.start() :].strip()

    return text_content


def _extract_artifact_targets(
    item: dict[str, Any], deliverable: str
) -> list[dict[str, str]]:
    """Infer concrete file targets from task metadata and generated content."""
    title_desc = f"{item.get('title', '')} {item.get('description', '')}"
    candidates = re.findall(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)", title_desc)

    artifacts: list[dict[str, str]] = []
    for c in candidates:
        rel = _sanitize_relative_path(c)
        if not rel:
            continue
        _, ext = os.path.splitext(rel)
        if ext.lower() not in ALLOWED_ARTIFACT_EXTENSIONS:
            continue
        artifacts.append(
            {"path": rel, "content": _normalize_artifact_content(rel, deliverable)}
        )

    # Remove duplicates while preserving order.
    dedup: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in artifacts:
        if a["path"] in seen:
            continue
        seen.add(a["path"])
        dedup.append(a)

    return dedup


def _build_review_payload(
    deliverable: str, artifact_targets: list[dict[str, str]]
) -> str:
    """What QA reviews: a digest of every file when files were written
    (``review_digest``), else the deliverable text."""
    if not artifact_targets:
        return (deliverable or "").strip()
    return review_digest.build(
        artifact_targets, record._summarize_deliverable(deliverable)
    )


def _delivery_comment(
    targets: list[dict[str, str]],
    evidence: dict[str, Any] | None,
    recorded: tuple[int, str] | None,
) -> str:
    run_note = f" — evidence run #{recorded[0]} ({recorded[1]})" if recorded else ""
    run_note += git_workflow.skipped_note(evidence)
    outcome, observation = evaluate_workspace_writes(
        [target["path"] for target in targets], evidence
    )
    if outcome == "pass":
        return f"Delivered to {(evidence or {}).get('workspace_root')}: {observation}{run_note}"
    if outcome == VERDICT_REFUTED:
        return f"Delivery refuted: {observation}{run_note}"
    if (evidence or {}).get("delivery_queued"):
        return f"Delivery queued: {observation}{run_note}"
    return f"Delivery unconfirmed: {observation}{run_note}"


# The project's test command is detected in services/workspace_daemon.
_detect_test_command = detect_test_command
_detect_project_test_command = workspace_daemon.detect_project_test_command
_run_delivery_tests = delivery_checks.run_delivery_tests


async def _observe_dependencies(
    evidence: dict[str, Any], targets: list[dict[str, str]]
) -> None:
    """Record the registry's answer per new dependency (the ``grounded`` check's).

    A registry that cannot be reached never blocks the delivery: the rows say
    ``exists: null`` and the check treats the dependency as unchecked.
    """
    try:
        await asyncio.to_thread(registry_lookup.observe, evidence, targets)
    except Exception as e:  # noqa: BLE001 - The delivery is not the lookup's hostage.
        logger.warning("Registry lookups failed for a delivery: %s", e)


def _plan_queued_delivery(
    item: dict[str, Any],
    sprint: dict[str, Any],
    workspace_root: str,
    targets: list[dict[str, str]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """The ``workspace.deliver`` job that keeps the files for an offline daemon.

    Marks *evidence* as waiting for it, so the delivery gate holds the task; the
    job is published only once that record exists, for a daemon reporting at once.
    """
    job_id = str(uuid4())
    evidence["delivery_queued"] = {"job_id": job_id}
    evidence["reason"] = (
        f"{evidence.get('reason') or 'the workspace daemon did not reply'}; "
        "delivery queued until the daemon connects"
    )
    return {
        "job_id": job_id,
        "workspace_root": workspace_root,
        "files": targets,
        "interaction_id": f"task-{item['id']}",
        "company_id": sprint["company_id"],
        "sprint_id": sprint["id"],
        "item_id": item["id"],
    }


async def _publish_queued_delivery(
    nats_client: NATSClient, job: dict[str, Any], evidence: dict[str, Any]
) -> bool:
    """
    Store *job* on ``workspace.deliver``; the daemon writes it when it connects
    and reports on ``workspace.delivered`` (``_on_delivery_result``).

    When it cannot be stored, *evidence* stops claiming the task waits for it.
    """
    try:
        await nats_client.publish(
            "workspace.deliver", job, msg_id=f"deliver-{job['job_id']}"
        )
        return True
    except Exception as e:  # noqa: BLE001 - Unqueued, the delivery stays unconfirmed.
        logger.warning("Could not queue delivery for item=%s: %s", job["item_id"], e)
        evidence.pop("delivery_queued", None)
        evidence["reason"] = evidence["reason"].replace(
            "delivery queued until the daemon connects",
            f"queueing the delivery failed ({type(e).__name__})",
        )
        return False


def _queued_delivery_evidence(
    company_id: int, item_id: int, job_id: str
) -> tuple[int, dict[str, Any], list[str]] | None:
    """The run whose latest verification is waiting for *job_id*, if any."""
    with Session(engine) as session:
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
        verification = session.exec(
            select(Verification)
            .where(Verification.agent_run_id == run.id)
            .order_by(Verification.id.desc())
        ).first()
        evidence = dict((verification.evidence or {}) if verification else {})
        if (evidence.get("delivery_queued") or {}).get("job_id") != job_id:
            return None
        files = [p for p in (run.claims or {}).get("files") or [] if isinstance(p, str)]
        return run.id, evidence, files


async def _on_delivery_result(nats_client: NATSClient, data: dict[str, Any]) -> None:
    """
    Record what the daemon wrote for a delivery that had been queued.

    The expected hashes come from the backend's own record of what it sent;
    the result supplies only the daemon's per-file observation.  A result for
    a job the task no longer waits for (a newer delivery superseded it) is
    ignored.
    """
    job_id = data.get("job_id")
    company_id, item_id, sprint_id = (
        data.get("company_id"),
        data.get("item_id"),
        data.get("sprint_id"),
    )
    if not job_id or company_id is None or item_id is None:
        logger.warning("Ignoring malformed workspace.delivered message: %s", data)
        return
    pending = await asyncio.to_thread(
        _queued_delivery_evidence, company_id, item_id, job_id
    )
    if pending is None:
        logger.info("No task waits for delivery job=%s; ignoring", job_id)
        return
    run_id, evidence, files = pending
    evidence.pop("delivery_queued", None)
    evidence.pop("reason", None)
    writes = data.get("files")
    evidence["workspace_writes"] = writes if isinstance(writes, list) else None
    if isinstance(data.get("project_files"), (dict, list)):
        evidence["project_files"] = data["project_files"]
    if evidence["workspace_writes"] is None:
        evidence["reason"] = data.get("error") or (
            "the workspace daemon sent no per-file results"
        )
    git_workflow.record_reply(evidence, data)
    evidence["delivered_after_queue"] = True

    written, _ = evaluate_workspace_writes(files, evidence)
    if settings.DELIVERY_TESTS_ENABLED and written == "pass":
        evidence["acceptance"] = await asyncio.to_thread(
            acceptance.evidence_for, engine, item_id
        )
        await delivery_checks.guard_test_files(
            nats_client, engine, evidence, settings.DELIVERY_TEST_TIMEOUT_SECONDS
        )
        await _run_delivery_tests(nats_client, {"id": sprint_id}, evidence, engine)

    recorded = await asyncio.to_thread(record._reverify_run, run_id, evidence)
    comment = _delivery_comment([{"path": p} for p in files], evidence, recorded)
    await asyncio.to_thread(
        board_writes._append_item_comment,
        item_id,
        f"Queued delivery: {comment}",
        "System",
        "verified",
        run_id=recorded[0] if recorded else None,
    )
    if sprint_id is not None:
        await publish_glassroom(
            nats_client,
            sprint_id,
            {
                "type": "status",
                "semantic_type": "task.artifact_delivery",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": item_id,
                "phase": "artifact_delivery",
                "agent_run_id": recorded[0] if recorded else None,
                "verdict": recorded[1] if recorded else None,
                "message": f"🧱 Queued delivery: {comment}",
            },
        )


async def _deliver_artifacts(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
) -> str:
    """
    Write the agent's files into the company's project and record the outcome.

    Files come from the agent's ``path=`` manifest, or — when it declared none —
    from file names in the task text.  The daemon is asked with a NATS request
    and reports, per file, the sha256 of the bytes it read back from disk; the
    worker compares that with the sha256 of the content it sent.  Returns the
    payload for QA review.
    """
    targets = with_tool_edits(parse_file_manifest(deliverable), agent.get("tool_edits"))
    if not targets:
        targets = await asyncio.to_thread(_extract_artifact_targets, item, deliverable)
    review_payload = _build_review_payload(deliverable, targets)

    evidence: dict[str, Any] | None = None
    queued_job: dict[str, Any] | None = None
    if targets:
        workspace_root = await asyncio.to_thread(
            _resolve_workspace_root, sprint["company_id"]
        )
        git_spec = await asyncio.to_thread(
            git_workflow.delivery_spec, engine, sprint, item
        )
        evidence = {
            "workspace_root": workspace_root,
            "expected": expected_hashes(targets),
            "workspace_writes": None,
        }
        if not workspace_root:
            evidence["reason"] = "workspace path not configured"
        else:
            try:
                reply = await nats_client.request(
                    "workspace.generate",
                    {
                        "workspace_root": workspace_root,
                        "files": targets,
                        "interaction_id": f"task-{item['id']}",
                        "sprint_id": sprint["id"],
                        **({"git": git_spec} if git_spec else {}),
                    },
                    timeout=git_workflow.delivery_timeout(git_spec),
                )
                writes = reply.get("files") if isinstance(reply, dict) else None
                evidence["workspace_writes"] = (
                    writes if isinstance(writes, list) else None
                )
                if isinstance(reply, dict) and isinstance(
                    reply.get("project_files"), (dict, list)
                ):
                    evidence["project_files"] = reply["project_files"]
                if evidence["workspace_writes"] is None:
                    evidence["reason"] = "the workspace daemon sent no per-file results"
                git_workflow.record_reply(evidence, reply)
            except Exception as e:  # noqa: BLE001 - A missing daemon leaves delivery unverifiable.
                evidence["reason"] = (
                    f"the workspace daemon did not reply ({type(e).__name__})"
                )
                logger.warning(
                    "Workspace write request failed for item=%s: %s", item["id"], e
                )
                if nats_client.jetstream_enabled:
                    queued_job = _plan_queued_delivery(
                        item, sprint, workspace_root, targets, evidence
                    ) | ({"git": git_spec} if git_spec else {})

        written, _ = evaluate_workspace_writes([t["path"] for t in targets], evidence)
        if settings.DELIVERY_TESTS_ENABLED and written == "pass":
            evidence["acceptance"] = await asyncio.to_thread(
                acceptance.evidence_for, engine, item["id"]
            )
            await delivery_checks.guard_test_files(
                nats_client, engine, evidence, settings.DELIVERY_TEST_TIMEOUT_SECONDS
            )
            await _run_delivery_tests(nats_client, sprint, evidence, engine)

    await _observe_dependencies(evidence, targets)
    recorded = await asyncio.to_thread(
        record._record_task_run, item, agent, sprint, deliverable, targets, evidence
    )
    queue_failed = queued_job is not None and not await _publish_queued_delivery(
        nats_client, queued_job, evidence
    )
    if queue_failed and recorded:
        # The run was recorded as waiting for a job that does not exist.
        recorded = await asyncio.to_thread(record._reverify_run, recorded[0], evidence)

    if targets:
        comment = _delivery_comment(targets, evidence, recorded)
        await asyncio.to_thread(
            board_writes._update_item_status,
            item["id"],
            "in_progress",
            comment_text=comment,
            comment_author="System",
            kind="verified",
            run_id=recorded[0] if recorded else None,
        )
        await publish_glassroom(
            nats_client,
            sprint["id"],
            {
                "type": "status",
                "semantic_type": "task.artifact_delivery",
                "source": "task_worker",
                "agent": "System",
                "role": "System",
                "task_id": item["id"],
                "task_status": "in_progress",
                "phase": "artifact_delivery",
                "task": item["title"],
                "agent_run_id": recorded[0] if recorded else None,
                "verdict": recorded[1] if recorded else None,
                "message": f"🧱 {comment}",
            },
        )

    return review_payload
