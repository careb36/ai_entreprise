"""The documentation pack's stakeholder register (issue #691,
``project_docs/stakeholder_docs.py`` over ``project_docs/collect_stakeholders.py``).

EARS criteria, each a test below:

- When a pack is compiled, the stakeholder register shall list each company
  role with the number of people in it, what the role receives and what it
  decides as the routes enforce it, each cited with the membership digest and
  the rule behind it.
- The register shall list the company's teams by number with the role each
  grants and its members counted, never a team's name, a person's name or an
  address.
- The register shall count the approvals of specifications and architectures
  by who gave them (the approving account's company role now, the Manager
  agent, or an account no longer in the company), each approval cited with
  the digest the backend computed over it.
- Stakeholders outside the company shall stay a person's section, quoted as
  a claim with the author's role and time when filled.
- The register shall read no retrospective or other claim; when a membership,
  a team or an approval changes, the lint shall flag it as stale, and a
  channel's change shall not.
- The check results shall measure who gave the approvals.
"""

import copy
import re

from project_docs_helpers import _docs, _empty, _Pack, _snapshot
from services.planning.project_docs import (
    checks,
    pack,
    stakeholder_docs,
    statements,
)
from services.planning.project_docs.refs import digest


def _in(lang, snapshot=None):
    out = copy.deepcopy(snapshot or _snapshot())
    out["language"] = lang
    return out


def _section(md, heading):
    return md.split(heading, 1)[1].split("\n## ", 1)[0]


def _row(md, heading, start):
    return next(
        line for line in _section(md, heading).splitlines() if line.startswith(start)
    )


def _sources_of(doc, marks_cell):
    ids = re.findall(r"S(\d+)", marks_cell)
    by_mark = {s["mark"]: s for s in doc["sources"]}
    return [by_mark[f"S{i}"] for i in ids]


ROLES = "## People in the company and what they decide"
TEAMS = "## Teams"
APPROVALS = "## Approvals given"
AGENTS = "## The agent team"


# --- the register ------------------------------------------------------------------


def test_the_register_is_placed_cited_and_compiled_in_the_pack():
    docs, _ = _docs(_snapshot())
    register = docs["stakeholder_register"]
    assert register["title"] == "Stakeholder register"
    assert (register["domain"], register["focus_area"]) == (
        "Stakeholders",
        "Initiating",
    )
    md = register["markdown"]
    assert "PMBOK Guide, 8th edition (PMI, 2025)" in md
    assert register["persons"] == ["Stakeholders outside the company"]
    assert register["missing"] == []
    assert "stakeholder_register.md" in docs["index"]["markdown"]
    marks = set(re.findall(r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", md))))
    assert marks == {s["mark"][1:] for s in register["sources"]}


def test_each_role_is_listed_with_what_the_routes_let_it_receive_and_decide():
    register = stakeholder_docs.register(_snapshot())
    md = register["markdown"]
    manager = _row(md, ROLES, "| Manager ")
    assert "| 2 |" in manager
    assert "approves specifications and architectures" in manager
    assert "the review inbox" in manager
    cited = {s["id"] for s in _sources_of(register, manager.rsplit("|", 2)[1])}
    assert {"routers/specifications", "routers/architectures", "routers/reviews"} <= (
        cited
    )
    membership = next(s for s in register["sources"] if s["kind"] == "membership")
    assert membership["sha256"] == digest(_snapshot()["members"])
    owner = _row(md, ROLES, "| Owner ")
    assert "| 1 |" in owner and "approves the documentation pack" in owner
    owner_rules = {s["id"] for s in _sources_of(register, owner.rsplit("|", 2)[1])}
    assert {"routers/documentation", "routers/teams"} <= owner_rules
    viewer = _row(md, ROLES, "| Viewer ")
    assert "| 0 |" in viewer and "nothing: a Viewer reads" in viewer


def test_teams_are_shown_by_number_with_their_role_and_members():
    register = stakeholder_docs.register(_snapshot())
    row = _row(register["markdown"], TEAMS, "| Team 4 ")
    assert "| Manager | 2 |" in row
    team = next(s for s in register["sources"] if s["kind"] == "team")
    assert team["sha256"] == _snapshot()["stakeholders"]["teams"][0]["sha256"]
    none = copy.deepcopy(_snapshot())
    none["stakeholders"]["teams"] = []
    assert (
        "The company has formed no team."
        in (stakeholder_docs.register(none)["markdown"])
    )


def test_approvals_are_counted_by_who_gave_them_and_cited():
    register = stakeholder_docs.register(_snapshot())
    md = register["markdown"]
    assert "| 1 | 0 |" in _row(md, APPROVALS, "| Manager ")
    assert "| 0 | 1 |" in _row(md, APPROVALS, "| Manager agent")
    former = _row(md, APPROVALS, "| An account no longer in the company ")
    assert "| 1 | 0 |" in former
    assert not any(
        line.startswith("| Owner ") for line in _section(md, APPROVALS).splitlines()
    )
    approvals = _snapshot()["stakeholders"]["approvals"]
    cited = {
        (s["kind"], s["id"], s.get("sha256"))
        for s in register["sources"]
        if s["kind"] in ("specification", "architecture")
    }
    for a in approvals:
        assert (a["kind"], str(a["id"]), a["sha256"]) in cited
    nothing = copy.deepcopy(_snapshot())
    nothing["stakeholders"]["approvals"] = []
    assert (
        "No specification or architecture has been approved yet."
        in (stakeholder_docs.register(nothing)["markdown"])
    )


def test_the_agent_team_is_the_roles_hired():
    md = stakeholder_docs.register(_snapshot())["markdown"]
    assert "Developer, PO, QA, ScrumMaster" in _section(md, AGENTS)
    assert "The company has hired no agent yet." in _section(
        stakeholder_docs.register(_empty())["markdown"], AGENTS
    )


def test_external_stakeholders_are_a_persons_section():
    plain = stakeholder_docs.register(_snapshot())
    assert plain["markdown"].count("**For a person to complete.**") == 1
    filled = copy.deepcopy(_snapshot())
    filled[statements.key_of("stakeholder_register")] = {
        "stakeholder_register.external": {
            "id": 31,
            "section": "stakeholder_register.external",
            "text": "The sponsor approves each release; the regulator reads the audit.",
            "author_role": "Owner",
            "created_at": "2026-11-18T11:00:00",
            "statement_sha256": "c" * 64,
        }
    }
    register = stakeholder_docs.register(filled)
    md = register["markdown"]
    assert "The sponsor approves each release" in md
    assert "Statement by a person (Owner), recorded 2026-11-18 11:00 UTC." in md
    assert "**For a person to complete.**" not in md
    [summary] = register["statements"]
    assert summary["section"] == "stakeholder_register.external"
    statement = next(s for s in register["sources"] if s["kind"] == "statement")
    assert statement["claim"] is True and statement["sha256"] == "c" * 64
    assert register["input_sha256"] != plain["input_sha256"]
    for heading in (ROLES, TEAMS, APPROVALS):
        assert _section(md, heading) == _section(plain["markdown"], heading)
    assert "stakeholder_register.external" in statements.SECTIONS


def test_without_records_the_register_says_what_is_missing():
    assert stakeholder_docs.register(_empty())["missing"] == ["company members"]
    gone = _empty()
    gone.pop("stakeholders")
    register = stakeholder_docs.register(gone)
    assert register["missing"] == ["company members", "teams and approvals"]
    assert "| 0 |" in _row(register["markdown"], ROLES, "| Owner ")


# --- claims and drift --------------------------------------------------------------


def test_the_register_reads_no_claim():
    snapshot = _snapshot()
    before = stakeholder_docs.register(snapshot)
    changed = copy.deepcopy(snapshot)
    changed["retrospectives"][0]["body"] = "Everyone outside loves the project."
    changed["procedures"] = []
    again = stakeholder_docs.register(changed)
    assert again["markdown"] == before["markdown"]
    assert again["input_sha256"] == before["input_sha256"]
    assert "retrospectives" not in again["inputs"]
    assert all(not s.get("claim") for s in again["sources"])


def test_the_lint_flags_the_register_when_what_it_read_changes():
    base = _snapshot()
    stored = _Pack(*pack.build_documents(base))
    assert pack.lint(stored, base)["ok"]

    def stale(change):
        current = copy.deepcopy(base)
        change(current)
        return {d["key"] for d in pack.lint(stored, current)["documents"] if d["stale"]}

    def team(s):
        s["stakeholders"]["teams"][0]["members"] = 3

    def approval(s):
        s["stakeholders"]["approvals"][1]["by"] = "Owner"

    def webhook(s):
        s["communications"]["webhook"]["enabled"] = False

    assert "stakeholder_register" in stale(team)
    assert "communications_plan" not in stale(team)
    assert "stakeholder_register" in stale(approval)
    assert "stakeholder_register" not in stale(webhook)


def test_record_text_cannot_become_structure():
    snapshot = _snapshot()
    snapshot["stakeholders"]["teams"][0]["role"] = "| injected | row |\n# Heading"
    snapshot["members"]["Auditor|x"] = 1
    md = stakeholder_docs.register(snapshot)["markdown"]
    assert "\n# Heading" not in md
    assert _row(md, TEAMS, "| Team 4 ").count(" | ") == 3
    assert "Auditor|x" not in md


def test_the_register_in_spanish():
    register = stakeholder_docs.register(_in("es"))
    assert register["title"] == "Registro de interesados"
    assert register["persons"] == ["Interesados ajenos a la empresa"]
    assert "| Equipo 4 |" in register["markdown"]
    assert "| Agente Manager" in register["markdown"]
    gone = _in("es", _empty())
    gone.pop("stakeholders")
    assert stakeholder_docs.register(gone)["missing"] == [
        "miembros de la empresa",
        "equipos y aprobaciones",
    ]


# --- the computed check ------------------------------------------------------------


def test_the_check_measures_who_gave_the_approvals():
    results = {r["id"]: r for r in checks.run(_snapshot())}
    approvals = results["approvals"]
    assert approvals["domain"] == "Stakeholders"
    assert approvals["result"] == "measured"
    assert approvals["observed"] == (
        "1 of 3 approvals were given by a current Owner or Manager, "
        "1 by the Manager agent and 1 by another account"
    )
    empty = {r["id"]: r["result"] for r in checks.run(_empty())}
    assert empty["approvals"] == "not computable"
    assert stakeholder_docs.MEASURED == checks.MEASURED
    assert stakeholder_docs.NOT_COMPUTABLE == checks.NOT_COMPUTABLE


# --- the snapshot ------------------------------------------------------------------


def test_the_snapshot_keeps_teams_and_approvals_without_a_name(sqlite_session):
    import secrets

    from models import (
        Architecture,
        Company,
        CompanyMember,
        Specification,
        Team,
        TeamMember,
    )
    from models.company import User
    from services.planning.project_docs import collect

    company = Company(company_name=f"Register {secrets.token_hex(3)}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    tag = secrets.token_hex(3)
    viewer_mail = f"viewer-{tag}" + "@example.test"
    gone_mail = f"gone-{tag}" + "@example.test"
    outsider_mail = f"outsider-{tag}" + "@example.test"
    viewer = User(email=viewer_mail, full_name="Vera Viewer", password_hash="x")
    outsider = User(email=outsider_mail, full_name="Otto Outsider", password_hash="x")
    sqlite_session.add_all([viewer, outsider])
    sqlite_session.commit()
    sqlite_session.add(
        CompanyMember(company_id=company.id, user_id=viewer.id, human_role="Viewer")
    )
    team = Team(
        company_id=company.id,
        name="Vera's reviewers",
        description="Led by Vera",
        role="Manager",
    )
    sqlite_session.add(team)
    sqlite_session.commit()
    sqlite_session.add_all(
        [
            TeamMember(company_id=company.id, team_id=team.id, user_id=viewer.id),
            # Not a member of the company: a team never makes anyone one.
            TeamMember(company_id=company.id, team_id=team.id, user_id=outsider.id),
        ]
    )
    first = Specification(
        company_id=company.id,
        title="First",
        status="superseded",
        approved_by=gone_mail,
    )
    second = Specification(
        company_id=company.id,
        title="Second",
        status="approved",
        version=2,
        approved_by=viewer_mail.upper(),
    )
    draft = Specification(company_id=company.id, title="Draft")
    sqlite_session.add_all([first, second, draft])
    sqlite_session.commit()
    arch = Architecture(
        company_id=company.id,
        specification_id=second.id,
        title="Arch",
        status="approved",
        approved_by="Manager agent",
    )
    sqlite_session.add(arch)
    sqlite_session.commit()

    snap = collect.snapshot(sqlite_session, company)
    found = snap["stakeholders"]
    [row] = found["teams"]
    assert row["role"] == "Manager" and row["members"] == 1
    assert len(row["sha256"]) == 64
    by = {(a["kind"], a["id"]): a["by"] for a in found["approvals"]}
    assert by == {
        ("specification", first.id): "former",
        # A Viewer raised to Manager by a team: the effective role.
        ("specification", second.id): "Manager",
        ("architecture", arch.id): "agent",
    }
    assert all(len(a["sha256"]) == 64 for a in found["approvals"])
    text = repr(snap)
    for hidden in (
        viewer_mail,
        viewer_mail.upper(),
        gone_mail,
        outsider_mail,
        "Vera",
        "Otto",
        "reviewers",
    ):
        assert hidden not in text
    assert snap[statements.key_of("stakeholder_register")] == {}
