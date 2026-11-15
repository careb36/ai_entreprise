"""What the sprint planner tells the PO: the roster, the stack and the objective.

The default execution team when a company has no stored agents yet, the
stack summary and root-file listing for the prompt, the team roster, the
PO's choice among the agents and the bounded objective of a
self-improvement sprint. ``sprint_planner`` re-exports every name.
"""

from pathlib import Path
from typing import Any

from logging_config import logger

_DEFAULT_EXECUTION_TEAM = [
    {
        "role": "PO",
        "name": "PO",
        "seniority": "Senior",
        "specialty": "product planning",
        "model": "",
    },
    {
        "role": "ScrumMaster",
        "name": "Scrum Master",
        "seniority": "Senior",
        "specialty": "delivery orchestration",
        "model": "",
    },
    {
        "role": "Developer",
        "name": "Developer",
        "seniority": "Senior",
        "specialty": "implementation",
        "model": "",
    },
    {
        "role": "QA",
        "name": "QA",
        "seniority": "Senior",
        "specialty": "quality assurance",
        "model": "",
    },
    {
        "role": "Architect",
        "name": "Architect",
        "seniority": "Senior",
        "specialty": "system design",
        "model": "",
    },
    {
        "role": "DevOps",
        "name": "DevOps",
        "seniority": "Senior",
        "specialty": "delivery infrastructure",
        "model": "",
    },
]


def _build_stack_summary(stack: dict[str, Any]) -> str:
    """Format the detected stack dict into a readable summary for the prompt."""
    parts = []
    if stack.get("language"):
        parts.append(f"Primary language: {stack['language']}")
    if stack.get("all_languages"):
        langs = ", ".join(f"{k} ({v} files)" for k, v in stack["all_languages"].items())
        parts.append(f"Languages: {langs}")
    if stack.get("frameworks"):
        parts.append(f"Frameworks: {', '.join(stack['frameworks'])}")
    if stack.get("databases"):
        parts.append(f"Databases: {', '.join(stack['databases'])}")
    if stack.get("infra"):
        parts.append(f"Infrastructure: {', '.join(stack['infra'])}")
    if stack.get("complexity"):
        parts.append(f"Complexity: {stack['complexity']}")
    if stack.get("file_count"):
        parts.append(f"Files scanned: {stack['file_count']}")
    return "\n".join(parts) if parts else "No stack data available."


def _list_root_files(project_path: str, max_files: int = 60) -> str:
    """List files in the project root (1 level deep) to help the PO assess docs."""
    try:
        root = Path(project_path)
        if not root.is_dir():
            return "(project path not accessible)"
        entries = []
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(".git") and entry.name != ".gitignore":
                continue
            marker = "📁" if entry.is_dir() else "📄"
            entries.append(f"  {marker} {entry.name}")
            if len(entries) >= max_files:
                entries.append("  ... and more")
                break
        return "\n".join(entries) if entries else "(empty directory)"
    except Exception as e:  # noqa: BLE001 - Directory context is optional for sprint planning.
        logger.warning(f"Could not list project root: {e}")
        return "(could not read directory)"


def _build_team_roster(agents: list[dict[str, Any]]) -> str:
    """Format the agent list into a readable roster for the prompt."""
    lines = []
    for a in agents:
        role = a.get("role", "Unknown")
        name = a.get("name", "")
        seniority = a.get("seniority", "Senior")
        specialty = a.get("specialty", "")
        model = a.get("model", "unknown")
        label = f"{name} ({role})" if name else role
        lines.append(f"- {label}: {seniority}, specialty={specialty}, model={model}")
    return "\n".join(lines) if lines else "- (no agents defined)"


def _find_po_agent(agents: list[dict[str, Any]]) -> dict[str, Any]:
    """Find the PO agent; fall back to Architect, then first agent."""
    for role_pref in ("PO", "Architect"):
        for a in agents:
            if a.get("role") == role_pref:
                return a
    return (
        agents[0]
        if agents
        else {"role": "PO", "model": "", "name": "PO", "seniority": "Senior"}
    )


def _ensure_execution_team(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Guarantee a minimum delivery roster even when the company has no stored agents yet."""
    if agents:
        return agents
    return list(_DEFAULT_EXECUTION_TEAM)


def build_self_improvement_objective(
    company_name: str,
    detected_stack: dict[str, Any] | None = None,
    sprint_metrics: dict[str, Any] | None = None,
) -> str:
    """Build a bounded, data-driven objective for a self-improvement sprint."""
    stack = detected_stack or {}
    framework_summary = (
        ", ".join(stack.get("frameworks") or []) or "current application stack"
    )
    language_summary = stack.get("language") or "the detected primary language"
    base = (
        f"Inspect the current {company_name} codebase and improve the factory's own autonomous delivery loop using Scrumban. "
        f"Prioritize the next highest-value bottleneck affecting self-planning, orchestration reliability, runtime validation, "
        f"or safe artifact generation for the existing stack ({framework_summary}; primary language: {language_summary}). "
        "Preserve files, workflows, and behaviors that are already correct and complete; iterate by extending or refining them instead of rewriting them. "
        "Choose one bounded slice that can be implemented and verified within a single sprint, keep work-in-progress limited, and prefer changes that let the system improve itself with less human intervention."
    )

    if not sprint_metrics:
        return base

    signals: list[str] = []
    completion = sprint_metrics.get("completion_rate", 100)
    if completion < 80:
        signals.append(
            f"Last sprint completion rate was {completion}% — prioritize reducing task blocking and improving agent resilience."
        )
    rework = sprint_metrics.get("rework_cycles", 0)
    if rework > 3:
        signals.append(
            f"There were {rework} QA rework cycles — focus on improving first-pass output quality or tightening pre-review validation."
        )
    qa_failures = sprint_metrics.get("qa_failures", 0)
    if qa_failures > 2:
        signals.append(
            f"QA failed {qa_failures} times — consider hardening the quality gate or improving agent prompts."
        )
    blocked = sprint_metrics.get("blocked_tasks", 0)
    if blocked > 0:
        signals.append(
            f"{blocked} tasks ended blocked — improve error recovery, task decomposition, or agent fallback behavior."
        )
    for reason in (sprint_metrics.get("blocked_reasons") or [])[:2]:
        signals.append(f"Known block pattern: {reason}")

    if signals:
        return (
            base
            + "\n\nRetro signals from last sprint (use to focus improvement area):\n"
            + "\n".join(f"- {s}" for s in signals)
        )
    return base
