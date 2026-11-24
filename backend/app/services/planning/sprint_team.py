"""A sprint's team: the roles its tasks may go to.

Asking for documentation of the whole project is not asking for a backend
change, so a sprint does not hand work to every role the company hired. When
a sprint is planned (``POST /v1/plan_sprint``) the PO suggests the team for
that request in a fixed shape (``team`` and ``team_reasons`` beside the
tasks); ``suggested`` reads it, and an unusable answer means every role the
company hired. The Manager confirms or changes it: planning again with a
``team`` plans only with those roles, and ``POST /v1/start_sprint`` keeps it
on ``Sprint.team`` and refuses a task whose assignee is outside it. The
worker never runs such a task (``refuse_off_team`` blocks it for a person),
and ``PATCH /v1/backlog`` refuses to move a task to a role outside it.
``None`` means every role the company hired, as before.

How the team is chosen is the company's ``team_selection``: ``suggested``
(the default) pre-ticks the PO's suggestion and the Manager confirms or
changes it; ``automatic`` takes the PO's suggestion without asking. A person
never picks a team from scratch. Planning and starting are separate calls, so
the suggestion travels with the plan: ``plan_sprint`` returns
``suggested_team`` and the form sends it back unchanged to ``start_sprint``
(``suggested_team``), which ``decide`` reads. A sprint the system plans on its
own (an autonomy-3 follow-up, with the tasks it carries over) always takes the
automatic path (``automatic_team``). The sprint records who decided in
``team_source``: ``suggested`` (the PO's suggestion, confirmed), ``person``
(a Manager changed it), ``automatic`` or ``all`` (no team: every hired role).

The team decides who gets tasks, not who judges them: QA's review and the
Verifier still look at every delivery. Roles are compared through
``roles.canonical``, so ``Dev`` and ``Developer`` are one role.
"""

from __future__ import annotations

import re
from typing import Any

from models import Agent, BacklogItem, Sprint
from sqlmodel import Session, select

from services.contracts import roles as catalogue
from services.contracts.task_text import DESCRIPTION_MAX
from services.tasks import task_events

SUGGESTED, AUTOMATIC, PERSON, ALL = "suggested", "automatic", "person", "all"
TEAM_SELECTIONS = (SUGGESTED, AUTOMATIC)
TEAM_SOURCES = (SUGGESTED, PERSON, AUTOMATIC, ALL)
TEAM_LIMIT = 20
REASON_LIMIT = 200
OFF_TEAM_PREFIX = "Blocked: the assignee"


class TeamRefused(Exception):
    """A team or an assignee the request may not have; the detail says why."""

    status = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def roles_of(agents: list[dict[str, Any]]) -> list[str]:
    """The distinct roles of *agents* (the default execution team without any)."""
    from services.planning.sprint_planner import _ensure_execution_team

    return sorted(
        {
            str(a.get("role") or "").strip()
            for a in _ensure_execution_team(agents)
            if str(a.get("role") or "").strip()
        }
    )


def hired_roles(session: Session, company_id: int) -> list[str]:
    """The roles the company hired, as its agents store them."""
    rows = session.exec(select(Agent.role).where(Agent.company_id == company_id)).all()
    return roles_of([{"role": r} for r in rows if r])


def _keys(names: Any) -> set[str]:
    return {catalogue.canonical(str(n)) for n in names or []}


def check_team(team: list[str] | None, hired: list[str]) -> list[str] | None:
    """*team* as hired role names, without repeats; ``None`` stays ``None``.

    An unknown role or an empty team is refused (422).
    """
    if team is None:
        return None
    by_key = {catalogue.canonical(r): r for r in hired}
    chosen: list[str] = []
    for name in team:
        role = by_key.get(catalogue.canonical(str(name)))
        if role is None:
            raise TeamRefused(
                f"Unknown role {name!r} for this sprint's team; "
                f"the company hired: {', '.join(hired)}"
            )
        if role not in chosen:
            chosen.append(role)
    if not chosen:
        raise TeamRefused("A sprint's team needs at least one role")
    return chosen[:TEAM_LIMIT]


def planning_roles(hired: list[str], team: list[str] | None) -> list[str]:
    """The roles a plan may assign: the team's, else every hired role."""
    return list(team) if team else list(hired)


def team_agents(
    agents: list[dict[str, Any]], team: list[str] | None
) -> list[dict[str, Any]]:
    """The agents on *team* (all of them without one), for the PO's roster."""
    if not team:
        return agents
    keys = _keys(team)
    return [a for a in agents if catalogue.canonical(a.get("role")) in keys]


def capabilities_section(roles: list[str]) -> str:
    """What each of *roles* can do with the project, for the planning prompt.

    Only the roles whose execution turn reads and runs (``roles.delivers_files``,
    from ``contracts/tool_steps``: every role unless its profile denies them)
    can look at the project or run anything; the others can still deliver whole files in their answer (the
    ``path=`` manifest) but see only what the prompt shows them. The plan used
    to give the Architect a task that ran validation scripts it had no tool to
    run (issue #766). Empty for no roles.
    """
    names = list(dict.fromkeys(r for r in roles if r))
    files = [r for r in names if catalogue.delivers_files(r)]
    text = [r for r in names if not catalogue.delivers_files(r)]
    if not names:
        return ""
    lines = ["## What each role can do"]
    if files:
        lines.append(
            f"These roles read the project's files, edit them and run its tests: "
            f"{', '.join(files)}."
        )
    if text:
        lines.append(
            f"These roles can write whole new files in their answer, but cannot "
            f"read the project's files nor run a command, a script, a build or "
            f"a test: {', '.join(text)}."
        )
    if files and text:
        lines.append(
            "Give a task that needs the existing code or runs anything (tests, "
            "scripts, a validation, a build) to a role of the first list; give "
            "the others writing and decisions. A task of the second list whose "
            "title runs, checks or changes the code is moved to the first."
        )
    elif text:
        lines.append(
            "No role here can read the project or run anything: plan only "
            "writing and decisions."
        )
    return "\n".join(lines) + "\n"


def prompt_section(hired: list[str], team: list[str] | None) -> str:
    """What the planning prompt says about the team, what each of its roles
    can do (``capabilities_section``) and the answer's shape."""
    if team:
        head = (
            f"## Sprint team\nThe Manager chose this sprint's team: {', '.join(team)}. "
            "Assign every task to one of these roles and to no other.\n"
        )
    else:
        head = (
            "## Sprint team\nNot every request needs every role: documentation of "
            "the project needs the TechWriter, a backend change a backend developer "
            "and QA. Choose the roles this request needs from the company's: "
            f"{', '.join(hired)}.\n"
        )
    return (
        f"\n\n{head}\n{capabilities_section(team or hired)}\nAdd to your JSON object the keys "
        '"team": ["<role>", ...] (the roles this sprint needs) and '
        '"team_reasons": {"<role>": "why, in one sentence"}.\n'
    )


def suggested(
    answer: Any,
    pool: list[str],
    tasks: list[dict[str, Any]] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """The PO's suggested team from its *answer*, within *pool*, and why.

    Every role a planned task is assigned to is on it. An unusable answer
    (no ``team`` list, or none of its roles in *pool*) suggests all of *pool*.
    """
    answer = answer if isinstance(answer, dict) else {}
    raw = answer.get("team")
    by_key = {catalogue.canonical(r): r for r in pool}
    chosen = {
        by_key[k]
        for k in (_keys(raw) if isinstance(raw, list) else set())
        if k in by_key
    }
    if not chosen:
        chosen = set(pool)
    for task in tasks or []:
        role = by_key.get(catalogue.canonical(task.get("assignee")))
        if role:
            chosen.add(role)
    team = [r for r in pool if r in chosen]
    reasons_raw = answer.get("team_reasons")
    reasons = {
        by_key[catalogue.canonical(k)]: str(v).strip()[:REASON_LIMIT]
        for k, v in (reasons_raw.items() if isinstance(reasons_raw, dict) else [])
        if catalogue.canonical(k) in by_key
        and by_key[catalogue.canonical(k)] in team
        and str(v).strip()
    }
    return team, reasons


# What a task title says it does in the project, read without a model (issue
# #766). Only the title: a description may quote the person's objective or a
# specification whole, and words there do not say what the task does. Verb
# forms only, so "Design implementation slice" stays a design task.
_RUNS = re.compile(
    r"\b(re-?run|runs?|running|execute[sd]?|executing|tests?|testing|"
    r"validat(?:e[sd]?|ing|ion)|verif(?:y|ies|ied|ying|ication)|checks?|checking|"
    r"scripts?|builds?|building|lint\w*|scans?|scanning|installs?|installing|"
    r"compil(?:e[sd]?|ing)|deploys?|deploying|migrat(?:e[sd]?|ing)|benchmark\w*|"
    r"reproduc(?:e[sd]?|ing)|ejecut\w*|pruebas?|probar|validar|valida|"
    r"validaci\w*|verific\w*|comprob\w*|comprueba|compilar|desplegar|despliega|"
    r"instalar)\b",
    re.IGNORECASE,
)
_CHANGES = re.compile(
    r"\b(implement(?:s|ed|ing)?|refactor\w*|fix(?:es|ed|ing)?|"
    r"modif(?:y|ies|ied|ying)|patch(?:es|ed|ing)?|edit(?:s|ed|ing)?|"
    r"integrat(?:e[sd]?|ing)|wir(?:e[sd]?|ing)|audit(?:s|ed|ing)?|"
    r"inspect(?:s|ed|ing)?|implementar|implementa|corregir|corrige|"
    r"refactoriz\w*|modificar|modifica|integrar|auditar|audita)\b",
    re.IGNORECASE,
)
_VALIDATES = re.compile(
    r"\b(tests?|testing|validat(?:e[sd]?|ing|ion)|verif(?:y|ies|ied|ying|ication)|"
    r"checks?|checking|qa|pruebas?|probar|validar|valida|validaci\w*|"
    r"verific\w*|comprob\w*|comprueba)\b",
    re.IGNORECASE,
)
_INFRA = re.compile(
    r"\b(deploy\w*|docker\w*|ci|ci/cd|pipelines?|kubernetes|k8s|infra\w*|"
    r"desplegar|despliega|despliegue)\b",
    re.IGNORECASE,
)
REASSIGNED_NOTE = (
    "Reassigned from {role}: this task runs, checks or changes the project's "
    "files, and only a role with the workspace tools can read them or run a "
    "command (issue #766)."
)


def needs_workspace(task: dict[str, Any]) -> bool:
    """Whether *task*, assigned to a role without the workspace tools, needs
    them: its title runs, checks or changes the existing code. The
    TechWriter's documents are whole files delivered in its answer, so its
    task moves only when the title's first word runs or checks something
    ("Verify the links"; "Document the testing strategy" stays). A task for
    a file role, or an acceptance-test task, never moves."""
    role = catalogue.canonical(task.get("assignee"))
    if catalogue.delivers_files(role) or task.get("kind") == "acceptance_tests":
        return False
    title = str(task.get("title") or "").strip()
    if role == catalogue.TECH_WRITER:
        return bool(_RUNS.match(title.split(" ", 1)[0]))
    return bool(_RUNS.search(title) or _CHANGES.search(title))


def file_role_for(task: dict[str, Any], allowed: list[str]) -> str | None:
    """The file role of *allowed* closest to *task*: QA for a validation that
    changes nothing, DevOps for deployment or CI, else a developer role, else
    any file role; None when *allowed* has none."""
    title = str(task.get("title") or "")
    files = [r for r in allowed if catalogue.delivers_files(r)]

    def first(*names: str) -> str | None:
        return next((r for r in files if catalogue.canonical(r) in names), None)

    choice = None
    if _VALIDATES.search(title) and not _CHANGES.search(title):
        choice = first(catalogue.QA)
    if choice is None and _INFRA.search(title):
        choice = first(catalogue.DEVOPS)
    return choice or first(*catalogue.DEVELOPER_ROLES) or (files[0] if files else None)


def to_file_role(task: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
    """*task* given to ``file_role_for`` when it ``needs_workspace``, with
    ``reassigned_from`` and a note in its description saying why; else as is."""
    if not needs_workspace(task):
        return task
    role = file_role_for(task, allowed)
    if role is None:
        return task
    before = str(task.get("assignee") or "")
    note = "\n\n" + REASSIGNED_NOTE.format(role=before)
    body = str(task.get("description") or "")[: DESCRIPTION_MAX - len(note)]
    return {
        **task,
        "assignee": role,
        "reassigned_from": before,
        "description": body + note,
    }


def fit_tasks(tasks: list[dict[str, Any]], allowed: list[str]) -> list[dict[str, Any]]:
    """*tasks* with an assignee outside *allowed* given to an allowed role: a
    developer role first, else the first one. A task that needs the workspace
    tools but went to a role without them goes to a file role of *allowed*
    (``to_file_role``, issue #766). Without *allowed*, unchanged."""
    if not allowed:
        return tasks
    by_key = {catalogue.canonical(r): r for r in allowed}
    fallback = next((r for r in allowed if catalogue.is_developer(r)), allowed[0])
    out = []
    for task in tasks:
        role = by_key.get(catalogue.canonical(task.get("assignee")))
        out.append(to_file_role({**task, "assignee": role or fallback}, allowed))
    return out


def selection_of(company: Any) -> str:
    """The company's ``team_selection``: ``suggested`` unless set to ``automatic``."""
    value = getattr(company, "team_selection", None)
    return value if value in TEAM_SELECTIONS else SUGGESTED


def decide(
    selection: str,
    team: list[str] | None,
    suggested_team: list[str] | None,
) -> tuple[list[str] | None, str]:
    """The sprint's team and ``team_source`` from what ``start_sprint`` got.

    *team* is the team sent (checked), *suggested_team* the PO's suggestion
    the plan carried (checked). In ``automatic`` mode no team is needed: the
    suggestion is the team. A team that differs from the suggestion is a
    person's choice; without either, every hired role.
    """
    if team is None:
        if selection == AUTOMATIC and suggested_team:
            return list(suggested_team), AUTOMATIC
        return None, ALL
    if suggested_team and _keys(team) == _keys(suggested_team):
        return team, AUTOMATIC if selection == AUTOMATIC else SUGGESTED
    return team, PERSON


def automatic_team(
    sprint: Sprint, plan: dict[str, Any], carried: list[Any]
) -> list[int]:
    """A sprint the system planned on its own takes the PO's suggestion as its
    team, with the roles of the tasks it carries over; without a suggestion,
    every hired role. Returns the carried tasks' ids."""
    suggested_team = plan.get("suggested_team")
    if isinstance(suggested_team, list) and suggested_team:
        team = list(dict.fromkeys(str(r) for r in suggested_team))
        for item in carried:
            if item.assignee and not on_team_list(team, item.assignee):
                team.append(item.assignee)
        sprint.team, sprint.team_source = team, AUTOMATIC
    else:
        sprint.team, sprint.team_source = None, ALL
    return [item.id for item in carried]


def on_team_list(team: list[str], assignee: str | None) -> bool:
    return catalogue.canonical(assignee) in _keys(team)


def source_of(sprint: Sprint | None) -> str:
    """How the sprint's team was chosen (``all`` for a sprint from before)."""
    source = getattr(sprint, "team_source", None)
    if source in TEAM_SOURCES:
        return source
    return ALL if team_of(sprint) is None else PERSON


def team_of(sprint: Sprint | None) -> list[str] | None:
    """The sprint's team as stored; ``None`` for every hired role."""
    team = getattr(sprint, "team", None)
    return [str(r) for r in team] if isinstance(team, list) and team else None


def on_team(sprint: Sprint | None, assignee: str | None) -> bool:
    """Whether a task assigned to *assignee* may run in *sprint*."""
    team = team_of(sprint)
    return team is None or catalogue.canonical(assignee) in _keys(team)


def check_assignees(team: list[str] | None, assignees: list[str | None]) -> None:
    """Refuse (422) an assignee outside *team*; nothing to check without one."""
    if team is None:
        return
    outside = [a for a in assignees if catalogue.canonical(a) not in _keys(team)]
    if outside:
        raise TeamRefused(
            f"Assignee {outside[0]!r} is not on this sprint's team ({', '.join(team)})"
        )


def refuse_off_team(session: Session, sprint: Sprint, item: BacklogItem) -> bool:
    """True when *item*'s assignee is outside *sprint*'s team: the task never
    runs. One still to start is blocked for a person, with the reason, so the
    sprint can close around it; the caller skips it either way."""
    if on_team(sprint, item.assignee):
        return False
    if item.status in ("todo", "in_progress"):
        task_events.record(
            session,
            item,
            "blocked",
            task_events.SYSTEM,
            text=(
                f"{OFF_TEAM_PREFIX} {item.assignee or '(none)'} is not on this "
                f"sprint's team ({', '.join(team_of(sprint) or [])}); manual triage: "
                "assign it to a team role and send it back."
            ),
            status="blocked",
        )
        session.add(item)
        session.commit()
    return True
