"""A new company from an exported file (issue #774), the other half of
``company_export``.

``import_company`` reads a ``la-fabrique.company-export/1`` file and creates
a company that the person importing it owns, with the file's agents,
business units and routines. Every value goes through the check its own
route applies (limits, run window, models, schedules, variables, hired
roles); what fails a check is left out and listed, never half applied.

What a file never sets, whatever it holds, and why (``NOT_FROM_A_FILE``):

- what runs on the person's machine or with what it may reach: the stack
  commands, the research hosts, the sandbox and its environment, the
  console producers and theirs;
- what an Owner sets on purpose, audited, on its own route: protected
  paths, the forge policy, the integration review, and the autonomous
  level (a file asking for level 3 gets level 2).

The person sets those afterwards where they are set today. Routines arrive
turned off, so nothing runs before a person has read them; model provider
connections are not created (they need their key and the URL check of their
own route) and are listed under ``to_set_up``. There is no project folder
and no foundation sprint: the company starts empty of work.

The file's ``manifest.sha256`` is the exporter's claim; the backend computes
its own digest of the content and records both, and whether they match, in
the ``company_imported`` audit row. A file edited by hand imports as well:
the check says it was edited, it does not refuse it.
"""

from __future__ import annotations

import re
from typing import Any

from models import Agent, BusinessUnit, Company, CompanyMember
from sqlmodel import Session

from services.contracts import company_limits, run_window
from services.loop import routines
from services.management import company_export
from services.models import company_models
from services.planning import sprint_team
from services.platform.agent_service import AgentService

AUDIT_ACTION = "company_imported"
MAX_AGENTS = 100
MAX_ROUTINES = 100
MAX_UNITS = 50
NAME_MAX = 200
TEXT_MAX = 20_000
_BRANCH = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._/-]{1,100}$")

NOT_FROM_A_FILE: dict[str, str] = {
    "stack_profile": "its commands run on your machine; set them on the Project page",
    "research_hosts": "it widens what the agents may reach; set it on the Execution page",
    "delivery_sandbox": "where agent code runs; set it on the Execution page",
    "sandbox_env": "what agent code sees; set it on the Execution page",
    "console_producers": "what runs a role's tasks; set it on the Models page",
    "console_env": "what the console producers see; set it on the Models page",
    "protected_paths_enabled": "an Owner's audited setting",
    "forge_policy": "an Owner's audited setting; a new company starts at none",
    "integration_review_required": "an Owner's audited setting",
}


class ImportRefused(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.status = 422
        self.detail = detail


def _list(content: dict[str, Any], key: str, most: int) -> list[dict[str, Any]]:
    value = content.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ImportRefused(f"{key} must be a list of objects")
    if len(value) > most:
        raise ImportRefused(f"at most {most} {key} in one import")
    return value


def _text(value: Any, most: int = TEXT_MAX) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value[:most]


def _company(
    session: Session, settings: dict[str, Any], name: str, skipped: list[dict[str, Any]]
) -> Company:
    def skip(field: str, why: str) -> None:
        skipped.append({"what": f"company.{field}", "why": why})

    for field, why in NOT_FROM_A_FILE.items():
        if settings.get(field) not in (None, [], {}, False, "", "none"):
            skip(field, why)
    company = Company(company_name=name, forge_policy="none")
    scope = settings.get("default_scope_size")
    if scope in ("S", "M", "L"):
        company.default_scope_size = scope
    level = settings.get("autonomy_level")
    if level in (1, 2):
        company.autonomy_level = level
    elif level == 3:
        company.autonomy_level = 2
        skip(
            "autonomy_level",
            "the autonomous level is chosen on purpose; set to assisted",
        )
    if settings.get("team_selection") in sprint_team.TEAM_SELECTIONS:
        company.team_selection = settings["team_selection"]
    if isinstance(settings.get("git_workflow"), bool):
        company.git_workflow = settings["git_workflow"]
    base = settings.get("git_base_branch")
    if base is not None:
        if isinstance(base, str) and _BRANCH.match(base):
            company.git_base_branch = base
        else:
            skip("git_base_branch", "not a branch name")
    if settings.get("doc_language") in ("en", "es"):
        company.doc_language = settings["doc_language"]
    try:
        company.limits = company_limits.validate(settings.get("limits"))
    except company_limits.InvalidLimits as exc:
        skip("limits", str(exc))
    try:
        company.run_window = run_window.validate(settings.get("run_window"))
    except ValueError as exc:
        skip("run_window", str(exc))
    session.add(company)
    session.flush()
    try:
        company_models.apply(
            session, company, settings.get("default_model"), settings.get("role_models")
        )
    except company_models.InvalidModelSettings as exc:
        skip("default_model/role_models", str(exc))
    try:
        company_models.apply_policy(session, company, settings.get("model_policy"))
    except company_models.InvalidModelSettings as exc:
        skip("model_policy", str(exc))
    return company


def _agents(
    session: Session, company: Company, rows: list[dict[str, Any]], skipped: list[dict]
) -> int:
    made = 0
    for index, row in enumerate(rows):
        role = (_text(row.get("role"), NAME_MAX) or "").strip()
        if not role:
            skipped.append({"what": f"agents[{index}]", "why": "no role"})
            continue
        model = ""
        try:
            model = (
                company_models.clean_model(
                    session, company.id, row.get("model"), "model"
                )
                or ""
            )
        except company_models.InvalidModelSettings as exc:
            skipped.append(
                {
                    "what": f"agents[{index}].model",
                    "why": f"{exc}; the role default applies",
                }
            )
        budget = row.get("token_budget")
        session.add(
            Agent(
                company_id=company.id,
                name=_text(row.get("name"), NAME_MAX) or "",
                role=role,
                persona=_text(row.get("persona")),
                seniority=_text(row.get("seniority"), NAME_MAX) or "Senior",
                specialty=_text(row.get("specialty"), NAME_MAX) or "",
                model=model,
                system_prompt=_text(row.get("system_prompt")),
                token_budget=budget
                if isinstance(budget, int)
                and not isinstance(budget, bool)
                and budget >= 0
                else 500000,
            )
        )
        made += 1
    session.flush()
    return made


def _units(session: Session, company: Company, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        limit = row.get("token_quota_limit")
        session.add(
            BusinessUnit(
                company_id=company.id,
                unit_name=_text(row.get("unit_name"), NAME_MAX) or "Default",
                token_quota_limit=limit if isinstance(limit, int) and limit >= 0 else 0,
            )
        )
    session.flush()
    return len(rows)


def import_company(
    session: Session,
    file: Any,
    owner_user_id: int | None,
    actor: dict[str, Any] | None,
    company_name: str | None = None,
) -> dict[str, Any]:
    """Create a company from an export *file*; what was created and what was
    left out. Commits."""
    if not isinstance(file, dict):
        raise ImportRefused("the file must be an object")
    manifest = file.get("manifest") if isinstance(file.get("manifest"), dict) else {}
    if manifest.get("format") != company_export.FORMAT:
        raise ImportRefused(f"not a {company_export.FORMAT} file")
    content = file.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("company"), dict):
        raise ImportRefused("the file has no company content")
    agents = _list(content, "agents", MAX_AGENTS)
    units = _list(content, "business_units", MAX_UNITS)
    routine_rows = _list(content, "routines", MAX_ROUTINES)
    connections = _list(content, "provider_connections", 50)
    name = (
        company_name or _text(content["company"].get("company_name"), NAME_MAX) or ""
    ).strip()
    if not name:
        raise ImportRefused("the company needs a name")

    ours = company_export.digest(content)
    claimed = manifest.get("sha256")
    skipped: list[dict[str, Any]] = []
    company = _company(session, content["company"], name[:NAME_MAX], skipped)
    if owner_user_id is not None:
        session.add(
            CompanyMember(
                company_id=company.id, user_id=owner_user_id, human_role="Owner"
            )
        )
    made_agents = _agents(session, company, agents, skipped)
    made_units = _units(session, company, units)
    AgentService().audit_interaction(
        session=session,
        company_id=company.id,
        interaction_id=f"company:{company.id}:import",
        agent_id=str((actor or {}).get("by") or "unknown"),
        action=AUDIT_ACTION,
        details={
            "company_id": str(company.id),
            "user_id": (actor or {}).get("user_id"),
            "token_id": (actor or {}).get("token_id"),
        },
        result={
            "sha256": ours,
            "claimed_sha256": claimed if isinstance(claimed, str) else None,
            "digest_matches": claimed == ours,
            "agents": made_agents,
            "skipped": skipped,
        },
        decision_signature=None,
        cost_tokens=0,
        commit=False,
    )
    session.commit()

    made_routines = 0
    for index, row in enumerate(routine_rows):
        payload = {
            k: row.get(k)
            for k in ("title", "description", "assignee", "schedule", "variables")
        }
        payload["enabled"] = False
        try:
            routines.create(session, company.id, payload, actor)
            made_routines += 1
        except routines.RoutineRefused as exc:
            session.rollback()
            skipped.append({"what": f"routines[{index}]", "why": exc.detail})
    return {
        "company_id": company.id,
        "company_name": company.company_name,
        "created": {
            "agents": made_agents,
            "business_units": made_units,
            "routines": made_routines,
        },
        "skipped": skipped,
        "to_set_up": [
            {
                "provider_connection": _text(c.get("name"), NAME_MAX),
                "provider": _text(c.get("provider"), NAME_MAX),
                "base_url": _text(c.get("base_url"), 500),
            }
            for c in connections
        ]
        + [s for s in file.get("required_secrets") or [] if isinstance(s, dict)][:100],
        "routines_off": made_routines > 0,
        "sha256": ours,
        "digest_matches": claimed == ours,
    }
