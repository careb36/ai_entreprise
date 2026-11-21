"""
Task Worker: the NATS consumer that has an AI agent execute each backlog item.

Per task: receive ``task.assign`` (from the outbox), load the item and the agent
for its ``assignee`` (role and company, with its configured model and prompt),
call the LLM proxy, move the item to ``in_review`` with the deliverable as a
comment, and publish to Glassroom.

Split by loop stage into ``services/loop/worker/`` (ADR 0014); this module is
the facade: the worker ``lifecycle`` starts (``process_backlog_tasks``, the
intake's), the ``engine`` a test points with ``tests/worker_stages.use_engine``
and ``console_host`` reads, and the stages' names re-exported for their
importers.
"""

from database import engine  # noqa: F401 - read by console_host and the tests
from models.agent_run import TASK_WORKER_PRODUCER  # noqa: F401 - re-exported

from services.contracts.manifest import parse_file_manifest  # noqa: F401 - re-exported

# Re-exported for importers of ``services.loop.task_worker`` (ADR 0014); a test
# patches the stage module (``services.loop.worker.<stage>``), which owns them.
from services.loop.worker.board_writes import (  # noqa: F401 - re-exported
    _append_item_comment,
    _update_item_status,
)
from services.loop.worker.close import (  # noqa: F401 - re-exported
    AUTO_SELF_IMPROVEMENT_ENABLED,
    _build_sprint_kickoff_story,
    _build_sprint_retrospective,
    _coerce_json_dict,
    _complete_sprint_and_plan_follow_up,
    _finalize_sprint_if_ready,
    _format_retrospective_summary,
)
from services.loop.worker.deliver import (  # noqa: F401 - re-exported
    _build_review_payload,
    _deliver_artifacts,
    _delivery_comment,
    _detect_project_test_command,
    _detect_test_command,
    _extract_artifact_targets,
    _normalize_artifact_content,
    _on_delivery_result,
    _plan_queued_delivery,
    _publish_queued_delivery,
    _queued_delivery_evidence,
    _resolve_workspace_root,
    _run_delivery_tests,
)
from services.loop.worker.execute import (  # noqa: F401 - re-exported
    ARCHITECT_CONSULT_ENABLED,
    LLMCallError,
    _architect_brief,
    _consult_architect,
    _execute_task,
    _sanitize_deliverable_text,
)
from services.loop.worker.intake import (  # noqa: F401 - re-exported
    DELIVERY_QUEUED,
    DELIVERY_RESULTS_CONSUMER,
    DISPATCH_REQUEUE_COOLDOWN_SECONDS,
    LAST_AUTONOMOUS_QUEUE_AT,
    TASK_ASSIGN_ACK_WAIT_SECONDS,
    TASK_ASSIGN_CONSUMER,
    _clear_queue_marker,
    _dispatch_todo_items,
    _mark_queued,
    _queued_recently,
    process_backlog_tasks,
)
from services.loop.worker.messages import (  # noqa: F401 - re-exported
    MESSAGE_BODY_LIMIT,
    _handoff_body,
    _message,
    _post_message,
    _triage_blocked_task,
)
from services.loop.worker.procedures_round import (  # noqa: F401 - re-exported
    _ask_scrum_master_for_procedures,
    _evaluate_procedures,
    _propose_procedures,
)
from services.loop.worker.prompt import (  # noqa: F401 - re-exported
    FILE_MANIFEST_INSTRUCTIONS,
    LESSON_FEEDBACK_CHARS,
    LESSON_FEEDBACK_LIMIT,
    LESSON_SPRINTS,
    _fetch_memory_context,
    _is_lesson,
    _load_procedures_context,
    _load_recent_lessons,
    _load_sprint_completed_tasks,
)
from services.loop.worker.qa_review import (  # noqa: F401 - re-exported
    AUTOPILOT_QA_REWORK_ENABLED,
    MAX_REWORKS,
    _call_llm_for_review,
)
from services.loop.worker.quality_gate import (  # noqa: F401 - re-exported
    MIN_DELIVERABLE_WORDS,
    MIN_DOC_DELIVERABLE_WORDS,
    _send_back_from_quality_gate,
    _validate_minimum_deliverable,
)
from services.loop.worker.record import (  # noqa: F401 - re-exported
    _delivery_gate,
    _delivery_risk,
    _latest_task_verification,
    _record_task_run,
    _reverify_run,
    _summarize_deliverable,
)
from services.loop.worker.review import (  # noqa: F401 - re-exported
    MAX_QA_REVIEW_FAILURES,
    _review_cycle,
    _review_cycle_body,
)
from services.loop.worker.task_run import (  # noqa: F401 - re-exported
    _load_item_runtime_state,
    _load_task_context,
    _sync_manual_freeze_state,
)
