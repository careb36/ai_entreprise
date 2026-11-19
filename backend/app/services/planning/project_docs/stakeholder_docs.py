"""The stakeholder register of a pack (issue #691).

Who has a stake in the project, compiled from the company's records:

- the people in the company, counted by company role (``members``), with what
  each role receives and decides as the routes enforce it, each cited with the
  route that enforces it (``RULES``);
- the company's teams by number, the role each grants and its members counted
  (``stakeholders``, read by ``collect_stakeholders``): a team's name is free
  text that may name a person, so it is never read;
- the approvals of specifications and architectures counted by who gave them
  (the approving account's role now, the Manager agent, or an account no
  longer in the company), each cited with the digest the backend computed;
- the agent roles the company hired (``roles``).

Nobody is named and no address is shown: the records the register reads hold
none. Stakeholders outside the company are a person's section
(``stakeholder_register.external``, ``statements``), quoted as a claim with
the author's role and time. The register reads no retrospective or agent
message. The structure follows the PMBOK Guide, 8th edition (PMI, 2025),
where stakeholders are identified in the Stakeholders performance domain at
the start of the work; the wording is our own (``texts_stakeholders_*``).

``approvals_check`` is the check result the register adds to
``checks.CHECKS`` (Stakeholders, measured); the result names are ``checks``'
own, repeated here so ``checks`` can import this module.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import statements
from services.planning.project_docs.control_docs import cell, rule_ref
from services.planning.project_docs.layout import PMBOK, build, missing_line, tr_of
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_id

KEY = "stakeholders"
EXTERNAL = "stakeholder_register.external"
INPUTS = ("members", "roles", KEY, statements.key_of("stakeholder_register"))
ROLES = ("Owner", "Manager", "Viewer")
APPROVERS = ("Owner", "Manager", "Viewer", "agent", "no_sign_in", "former")
KINDS = ("specification", "architecture")
ROLES_DOC = "services/platform/teams"
# Per company role, the routes that let it receive or decide something:
# (the module that calls ``require_company_role``, the text naming the rule).
RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "Owner": (
        ("routers/documentation", "reg.rule_pack"),
        ("routers/teams", "reg.rule_teams"),
        ("routers/chat_approvals", "reg.rule_chat"),
        ("routers/audit_anchors", "reg.rule_anchors"),
        ("routers/sod", "reg.rule_sod"),
    ),
    "Manager": (
        ("routers/specifications", "reg.rule_specs"),
        ("routers/architectures", "reg.rule_archs"),
        ("routers/reviews", "reg.rule_reviews"),
        ("routers/procedures", "reg.rule_procedures"),
        ("routers/sprint_control", "reg.rule_sprints"),
        ("routers/invitations", "reg.rule_invites"),
        ("routers/webhooks", "reg.rule_webhook"),
        ("routers/person_sections", "reg.rule_sections"),
    ),
    "Viewer": (
        ("services/tasks/board", "reg.rule_board"),
        ("routers/inbox", "reg.rule_inbox"),
        ("routers/documentation", "reg.rule_docs"),
    ),
}
MEASURED, NOT_COMPUTABLE = "measured", "not computable"
MAX_MARKS = 5


def found(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    value = snapshot.get(KEY)
    return value if isinstance(value, dict) else None


def _lower(text: str) -> str:
    """A row label inside a sentence: the company roles keep their capital."""
    return text if text in ROLES else text[:1].lower() + text[1:]


def approval_ref(approval: dict[str, Any], tr) -> dict[str, Any]:
    by = approval.get("by")
    return ref(
        str(approval.get("kind")),
        approval.get("id"),
        sha256=approval.get("sha256"),
        label=tr(
            "reg.label_approval",
            version=cell(approval.get("version"), 10),
            by=_lower(tr(f"reg.by.{by}")) if by in APPROVERS else "?",
        ),
    )


def _cite(src: Sources, refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "-"
    more = f" (+{len(refs) - MAX_MARKS})" if len(refs) > MAX_MARKS else ""
    return src.cite(*refs[:MAX_MARKS]) + more


def _roles(snapshot, src, missing, tr) -> list[str]:
    members = snapshot.get("members") or {}
    if not members:
        missing.append(tr("reg.section_members"))
    people = ref(
        "membership", "people", sha256=digest(members), label=tr("label_people")
    )
    effective = rule_ref(ROLES_DOC, tr("comms.rule_roles"))
    body = [
        tr("reg.roles_heading"),
        "",
        tr("reg.roles_note"),
        "",
        tr("reg.roles_header"),
        "|---|---|---|---|---|",
    ]
    for role in ROLES:
        rules = [rule_ref(doc, tr(rule)) for doc, rule in RULES[role]]
        mark = src.cite(people, effective, *rules)
        body.append(
            f"| {role} | {members.get(role) or 0} | {tr('reg.receives.' + role)} | "
            f"{tr('reg.decides.' + role)} | {mark} |"
        )
    others = sum(
        int(n) for r, n in members.items() if r not in ROLES and isinstance(n, int)
    )
    if others:
        body += ["", tr("reg.unknown_roles", count=others)]
    return body


def _teams(stake, src, tr) -> list[str]:
    body = [tr("reg.teams_heading"), "", tr("reg.teams_note"), ""]
    rows = (stake or {}).get("teams") or []
    if not rows:
        return body + [tr("reg.no_teams")]
    body += [tr("reg.teams_header"), "|---|---|---|---|"]
    for team in rows:
        name = tr("reg.team", id=md_id(team.get("id")))
        role = cell(team.get("role"), 20)
        members = team.get("members")
        count = members if isinstance(members, int) else 0
        mark = src.cite(
            ref(
                "team",
                team.get("id"),
                sha256=team.get("sha256"),
                label=tr("reg.label_team", role=role, count=count),
            )
        )
        body.append(f"| {name} | {role} | {count} | {mark} |")
    return body


def _approvals(stake, src, tr) -> list[str]:
    body = [tr("reg.approvals_heading"), "", tr("reg.approvals_note"), ""]
    rows = (stake or {}).get("approvals") or []
    if not rows:
        return body + [tr("reg.no_approvals")]
    body += [tr("reg.approvals_header"), "|---|---|---|---|"]
    for by in APPROVERS:
        given = [a for a in rows if a.get("by") == by]
        if not given:
            continue
        counts = [sum(1 for a in given if a.get("kind") == k) for k in KINDS]
        mark = _cite(src, [approval_ref(a, tr) for a in given])
        body.append(f"| {tr('reg.by.' + by)} | {counts[0]} | {counts[1]} | {mark} |")
    return body


def _agents(snapshot, src, tr) -> list[str]:
    hired = [str(r) for r in snapshot.get("roles") or []]
    body = [tr("reg.agents_heading"), "", tr("reg.agents_note"), ""]
    if not hired:
        return body + [tr("reg.no_agents")]
    mark = src.cite(
        ref("membership", "roles", sha256=digest(hired), label=tr("label_roles"))
    )
    roles = ", ".join(cell(r, 40) for r in hired)
    return body + [tr("reg.agents_list", roles=roles, mark=mark)]


def register(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The stakeholder register from *snapshot*."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    stake = found(snapshot)
    body = [tr("reg.intro"), ""]
    body += _roles(snapshot, src, missing, tr)
    if stake is None:
        missing.append(tr("reg.section_records"))
        body += ["", missing_line(lang, tr("reg.no_records"))]
    body += [""] + _teams(stake, src, tr)
    body += [""] + _approvals(stake, src, tr)
    body += [""] + _agents(snapshot, src, tr)
    body += ["", f"## {tr('person.register_external')}", ""]
    body += statements.lines(snapshot, EXTERNAL, src)
    body += ["", tr("reg.read_heading"), "", tr("reg.read")]
    return build(
        "stakeholder_register",
        PMBOK,
        body,
        src,
        snapshot,
        INPUTS,
        missing,
        persons=[tr("person.register_external")],
        statements=statements.filled(snapshot, "stakeholder_register"),
    )


def approvals_check(snapshot: dict, src: Sources, tr) -> tuple[str, str, str]:
    """Who gave the approvals (Stakeholders, measured): which share a project
    expects from a person is the Owner's to set."""
    stake = found(snapshot)
    if stake is None:
        return NOT_COMPUTABLE, tr("check.no_stakeholders"), ""
    rows = stake.get("approvals") or []
    if not rows:
        return NOT_COMPUTABLE, tr("check.no_approvals"), ""
    people = sum(1 for a in rows if a.get("by") in ("Owner", "Manager"))
    agent = sum(1 for a in rows if a.get("by") == "agent")
    observed = tr(
        "check.approvals_obs",
        people=people,
        whole=len(rows),
        agent=agent,
        other=len(rows) - people - agent,
    )
    marks = _cite(src, [approval_ref(a, tr) for a in rows])
    return MEASURED, observed, marks
