"""The LLM call, the Architect consultation and the edit plan (ADR 0014, the task worker's execute stage).

``_execute_task`` has the agent produce a deliverable through the LLM proxy,
with the workspace, research and MCP tools of its role (each call an
observation); ``_architect_brief`` asks the company's Architect for a brief,
or an edit plan (``edit_plan``), before a design-sensitive, reworked or hard
task of a coding role, QA, PO or ScrumMaster (the team's own advice, on the
Architect's role default model unless the company chose one; issue #750), and
``_consult_architect`` is that call, with the project's read tools
(``architect_brief``). Moved out of ``task_worker``, which re-exports them.
"""

import asyncio
from typing import Any

from config import settings
from database import engine
from llm_proxy_client import LLMProxyClient
from logging_config import setup_logger
from models import Agent
from nats_client import NATSClient
from sqlmodel import Session, select

from services.contracts import tool_calls
from services.learning.architect import track_record as architect_track_record
from services.learning.architect import track_record_context as architect_record_context
from services.loop import edit_plan, sprint_control, task_routing
from services.loop.answers import BRIEF_MAX_TOKENS
from services.loop.answers import llm_content as _llm_content
from services.loop.answers import llm_failure as _llm_failure
from services.loop.answers import llm_finish_reason as _llm_finish_reason
from services.loop.carry_over import previous_attempt_context
from services.loop.worker import (
    architect_brief,
    board_writes,
    messages,
    prompt,
    prompt_budget,
    related,
    sprint_notes,
    wiki_section,
)
from services.models import edit_format, model_profile, model_routing
from services.platform import company_limits_setting
from services.platform.glassroom import publish_glassroom
from services.platform.orchestration_metrics import (
    ACTION_ARCHITECT_CONSULT,
    ACTION_EXECUTE_TASK,
    ARCHITECT_COMMENT_AUTHOR,
    KPI_ROLE_ARCHITECT,
    build_architect_consult_prompt,
    classify_review_result,
    format_architect_consult_comment,
    record_agent_turn,
    should_consult_architect,
)
from services.tasks import task_projection
from services.tools import developer_tools, mcp_tools, workspace_edits

logger = setup_logger("fabrique-task-worker")

ARCHITECT_CONSULT_ENABLED = settings.ARCHITECT_CONSULT_ENABLED


class LLMCallError(RuntimeError):
    """An LLM call that produced no usable answer; its text goes on the board.

    ``credit_provider`` names the provider when it refused for lack of credit
    (``sprint_control.credit_failure``): the sprint is paused, not the task blocked.
    """

    def __init__(self, message: str, credit_provider: str | None = None):
        super().__init__(message)
        self.credit_provider = credit_provider


def _developer_system_prompt(agent: dict[str, Any]) -> str:
    """The agent's own system prompt, else one from its seniority, role and specialty."""
    return agent.get("system_prompt") or (
        f"You are a {agent.get('seniority', 'Senior')} {agent.get('role', 'Developer')} "
        f"specializing in {agent.get('specialty', 'software engineering')}. "
        "Produce clear, actionable, production-quality deliverables."
    )


def _developer_prompt(
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint_name: str,
    memory_context: str | None,
    completed_tasks_context: str | None,
    architect_advice: str | None,
    company_id: int | None,
    lessons_context: str | None,
    procedures_context: str | None,
    requirements_context: str | None,
    budget: int | None = None,
    wiki_context: str | None = None,
    off: frozenset[str] = frozenset(),
    output_format: str | None = None,
) -> tuple[str, list[str], dict[str, Any]]:
    """The task's user prompt, the pending rework feedback it carries, and its measure.

    The prompt is a list of named sections (``prompt_budget``): measured one
    by one, those named in *off* left out (``PROMPT_SECTIONS_OFF``) and, over
    *budget*, the least needed left out first (#443).
    """
    # Sections run from the most stable to the most variable, so the providers'
    # prefix cache bills the shared start of the prompt at the cached rate:
    # format, procedures and lessons first; the task and its history next;
    # what changes between attempts (the brief, the review feedback) last.
    # The priority is the order they are left out over budget (0: never).
    Section = prompt_budget.Section
    sections = [
        # The model's edit format (#689); the format before it by default.
        Section(
            "output_format",
            (output_format or prompt.FILE_MANIFEST_INSTRUCTIONS).lstrip("\n"),
        ),
        Section("standards_and_procedures", procedures_context or "", 2),
    ]
    if lessons_context:
        sections.append(
            Section(
                "lessons",
                "\n\n## Lessons from Recent Sprints\n"
                "How this company's last sprints went and what reviewers sent back. "
                "The review requests are reviewers' opinions, not verified facts; use "
                "them to avoid repeating the same mistakes.\n\n"
                f"{lessons_context[:3000]}",
                4,
            )
        )
    sections += [
        Section(
            "task",
            f"\n\n## Task: {item['title']}\n\n"
            f"{item.get('description', '')}\n\n"
            f"Sprint: {sprint_name}\n"
            f"Priority: {item.get('priority', 'medium')}\n\n"
            "Produce a complete, professional deliverable for this task. "
            "Be thorough and specific to the project context.",
        ),
        Section("requirements", requirements_context or "", 2),
        Section(
            "repository",
            developer_tools.repository_section(
                company_id, agent.get("role"), item.get("id"), engine=engine
            ),
            3,
        ),
        # The files around the task's modules and earlier writes (#438); empty without seeds.
        Section(
            "related_code",
            related.related_code_section(item, agent, company_id, engine=engine),
            3,
        ),
    ]
    # The project wiki's pages for this task (claims, #445), before the vector
    # memory, which is only fetched when no page applies.
    if wiki_context is None:
        wiki_context = wiki_section.section(item, company_id, engine=engine)["text"]
    sections.append(
        Section(wiki_section.SECTION_NAME, wiki_context or "", wiki_section.PRIORITY)
    )
    if memory_context:
        sections.append(
            Section(
                "memory",
                "\n\n## Memory: Relevant Past Decisions\n"
                "These are decisions and deliverables from previous sprints in this factory. "
                "Use them to maintain consistency and avoid repeating past mistakes.\n\n"
                f"{memory_context[:3000]}",
                5,
            )
        )
    # A task carried over from an earlier sprint says what blocked it there.
    sections.append(Section("previous_attempt", previous_attempt_context(item), 2))
    if completed_tasks_context:
        sections.append(
            Section(
                "completed_tasks",
                "\n\n## Sprint Context: Already Completed Tasks\n"
                f"{completed_tasks_context[:2000]}",
                5,
            )
        )
    # The sprint's notes that concern this task (claims, #439); cut before the code.
    sections.append(
        Section(
            sprint_notes.SECTION_NAME,
            sprint_notes.board_section(item, company_id, engine=engine),
            sprint_notes.PRIORITY,
        )
    )
    # A plan, or guidance.
    sections.append(
        Section("architect_brief", edit_plan.prompt_section(architect_advice))
    )

    rework_feedback = task_projection.record_from_dict(item).pending_rework_feedback()
    if rework_feedback:
        sections.append(
            Section(
                "review_feedback",
                "\n\n## Review feedback to address\n"
                "Your previous deliverable for this task was sent back. Fix every point "
                "below and deliver the complete result again.\n\n"
                + "\n".join(f"- {text[:1500]}" for text in rework_feedback),
            )
        )
    user_prompt, measure = prompt_budget.fit(sections, budget, off)
    return user_prompt, rework_feedback, measure


def _execute_task(
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint_name: str,
    memory_context: str | None = None,
    completed_tasks_context: str | None = None,
    architect_advice: str | None = None,
    company_id: int | None = None,
    sprint_id: int | None = None,
    lessons_context: str | None = None,
    procedures_context: str | None = None,
    requirements_context: str | None = None,
    wiki_context: str | None = None,
) -> str:
    """
    Call the LLM to produce a deliverable for the given backlog item.

    Runs synchronously (``asyncio.to_thread``). Returns the LLM's text. An
    empty agent model resolves to the role's default; the model the proxy
    reports is stored as ``agent["served_model"]``. Raises ``LLMCallError``
    on a failed or empty call. With ``company_id`` the turn goes to
    ``audit_log`` (role, company, token cost) for the KPIs.
    """
    client = LLMProxyClient(settings.LLM_PROXY_URL)
    client.company_id = company_id

    # The model by difficulty (#319): the estimate and the routing are stored
    # on the task and kept for the run's evidence; the served model stays apart.
    decision = task_routing.route_task(
        item,
        agent,
        company_id,
        rework=bool(task_projection.record_from_dict(item).pending_rework_feedback()),
        editor=bool(edit_plan.plan_in(architect_advice)),
        engine=engine,
    )
    model = decision["model"]
    agent["difficulty"], agent["routing"] = decision["difficulty"], decision["routing"]
    role = agent.get("role", "Dev")
    # The requested model's edit format (#689), kept for the run's evidence.
    agent["edit_format"] = edit_format.format_for(model, settings.EDIT_FORMAT_BY_MODEL)
    # The model's profile and the limits it sets, with their sources (#766).
    try:
        with Session(engine) as session:
            own: dict[str, Any] | None = company_limits_setting.stored(
                session, company_id
            )
    except Exception as exc:  # noqa: BLE001 - As the routing: the deployment's limits.
        logger.warning(
            "Company limits unavailable for item=%s: %s", item.get("id"), exc
        )
        own = None
    limits = agent["model_profile"] = model_profile.profile(model, own)
    limits["company_limits_read"] = own is not None
    client.max_tokens = limits["max_output_tokens"] or None

    system_prompt = _developer_system_prompt(agent) + prompt.language_note(
        company_id, engine
    )
    user_prompt, _rework_feedback, measure = _developer_prompt(
        item,
        agent,
        sprint_name,
        memory_context,
        completed_tasks_context,
        architect_advice,
        company_id,
        lessons_context,
        procedures_context,
        requirements_context,
        prompt_budget.budget_for(agent.get("role")),
        wiki_context,
        off=prompt_budget.sections_off(),
        output_format=prompt.file_instructions(agent["edit_format"]["format"]),
    )
    # The backend's own measure of what it sent: an observation beside the run.
    measure["system_tokens"] = prompt_budget.estimate_tokens(system_prompt)
    # Wiki on or off, and whether the vector memory was the fallback (#445, bench).
    measure["wiki"] = {
        "enabled": settings.WIKI_ENABLED,
        "chars": next(
            (s["chars"] for s in measure["sections"] if s["name"] == "wiki"), 0
        ),
        "memory": bool(memory_context),
    }
    agent["prompt_sections"] = measure

    who = agent.get("name") or role
    logger.info(
        "Agent %s (%s) working on: %s [model=%s]", who, role, item["title"], model
    )

    def record(llm_result: dict[str, Any], preview: str) -> None:
        if company_id is not None:
            record_agent_turn(
                company_id=company_id,
                sprint_id=sprint_id,
                backlog_item_id=item.get("id"),
                agent={**agent, "model": llm_result.get("served_model") or model},
                action=ACTION_EXECUTE_TASK,
                llm_result=llm_result,
                result_preview=preview,
            )

    # The workspace and MCP tools for this role: each call an observation.
    toolbox = developer_tools.toolbox_for(
        company_id, role, engine=engine, item_id=item.get("id")
    )
    developer_tools.apply_limits(
        toolbox,
        rounds=limits["tool_rounds"],
        result_max_chars=limits["result_max_chars"],
    )
    # The tools offered (and withheld, with why) go on the evidence; a split
    # is pointed to the move tool when it is offered.
    agent["tools_offered"] = workspace_edits.offered(toolbox)
    user_prompt += workspace_edits.move_note(agent["tools_offered"])
    try:
        result, tool_calls = mcp_tools.chat_with_tools(
            client,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model,
            role,
            toolbox,
            on_round=lambda r, calls: record(
                r, "tool calls: " + mcp_tools.summarize(calls)
            ),
        )
    finally:
        if toolbox is not None:
            toolbox.close()  # The tools' edits come back as files to deliver.
    agent["tool_calls"] = tool_calls
    agent["tool_edits"] = getattr(toolbox, "edited_files", [])

    failure = _llm_failure(result, model)
    if failure:
        raise LLMCallError(failure, sprint_control.credit_failure(result))

    content = _llm_content(result)
    agent["served_model"] = result.get("served_model") or None
    agent["finish_reason"] = _llm_finish_reason(result)
    record(result, content)
    return content


def _consult_architect(
    item: dict[str, Any],
    developer: dict[str, Any],
    sprint: dict[str, Any],
    prior_feedback: list[str] | None = None,
) -> str:
    """Ask the company's Architect agent for a short brief on *item*, with the
    repository map. Synchronous (via ``asyncio.to_thread``); ``""`` when no
    Architect exists or the call fails (no consultation happened). Successful
    calls are written to ``audit_log`` with ``role=Architect``."""
    with Session(engine) as session:
        architect_row = session.exec(
            select(Agent).where(
                Agent.company_id == sprint["company_id"],
                Agent.role == KPI_ROLE_ARCHITECT,
            )
        ).first()
        cm = model_routing.company_model(session, sprint["company_id"], "Architect")
        # Its record from the boards; a failed count only leaves it out.
        record = ""
        if architect_row:
            try:
                record = architect_record_context(
                    architect_track_record(session, sprint["company_id"])
                )
            except Exception as exc:  # noqa: BLE001 - The brief goes without its record.
                logger.warning(
                    "Architect record unavailable for company=%s: %s",
                    sprint["company_id"],
                    exc,
                )

    if not architect_row:
        logger.info(
            "No Architect agent for company=%s; skipping consultation for item=%s",
            sprint["company_id"],
            item.get("id"),
        )
        return ""

    architect = {
        "role": architect_row.role or KPI_ROLE_ARCHITECT,
        "name": architect_row.name or KPI_ROLE_ARCHITECT,
        "model": model_routing.resolve_agent_model(
            {"model": architect_row.model or cm, "role": KPI_ROLE_ARCHITECT}
        ),
        "seniority": architect_row.seniority,
        "specialty": architect_row.specialty,
        "system_prompt": architect_row.system_prompt,
    }
    system_prompt = architect["system_prompt"] or (
        f"You are a {architect.get('seniority') or 'Senior'} Software Architect "
        f"specializing in {architect.get('specialty') or 'system design'}. "
        "You advise developers with concise, decisive technical briefs."
    )
    system_prompt += prompt.language_note(sprint["company_id"], engine)
    request = build_architect_consult_prompt(
        item, developer, sprint.get("name", ""), prior_feedback or (), record
    )
    request += edit_plan.request_section(item)
    request += developer_tools.repository_section(
        sprint["company_id"], KPI_ROLE_ARCHITECT, item.get("id"), engine=engine
    )

    client = LLMProxyClient(settings.LLM_PROXY_URL)
    client.max_tokens, client.company_id = BRIEF_MAX_TOKENS, sprint["company_id"]

    def record(llm_result: dict[str, Any], preview: str, calls: Any = None) -> None:
        model = llm_result.get("served_model") or architect["model"]
        record_agent_turn(
            company_id=sprint["company_id"],
            sprint_id=sprint.get("id"),
            backlog_item_id=item.get("id"),
            agent={**architect, "model": model},
            action=ACTION_ARCHITECT_CONSULT,
            llm_result=llm_result,
            result_preview=preview,
            tool_calls=calls,
        )

    # The project's read tools, each call an observation (``architect_brief``).
    toolbox = architect_brief.read_tools(sprint["company_id"], item.get("id"), engine)
    try:
        result, observed = architect_brief.ask(
            client, system_prompt, request, architect, toolbox, record
        )
    except Exception as exc:  # noqa: BLE001 - Network/proxy failure must not block the developer.
        logger.warning(
            "Architect consultation failed for item=%s: %s", item.get("id"), exc
        )
        return ""

    failure = _llm_failure(result, architect["model"])
    if failure:
        logger.warning(
            "Architect consultation returned no advice for item=%s: %s",
            item.get("id"),
            failure,
        )
        return ""

    advice = _llm_content(result)
    if tool_calls.written_as_text(advice):
        # A tool call typed as text, not made through the tools: no brief.
        logger.warning(
            "Architect answered item=%s with a tool call written as text; no brief",
            item.get("id"),
        )
        advice = ""

    record(result, advice, observed)
    return advice


async def _architect_brief(
    nats_client: NATSClient,
    item: dict[str, Any],
    agent: dict[str, Any],
    sprint: dict[str, Any],
) -> str:
    """Consult the Architect before a design-sensitive, reworked or hard task.

    The brief (``""`` when none was asked or none came) goes into the
    Developer's prompt; the question and answer are agent messages, the brief
    a board comment and Glassroom events.
    """
    # Architect consultation for design-sensitive or reworked Developer tasks.
    # Recorded as an ``ARCHITECT_CONSULTED:`` comment so the orchestration
    # health analytics can count it.
    architect_advice = ""
    if should_consult_architect(
        item, agent, item.get("comments"), enabled=ARCHITECT_CONSULT_ENABLED
    ) or (ARCHITECT_CONSULT_ENABLED and edit_plan.wanted(item, agent)):
        prior_feedback = [
            str(c.get("text", ""))
            for c in (item.get("comments") or [])
            if isinstance(c, dict)
            and classify_review_result(str(c.get("text", ""))) == "NEEDS_WORK"
        ][-2:]
        await publish_glassroom(
            nats_client,
            sprint["id"],
            {
                "type": "status",
                "semantic_type": "task.architect_consult_started",
                "source": "task_worker",
                "agent": ARCHITECT_COMMENT_AUTHOR,
                "role": ARCHITECT_COMMENT_AUTHOR,
                "task_id": item["id"],
                "task_status": "in_progress",
                "phase": "architect_consult",
                "task": item["title"],
                "message": f"📐 {agent.get('name') or agent['role']} is consulting the Architect on: {item['title']}",
                "message_key": "glassroom.events.architect_consulting",
                "message_params": {
                    "agent": agent.get("name") or agent["role"],
                    "task": item["title"],
                },
            },
        )
        architect_advice = await asyncio.to_thread(
            _consult_architect, item, agent, sprint, prior_feedback
        )
        if architect_advice:
            # Recorded once the Architect has answered: a company with
            # no Architect asked no one.
            question = (
                f'Before I implement "{item["title"]}", what design should I follow?'
            )
            if prior_feedback:
                question += " The last review sent it back: " + " / ".join(
                    str(text)[:300] for text in prior_feedback[:3]
                )
            await asyncio.to_thread(
                messages._post_message,
                company_id=sprint["company_id"],
                sprint_id=sprint["id"],
                item_id=item["id"],
                from_name=agent.get("name") or agent["role"],
                from_role=agent["role"],
                to_role=ARCHITECT_COMMENT_AUTHOR,
                kind="question",
                body=question,
            )
            await asyncio.to_thread(
                messages._post_message,
                company_id=sprint["company_id"],
                sprint_id=sprint["id"],
                item_id=item["id"],
                from_name=ARCHITECT_COMMENT_AUTHOR,
                from_role=ARCHITECT_COMMENT_AUTHOR,
                to_role=agent["role"],
                kind="answer",
                body=architect_advice,
            )
            await asyncio.to_thread(
                board_writes._update_item_status,
                item["id"],
                "in_progress",
                comment_text=format_architect_consult_comment(architect_advice),
                comment_author=ARCHITECT_COMMENT_AUTHOR,
                kind="advised",
            )
            await publish_glassroom(
                nats_client,
                sprint["id"],
                {
                    "type": "message",
                    "semantic_type": "task.architect_consulted",
                    "source": "task_worker",
                    "agent": ARCHITECT_COMMENT_AUTHOR,
                    "role": ARCHITECT_COMMENT_AUTHOR,
                    "task_id": item["id"],
                    "task_status": "in_progress",
                    "phase": "architect_consult",
                    "task": item["title"],
                    "message": f"📐 Architect brief delivered for: {item['title']}",
                    "message_key": "glassroom.events.architect_consulted",
                    "message_params": {"task": item["title"]},
                    "preview": architect_advice[:200],
                },
            )
    return architect_advice


def _sanitize_deliverable_text(deliverable: str) -> str:
    """Repair small formatting truncations that should not block autonomous progress."""
    text = (deliverable or "").strip()
    if not text:
        return ""

    if text.count("```") % 2 != 0:
        text = f"{text}\n```"

    return text
