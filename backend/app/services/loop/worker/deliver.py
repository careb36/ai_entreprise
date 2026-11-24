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
from typing import Any
from uuid import uuid4

from config import settings
from database import engine
from logging_config import setup_logger
from models import ProjectDiscovery
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from nats_client import NATSClient
from sqlmodel import Session, select, text

from services.contracts import roles, tool_steps
from services.contracts.manifest import expected_hashes, parse_file_manifest
from services.evidence import acceptance
from services.evidence.verification import evaluate_workspace_writes
from services.loop import delivery_checks, folder_baseline, refuted_undo, review_digest
from services.loop.worker import board_writes, record, sprint_notes
from services.platform.glassroom import publish_glassroom
from services.platform.orchestration_metrics import VERDICT_REFUTED
from services.tools import registry_lookup, signature_lookup
from services.tools.workspace_edits import deletions, with_tool_edits
from services.workspace import daemon_liveness, git_workflow, workspace_daemon
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


async def _observe_signatures(
    nats_client: NATSClient, evidence: dict[str, Any] | None, targets: list
) -> None:
    """Record the daemon's signatures for the project names the delivered
    Python calls (the ``grounded`` check's symbol findings); never blocks."""
    try:
        await signature_lookup.observe(nats_client, evidence, targets)
    except Exception as e:  # noqa: BLE001 - The delivery is not the lookup's hostage.
        logger.warning("Signature lookups failed for a delivery: %s", e)


TESTS_DISABLED = (
    "tests are disabled for this deployment (DELIVERY_TESTS_ENABLED is off)"
)
FILES_UNCONFIRMED = "the files were not confirmed written, so the tests were not run"


def _record_tests_not_run(evidence: dict[str, Any], written: str) -> None:
    """Say why the tests did not run, instead of leaving nothing (issue #766).

    Without a ``test_run`` the Verifier used to have nothing to check and the
    missing run read as a pass; with this, ``tests_run`` is unverifiable with
    the reason (``services/evidence/required_checks.py``).
    """
    if "test_run" in evidence:
        return
    reason = (
        TESTS_DISABLED if not settings.DELIVERY_TESTS_ENABLED else FILES_UNCONFIRMED
    )
    evidence["test_run"] = {"command": None, "reason": reason}


def _heartbeat_note() -> str:
    """What the backend observed of the daemon's heartbeat, when it explains a missing reply."""
    beat = daemon_liveness.liveness()
    return f"; {beat['reason']}" if beat["state"] in {"stale", "offline"} else ""


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
            acceptance.evidence_for, engine, item_id, evidence.get("git")
        )
        await delivery_checks.guard_test_files(
            nats_client, engine, evidence, settings.DELIVERY_TEST_TIMEOUT_SECONDS
        )
        await asyncio.to_thread(
            folder_baseline.observe, engine, company_id, item_id, evidence
        )
        await _run_delivery_tests(nats_client, {"id": sprint_id}, evidence, engine)
    else:
        _record_tests_not_run(evidence, written)

    recorded = await asyncio.to_thread(record._reverify_run, run_id, evidence)
    comment = _delivery_comment([{"path": p} for p in files], evidence, recorded)
    undone = await refuted_undo.undo_refuted(
        nats_client,
        engine,
        company_id=company_id,
        item_id=item_id,
        run_id=recorded[0] if recorded else None,
        verdict=recorded[1] if recorded else None,
        evidence=evidence,
    )
    if undone:
        comment = f"{comment}. {undone}"
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


DOCS_ONLY_REASON = "a documentation task writes documentation only"


def docs_scope(
    targets: list[dict[str, Any]],
    deleted: list[dict[str, Any]],
    agent: dict[str, Any],
    kind: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """*targets* and *deleted* within the execution turn's write scope
    (``contracts/tool_steps.writable``: a TechWriter, or a task whose kind is
    documentation, writes documentation only), and the paths left out. The
    refusal goes on the run's evidence (``docs_write``) with its reason."""
    role = roles.canonical(agent.get("role"))
    step = tool_steps.execution_step(kind)
    keep = [t for t in targets if tool_steps.writable(step, role, t.get("path"))]
    gone = [d for d in deleted if tool_steps.writable(step, role, d.get("path"))]
    refused = [t.get("path") or "" for t in targets + deleted if t not in keep + gone]
    if refused:
        agent["docs_write"] = {"refused": refused, "reason": DOCS_ONLY_REASON}
    return keep, gone, refused


async def _deliver_artifacts(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
    deliverable: str,
) -> str:
    """
    Write the agent's files into the company's project and record the outcome.

    Files come only from the agent's ``path=`` manifest and its tool edits. A
    deliverable that declares none writes nothing: it is text for QA to read.
    Until 2026-11-23 a deliverable without a manifest was written, whole, into
    every file the task's title or description named; in sprints 4 and 5 that
    put a plan in prose into ``backend/tests/test_browser_image.py``,
    ``docs/architecture/contexts.json`` and two check scripts, because their
    tasks mentioned them.  The daemon is asked with a NATS request
    and reports, per file, the sha256 of the bytes it read back from disk; the
    worker compares that with the sha256 of the content it sent.  Returns the
    payload for QA review.
    """
    targets = with_tool_edits(parse_file_manifest(deliverable), agent.get("tool_edits"))
    # Files the edit tool emptied and deleted (docs/design/move-and-delete-files.md).
    deleted = deletions(agent.get("tool_edits"))
    # A documentation task writes documentation only, its answer's blocks too.
    targets, deleted, refused = docs_scope(targets, deleted, agent, item.get("kind"))
    review_payload = _build_review_payload(deliverable, targets)
    if deleted:
        review_payload += "\n\nDeleted: " + ", ".join(d["path"] for d in deleted)
    if refused:
        review_payload += f"\n\nNot delivered ({DOCS_ONLY_REASON}): " + ", ".join(
            refused
        )

    evidence: dict[str, Any] | None = None
    queued_job: dict[str, Any] | None = None
    if targets or deleted:
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
        incompatible = daemon_liveness.incompatibility()
        if not workspace_root:
            evidence["reason"] = "workspace path not configured"
        elif incompatible:
            # Not sent, and not queued: the same daemon would take the job.
            evidence["reason"] = f"the workspace daemon is incompatible: {incompatible}"
        else:
            try:
                reply = await nats_client.request(
                    "workspace.generate",
                    {
                        "workspace_root": workspace_root,
                        "files": targets + deleted,
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
                    + _heartbeat_note()
                )
                logger.warning(
                    "Workspace write request failed for item=%s: %s", item["id"], e
                )
                if nats_client.jetstream_enabled:
                    queued_job = _plan_queued_delivery(
                        item, sprint, workspace_root, targets + deleted, evidence
                    ) | ({"git": git_spec} if git_spec else {})

        written, _ = evaluate_workspace_writes(
            [t["path"] for t in targets + deleted], evidence
        )
        if settings.DELIVERY_TESTS_ENABLED and written == "pass":
            evidence["acceptance"] = await asyncio.to_thread(
                acceptance.evidence_for, engine, item["id"], evidence.get("git")
            )
            await delivery_checks.guard_test_files(
                nats_client, engine, evidence, settings.DELIVERY_TEST_TIMEOUT_SECONDS
            )
            await asyncio.to_thread(
                folder_baseline.observe,
                engine,
                sprint["company_id"],
                item["id"],
                evidence,
            )
            await _run_delivery_tests(nats_client, sprint, evidence, engine)
        else:
            _record_tests_not_run(evidence, written)

    await _observe_dependencies(evidence, targets)
    await _observe_signatures(nats_client, evidence, targets)
    recorded = await asyncio.to_thread(
        record._record_task_run, item, agent, sprint, deliverable, targets, evidence
    )
    # The deliverable's NOTE … END blocks go on the sprint board (#439): claims.
    await asyncio.to_thread(
        sprint_notes.record_from_delivery,
        engine,
        item,
        agent,
        sprint,
        deliverable,
        recorded[0] if recorded else None,
    )
    queue_failed = queued_job is not None and not await _publish_queued_delivery(
        nats_client, queued_job, evidence
    )
    if queue_failed and recorded:
        # The run was recorded as waiting for a job that does not exist.
        recorded = await asyncio.to_thread(record._reverify_run, recorded[0], evidence)

    if targets or deleted:
        comment = _delivery_comment(targets + deleted, evidence, recorded)
        # Without Git a refuted delivery is taken back out (refuted_undo).
        undone = await refuted_undo.undo_refuted(
            nats_client,
            engine,
            company_id=sprint["company_id"],
            item_id=item["id"],
            run_id=recorded[0] if recorded else None,
            verdict=recorded[1] if recorded else None,
            evidence=evidence,
        )
        if undone:
            comment = f"{comment}. {undone}"
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
