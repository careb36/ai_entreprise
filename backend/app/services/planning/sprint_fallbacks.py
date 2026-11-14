"""The sprint planner's deterministic plans, used when no model answers usably.

The foundation tasks filtered to the hired roles, and the execution and
self-improvement backlogs bounded by the scope. ``sprint_planner``
re-exports every name.
"""

from typing import Any

from services.planning.sprint_context import _ensure_execution_team

# Fallback tasks when the LLM is unavailable

_FALLBACK_TASKS = [
    {
        "title": "Verify and complete README.md",
        "description": "Ensure the README includes: project description, setup instructions, "
        "architecture overview, contribution guidelines link, and license.",
        "priority": "critical",
        "assignee": "PO",
    },
    {
        "title": "Create or update CONTRIBUTING.md",
        "description": "Document the contribution workflow: branching strategy, commit conventions, "
        "code review process, and PR template.",
        "priority": "high",
        "assignee": "PO",
    },
    {
        "title": "Document system architecture",
        "description": "Create ARCHITECTURE.md with component diagram, data flow, service boundaries, "
        "and technology decisions with rationale.",
        "priority": "critical",
        "assignee": "Architect",
    },
    {
        "title": "Define coding standards and conventions",
        "description": "Document language-specific coding standards, naming conventions, file structure, "
        "and linter/formatter configuration.",
        "priority": "high",
        "assignee": "Dev",
    },
    {
        "title": "Create testing strategy document",
        "description": "Define testing pyramid: unit, integration, e2e test expectations, coverage "
        "targets, and test framework choices.",
        "priority": "high",
        "assignee": "QA",
    },
    {
        "title": "Document CI/CD pipeline and deployment process",
        "description": "Create deployment guide covering CI/CD pipeline stages, environment configuration, "
        "secrets management, and rollback procedures.",
        "priority": "high",
        "assignee": "DevOps",
    },
    {
        "title": "Verify environment configuration files",
        "description": "Ensure .env.example exists with all required variables documented, Docker configs "
        "are complete, and local dev setup is reproducible.",
        "priority": "medium",
        "assignee": "DevOps",
    },
    {
        "title": "Create security baseline document",
        "description": "Document authentication/authorization approach, secrets management policy, "
        "dependency scanning setup, and security review checklist.",
        "priority": "medium",
        "assignee": "Security",
    },
]


def _filter_fallback_tasks(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only fallback tasks whose assignee role exists in the team."""
    available_roles = {a.get("role") for a in agents}
    return [t for t in _FALLBACK_TASKS if t["assignee"] in available_roles]


def _build_execution_fallback(
    objective: str, agents: list[dict[str, Any]], scope_size: str
) -> dict[str, Any]:
    """Deterministic fallback when no planner model is available."""
    team = _ensure_execution_team(agents)
    available_roles = {a.get("role") for a in team}
    defaults = [
        {
            "title": "Refine sprint objective and acceptance criteria",
            "description": f"Turn the prompt into explicit delivery criteria, non-goals, and success checks. Base objective: {objective[:220]}",
            "priority": "critical",
            "assignee": "PO",
        },
        {
            "title": "Design implementation slice",
            "description": "Define the smallest architecture and execution slice that fits this sprint, including dependencies and guardrails.",
            "priority": "high",
            "assignee": "Architect",
        },
        {
            "title": "Implement highest-value workflow",
            "description": "Deliver the core implementation for the selected slice and keep changes bounded to what can be validated this sprint.",
            "priority": "critical",
            "assignee": "Developer",
        },
        {
            "title": "Validate behavior and regression risk",
            "description": "Review the deliverable, verify acceptance criteria, and block unsafe output instead of allowing low-quality completion.",
            "priority": "high",
            "assignee": "QA",
        },
    ]

    scoped_limit = {"S": 4, "M": 8, "L": 12}.get(scope_size.upper(), 8)
    tasks = [t for t in defaults if t["assignee"] in available_roles][:scoped_limit]
    if not tasks:
        tasks = defaults[: min(4, scoped_limit)]

    return {
        "sprint_name": f"Sprint - {objective[:40].strip() or 'Execution'}",
        "goal": f"Plan and execute the highest-value slice of: {objective[:90].strip()}",
        "tasks": tasks,
    }


def _build_self_improvement_fallback(
    company_name: str, agents: list[dict[str, Any]], scope_size: str
) -> dict[str, Any]:
    """Deterministic self-improvement backlog that preserves healthy artifacts."""
    team = _ensure_execution_team(agents)
    available_roles = {a.get("role") for a in team}
    defaults = [
        {
            "title": "Identify the next factory bottleneck",
            "description": "Review current orchestration, UX, and delivery signals to select the highest-value improvement slice. Capture explicit acceptance criteria and confirm what should remain untouched because it already works well.",
            "priority": "critical",
            "assignee": "PO",
        },
        {
            "title": "Design an incremental Scrumban change",
            "description": "Define the smallest process or architecture adjustment that improves flow without destabilizing healthy parts of the product. Make the change bounded enough to move across the board within one sprint.",
            "priority": "high",
            "assignee": "Architect",
        },
        {
            "title": "Implement the next constrained improvement",
            "description": "Apply the selected improvement by extending the current implementation instead of replacing good working assets. Keep the work focused on one bottleneck and wire it into the existing agent orchestration.",
            "priority": "critical",
            "assignee": "Developer",
        },
        {
            "title": "Validate flow and protect healthy behavior",
            "description": "Verify the improved slice and confirm that already-correct artifacts and workflows remain intact. Reject destructive churn and document any new operating rule needed for the next iteration.",
            "priority": "high",
            "assignee": "QA",
        },
    ]

    scoped_limit = {"S": 4, "M": 8, "L": 12}.get(scope_size.upper(), 8)
    tasks = [task for task in defaults if task["assignee"] in available_roles][
        :scoped_limit
    ]
    if not tasks:
        tasks = defaults[: min(4, scoped_limit)]

    return {
        "sprint_name": f"Sprint - Improve {company_name}",
        "goal": f"Improve {company_name} through one bounded Scrumban iteration without rewriting healthy assets.",
        "tasks": tasks,
    }
