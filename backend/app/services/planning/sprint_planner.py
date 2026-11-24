"""
Sprint planner service — AI-driven foundation sprint planning.

When a new company is bootstrapped, the **PO agent** analyses the discovered
project stack and produces a set of backlog items for "Sprint 0 — Foundations".
These items focus on documentation completeness, missing configuration, and
establishing the knowledge base that agents need to work effectively.

The service delegates to the LLM proxy using the PO agent's assigned model,
falling back to a deterministic task list when the LLM is unavailable.

The prompts are in ``sprint_prompts``, the roster, stack and objective in
``sprint_context`` and the deterministic plans in ``sprint_fallbacks``; this
module re-exports their names, so callers and tests keep importing them here.
"""

import json
from typing import Any

from config import settings
from llm_proxy_client import LLMProxyClient
from logging_config import logger

from services.contracts.task_text import clip_description
from services.planning.objective_text import display_name
from services.planning.sprint_context import (  # noqa: F401 - re-exported
    _DEFAULT_EXECUTION_TEAM,
    _build_stack_summary,
    _build_team_roster,
    _ensure_execution_team,
    _find_po_agent,
    _list_root_files,
    build_self_improvement_objective,
)
from services.planning.sprint_fallbacks import (  # noqa: F401 - re-exported
    _FALLBACK_TASKS,
    SPRINT_NAME_LIMIT,
    _build_execution_fallback,
    _build_self_improvement_fallback,
    _filter_fallback_tasks,
)
from services.planning.sprint_prompts import (
    _EXECUTION_PROMPT,
    _FOUNDATION_PROMPT,
    _SELF_IMPROVEMENT_PROMPT,
)
from services.tasks import dependencies

# A plan is a JSON list of a handful of tasks. One attempt: the template stands in.
PLAN_MAX_TOKENS = 4096


def _client(po: dict[str, Any]) -> LLMProxyClient:
    """The proxy client for the PO's call; its company routes a connection model."""
    client = LLMProxyClient(settings.LLM_PROXY_URL)
    client.max_tokens, client.max_retries = PLAN_MAX_TOKENS, 0
    client.company_id = po.get("company_id")
    # The plan is parsed as JSON: thinking off (services/models/thinking.py).
    client.structured = True
    return client


def _model_failure(result: dict[str, Any]) -> str | None:
    """The ``model_error`` detail of an answer that cannot be a plan, or ``None``.

    The detail starts with the named reason as observed: the proxy's
    (``empty_content_reasoning_exhausted``, ``empty_content``,
    ``upstream_timeout``), the client's (``proxy_read_timeout``,
    ``proxy_connection_lost``), or ``empty_content`` for a 2xx answer with no
    text, which is never parsed as an empty plan.
    """
    if result.get("error"):
        body = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        named = body.get("reason") or result.get("reason")
        message = str(body.get("message") or result["error"])[:500]
        if not named or message.startswith(f"{named}:"):
            return message
        return f"{named}: {message}"
    choice = (result.get("choices") or [{}])[0] or {}
    content = (choice.get("message") or {}).get("content") or ""
    if not str(content).strip():
        finish = choice.get("finish_reason") or "none"
        return f"empty_content: the answer had no text (finish_reason={finish})"
    return None


def _unparseable(exc: Exception, raw: str) -> str:
    """The ``unparseable`` detail: the parse error and the answer's length."""
    return f"{type(exc).__name__}: {exc} (answer of {len(raw)} characters)"


def named_dependencies(task: dict[str, Any]) -> list[str]:
    """What the PO's *task* says it waits for (``depends_on``), as text."""
    raw = task.get("depends_on")
    return [str(d).strip() for d in raw] if isinstance(raw, list) else []


def with_dependencies(
    tasks: list[dict[str, Any]], originals: list[str], named: list[list[str]]
) -> list[dict[str, Any]]:
    """*tasks* each with ``depends_on``: the positions, in *tasks*, of the
    tasks it waits for.

    *named* holds what the PO wrote for each task: a title (as given in
    *originals*, or as cut in ``title``) or a position. Unknown ones and self
    are dropped, and a cycle is broken at the edge that closes it, so
    ``start_sprint`` gets only an order it can follow
    (``services/tasks/dependencies.py``)."""
    position_of: dict[str, int] = {}
    for i, t in enumerate(tasks):
        position_of.setdefault(originals[i].casefold(), i)
        position_of.setdefault(t["title"].casefold(), i)
    graph = {
        i: list(
            dict.fromkeys(
                p
                for d in deps
                for p in [position_of.get(d.casefold(), int(d) if d.isdigit() else -1)]
                if 0 <= p < len(tasks) and p != i
            )
        )
        for i, deps in enumerate(named)
    }
    for i, deps in dependencies.without_cycles(graph).items():
        tasks[i]["depends_on"] = deps
    return tasks


def _foundation_fallback(
    company_name: str, agents: list[dict[str, Any]], reason: str, detail: str
) -> dict[str, Any]:
    """The deterministic foundation plan, with why the model's was not used."""
    logger.warning("PO foundation plan not used (%s): %s", reason, detail)
    return {
        "goal": f"Establish documentation and configuration foundations for {company_name}",
        "tasks": _filter_fallback_tasks(agents),
        "planner_fallback": {"reason": reason, "detail": detail},
    }


# Public API


def plan_foundation_sprint(
    company_name: str,
    agents: list[dict[str, Any]],
    detected_stack: dict[str, Any] | None,
    project_path: str | None,
) -> dict[str, Any]:
    """
    Have the PO agent plan "Sprint 0 — Foundations" via LLM.

    Args:
        company_name:   Display name of the company / project.
        agents:         List of agent dicts (role, name, model, seniority, specialty).
        detected_stack: Stack analysis from the discovery phase (may be None).
        project_path:   Filesystem path to the project root (may be None).

    Returns:
        Dict with ``"goal"`` (str) and ``"tasks"`` (list of backlog dicts).
        Each task has: title, description, priority, assignee.
    """
    po = _find_po_agent(agents)
    po_model = po.get("model", "")
    available_roles = sorted({a.get("role", "Dev") for a in agents})

    # If no LLM model or no stack data, return fallback immediately.
    if not po_model or not detected_stack:
        logger.info("No PO model or stack data — using fallback foundation tasks")
        return {
            "goal": f"Establish documentation and configuration foundations for {company_name}",
            "tasks": _filter_fallback_tasks(agents),
        }

    client = _client(po)

    stack_summary = _build_stack_summary(detected_stack)
    file_listing = (
        _list_root_files(project_path) if project_path else "(no project path)"
    )
    team_roster = _build_team_roster(agents)

    prompt = _FOUNDATION_PROMPT.format(
        agent_name=po.get("name") or "PO",
        seniority=po.get("seniority", "Senior"),
        team_roster=team_roster,
        stack_summary=stack_summary,
        file_listing=file_listing,
        available_roles=", ".join(available_roles),
    )

    logger.info(f"PO planning foundation sprint with model={po_model}")

    raw = ""
    try:
        result = client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=po_model,
            role="PO",
        )

        failure = _model_failure(result)
        if failure:
            return _foundation_fallback(company_name, agents, "model_error", failure)

        raw = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        parsed = json.loads(cleaned)

        goal = parsed.get("goal", f"Foundation sprint for {company_name}")
        tasks = parsed.get("tasks", [])

        valid_roles = set(available_roles)
        validated = []
        for t in tasks:
            if not isinstance(t, dict) or not t.get("title"):
                continue
            assignee = t.get("assignee", "PO")
            if assignee not in valid_roles:
                assignee = "PO"
            validated.append(
                {
                    "title": str(t["title"])[:80],
                    "description": clip_description(t.get("description")),
                    "priority": t.get("priority", "medium")
                    if t.get("priority") in ("critical", "high", "medium", "low")
                    else "medium",
                    "assignee": assignee,
                }
            )

        if not validated:
            logger.warning("LLM returned no valid tasks — using fallback")
            return {
                "goal": goal,
                "tasks": _filter_fallback_tasks(agents),
            }

        logger.info(f"PO planned {len(validated)} foundation tasks")
        return {"goal": goal, "tasks": validated}

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return _foundation_fallback(
            company_name, agents, "unparseable", _unparseable(e, raw)
        )
    except Exception as e:  # noqa: BLE001 - Planning failures use the deterministic fallback.
        logger.error(f"Unexpected error in sprint planning: {e}")
        return {
            "goal": f"Establish documentation and configuration foundations for {company_name}",
            "tasks": _filter_fallback_tasks(agents),
        }


def _plan_with_po(
    template: str,
    fallback: Any,
    label: str,
    default_name: str,
    objective: str,
    scope_size: str,
    agents: list[dict[str, Any]],
    detected_stack: dict[str, Any] | None,
    project_path: str | None,
    team: list[str] | None,
) -> dict[str, Any]:
    """The PO's plan from *template*, or *fallback(team_agents, scope)* when no
    model answers usably. With a sprint *team* (``services/planning/sprint_team.py``)
    only its roles get tasks, else only hired ones; the PO suggests the team.

    The plan carries ``objective`` (what the person wrote, never cut) apart
    from the model's ``goal``, and ``planner_fallback``: None when the model
    answered usably, else ``{"reason", "detail"}`` as the backend observed it
    (``no_model``, ``model_error``, ``unparseable``, ``too_few_tasks``,
    ``unexpected_error``)."""
    from services.planning import sprint_team

    everyone = _ensure_execution_team(agents)
    po = _find_po_agent(everyone)
    po_model = po.get("model", "")
    normalized_scope = scope_size.upper()
    item_limit = {"S": 4, "M": 8, "L": 12}.get(normalized_scope, 8)
    hired = sprint_team.roles_of(agents)
    available_roles = sprint_team.planning_roles(hired, team)
    roster = sprint_team.team_agents(everyone, team) or everyone
    parsed: Any = None

    def finish(plan: dict[str, Any]) -> dict[str, Any]:
        plan["tasks"] = sprint_team.fit_tasks(plan["tasks"], available_roles)
        suggested, reasons = sprint_team.suggested(parsed, team or hired, plan["tasks"])
        return {
            **plan,
            # The person's objective, whole and apart from the model's goal.
            "objective": objective,
            "planner_fallback": None,
            "suggested_team": suggested,
            "team_reasons": reasons,
        }

    def fell_back(reason: str, detail: str) -> dict[str, Any]:
        """The deterministic plan, with why the backend saw no usable answer."""
        plan = finish(fallback(roster, normalized_scope))
        plan["planner_fallback"] = {"reason": reason, "detail": detail}
        logger.warning("PO %s plan not used (%s): %s", label, reason, detail)
        return plan

    if not po_model:
        logger.info("No planning model available — using %s fallback", label)
        return fell_back("no_model", "No planning model is assigned to the PO.")
    prompt = template.format(
        agent_name=po.get("name") or "PO",
        seniority=po.get("seniority", "Senior"),
        team_roster=_build_team_roster(roster),
        objective=objective.strip(),
        scope_size=normalized_scope,
        item_limit=item_limit,
        stack_summary=_build_stack_summary(detected_stack or {}),
        file_listing=_list_root_files(project_path)
        if project_path
        else "(no project path)",
        available_roles=", ".join(available_roles),
    ) + sprint_team.prompt_section(hired, team)
    logger.info(
        "PO planning %s sprint with model=%s scope=%s",
        label,
        po_model,
        normalized_scope,
    )
    raw = ""
    try:
        result = _client(po).chat(
            messages=[{"role": "user", "content": prompt}], model=po_model, role="PO"
        )
        failure = _model_failure(result)
        if failure:
            return fell_back("model_error", failure)
        raw = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        parsed = json.loads(cleaned.strip())
        valid_roles = set(available_roles)
        validated = []
        originals: list[str] = []  # the PO's titles as given, for depends_on
        named: list[list[str]] = []
        for task in parsed.get("tasks", []):
            if not isinstance(task, dict) or not task.get("title"):
                continue
            originals.append(str(task["title"]).strip())
            named.append(named_dependencies(task))
            assignee = str(task.get("assignee", "PO"))
            validated.append(
                {
                    "title": str(task.get("title", ""))[:80],
                    "description": clip_description(task.get("description")),
                    "priority": task.get("priority", "medium")
                    if task.get("priority") in ("critical", "high", "medium", "low")
                    else "medium",
                    "assignee": assignee if assignee in valid_roles else "PO",
                }
            )
        validated = with_dependencies(
            validated[:item_limit], originals, named[:item_limit]
        )
        if len(validated) < 2:
            return fell_back(
                "too_few_tasks",
                f"The answer held {len(validated)} usable task(s); a plan needs 2.",
            )
        return finish(
            {
                "sprint_name": str(parsed.get("sprint_name") or default_name)[:70],
                # The model's one-line goal is bounded; without one, the
                # person's objective stands whole, never a cut of it.
                "goal": str(parsed["goal"])[:140] if parsed.get("goal") else objective,
                "tasks": validated,
            }
        )
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as exc:
        return fell_back("unparseable", _unparseable(exc, raw))
    except Exception as exc:  # noqa: BLE001 - Planning failures use the deterministic fallback.
        logger.exception("Unexpected error planning %s sprint", label)
        return fell_back("unexpected_error", f"{type(exc).__name__}: {exc}")


def plan_execution_sprint(
    company_name: str,
    objective: str,
    scope_size: str,
    agents: list[dict[str, Any]],
    detected_stack: dict[str, Any] | None = None,
    project_path: str | None = None,
    team: list[str] | None = None,
) -> dict[str, Any]:
    """Plan an executable sprint from a strategic prompt."""
    return _plan_with_po(
        _EXECUTION_PROMPT,
        lambda roster, scope: _build_execution_fallback(objective, roster, scope),
        "execution",
        f"Sprint - {display_name(objective, SPRINT_NAME_LIMIT)}",
        objective,
        scope_size,
        agents,
        detected_stack,
        project_path,
        team,
    )


def plan_self_improvement_sprint(
    company_name: str,
    objective: str,
    scope_size: str,
    agents: list[dict[str, Any]],
    detected_stack: dict[str, Any] | None = None,
    project_path: str | None = None,
    team: list[str] | None = None,
) -> dict[str, Any]:
    """Plan a bounded self-improvement sprint using explicit preservation and Scrumban rules."""
    return _plan_with_po(
        _SELF_IMPROVEMENT_PROMPT,
        lambda roster, scope: _build_self_improvement_fallback(
            company_name, roster, scope
        ),
        "self-improvement",
        f"Sprint - Improve {company_name}",
        objective,
        scope_size,
        agents,
        detected_stack,
        project_path,
        team,
    )
