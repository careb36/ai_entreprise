"""The person sections of a documentation pack (issue #530 phase 2,
``routers/person_sections.py``, ``project_docs/statements.py``).

EARS criteria:
- When an Owner or a Manager writes a person section, the system shall keep a
  new statement with the author, the author's company role and the time, a
  digest it computed itself, and an audit entry attributed to the person.
- When a Viewer or a member of another company writes or withdraws one, the
  system shall refuse it (403); an unknown section is a 404 to a member.
- When a pack is compiled, the system shall quote each current statement
  under its section as a claim by its author's role with its time, cite it as
  a claim with its digest, and leave every other section and every check
  result unchanged; the author's address shall never reach the pack.
- When a statement is written, changed or withdrawn after a compile, the
  system shall flag the documents that show it as stale, and only those.
- When a statement is withdrawn, the system shall keep its history and the
  next compile shall show the template again.
"""

import copy

import pytest
from factories import company as _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models import AuditLog, PersonStatement
from services.planning.project_docs import checks, control_docs, pack, scope_docs
from services.planning.project_docs import statements as st
from test_documentation_router import _records, no_prose  # noqa: F401 - fixture
from test_project_docs import _Pack, _snapshot


def _url(company_id, section=""):
    base = f"/v1/companies/{company_id}/documentation/person-sections"
    return f"{base}/{section}" if section else base


@pytest.mark.usefixtures("enforcement")
def test_an_owner_or_manager_writes_a_section_and_the_author_is_recorded(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    owner = _member(sqlite_session, company.id, "Owner")
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Owner")
    url = _url(company.id, "charter.budget")
    body = {"text": "  Two people for six months.  "}

    assert test_client.put(url, json=body, headers=viewer).status_code == 403
    assert test_client.put(url, json=body, headers=outsider).status_code == 403
    assert test_client.delete(url, headers=viewer).status_code == 403
    assert (
        test_client.put(
            _url(company.id, "charter.nothing"), json=body, headers=manager
        ).status_code
        == 404
    )
    assert (
        test_client.put(url, json={"text": "   "}, headers=manager).status_code == 422
    )
    assert test_client.put(url, json={"text": ""}, headers=manager).status_code == 422

    first = test_client.put(url, json=body, headers=manager)
    assert first.status_code == 200, first.text
    statement = first.json()
    assert statement["text"] == "Two people for six months."
    assert statement["author_role"] == "Manager"
    assert statement["author"].startswith("member-") or "@" in statement["author"]
    assert statement["recorded_at"] and len(statement["statement_sha256"]) == 64
    assert statement["supersedes_id"] is None

    second = test_client.put(
        url, json={"text": "Three people.", "statement_sha256": "f" * 64}, headers=owner
    ).json()
    # The digest is the backend's, never the request's.
    assert second["statement_sha256"] != "f" * 64
    assert second["author_role"] == "Owner"
    assert second["supersedes_id"] == statement["id"]

    listed = test_client.get(_url(company.id), headers=viewer).json()
    by_key = {s["key"]: s for s in listed["sections"]}
    assert list(by_key) == list(st.SECTIONS)
    assert by_key["charter.budget"]["statement"]["text"] == "Three people."
    assert by_key["charter.sponsor"]["statement"] is None
    history = test_client.get(f"{url}/history", headers=viewer).json()["revisions"]
    assert [r["id"] for r in history] == [second["id"], statement["id"]]
    assert test_client.get(_url(company.id), headers=outsider).status_code == 403

    actions = [
        a.action
        for a in sqlite_session.query(AuditLog).filter_by(company_id=company.id).all()
    ]
    assert actions.count("person_statement_recorded") == 2
    entry = (
        sqlite_session.query(AuditLog)
        .filter_by(company_id=company.id, action="person_statement_recorded")
        .first()
    )
    assert entry.agent_id.startswith("human:")


@pytest.mark.usefixtures("enforcement")
def test_a_withdrawal_keeps_the_history_and_cannot_repeat(sqlite_session, test_client):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    url = _url(company.id, "risks.appetite")
    assert test_client.delete(url, headers=manager).status_code == 409
    test_client.put(url, json={"text": "Low appetite."}, headers=manager)
    withdrawn = test_client.delete(url, headers=manager)
    assert withdrawn.status_code == 200 and withdrawn.json()["withdrawn"] is True
    assert test_client.delete(url, headers=manager).status_code == 409
    listed = test_client.get(_url(company.id), headers=manager).json()["sections"]
    assert next(s for s in listed if s["key"] == "risks.appetite")["statement"] is None
    rows = sqlite_session.query(PersonStatement).filter_by(company_id=company.id).all()
    assert [r.text for r in rows] == ["Low appetite.", None]
    actions = {
        a.action
        for a in sqlite_session.query(AuditLog).filter_by(company_id=company.id).all()
    }
    assert "person_statement_withdrawn" in actions


@pytest.mark.usefixtures("no_prose")
def test_the_pack_quotes_a_statement_as_a_claim_apart_from_the_records(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    _records(sqlite_session, company)
    url = f"/v1/companies/{company.id}/documentation"
    before = test_client.post(url, json={"prose": False}).json()
    docs = {d["key"]: d for d in before["documents"]}
    assert docs["charter"]["statements"] == []
    assert "For a person to complete" in docs["charter"]["markdown"]

    hostile = "We commit **[money](javascript:alert(1))**\n\n# Not a heading"
    test_client.put(f"{url}/person-sections/charter.budget", json={"text": hostile})
    test_client.put(
        f"{url}/person-sections/checks.external", json={"text": "Two banks."}
    )

    lint = test_client.get(f"/v1/documentation/{before['id']}").json()["lint"]
    stale = {d["key"] for d in lint["documents"] if d["stale"]}
    assert stale == {"charter", "checks", "index"}

    after = test_client.post(url, json={"prose": False}).json()
    docs = {d["key"]: d for d in after["documents"]}
    charter = docs["charter"]
    [summary] = charter["statements"]
    assert summary["section"] == "charter.budget"
    assert summary["author_role"] == "Owner" and len(summary["sha256"]) == 64
    md = charter["markdown"]
    assert "Statement by a person (Owner), recorded" in md and "UTC" in md
    # Escaped brackets render as text, never as a link.
    assert "\\[money\\](javascript" in md and "[money](javascript" not in md
    assert "\n# Not a heading" not in md
    assert "\\# Not a heading" in md
    # The other person sections stay templates; the prose is not a statement.
    assert md.count("For a person to complete") == 3
    statement_sources = [s for s in charter["sources"] if s["kind"] == "statement"]
    assert statement_sources and all(s["claim"] for s in statement_sources)
    assert statement_sources[0]["sha256"] == summary["sha256"]
    # A check result never reads a statement: "person" stays "person".
    results = checks.run({**_snapshot(), **st.by_document([])})
    assert next(r for r in results if r["id"] == "external")["result"] == "person"
    assert "Two banks." in docs["checks"]["markdown"]
    assert "local demo" not in md and "@" not in md


def _filled(snapshot, section, text="A person's decision."):
    out = copy.deepcopy(snapshot)
    document = st.SECTIONS[section][0]
    out.setdefault(st.key_of(document), {})[section] = {
        "id": 7,
        "section": section,
        "text": text,
        "author_role": "Manager",
        "created_at": "2026-11-17T14:03:59",
        "statement_sha256": "c" * 64,
    }
    return out


def test_each_builder_shows_its_own_statements_and_reads_them_as_an_input():
    base = _snapshot()
    for builder, section in (
        (scope_docs.charter, "charter.sponsor"),
        (control_docs.risks, "risks.appetite"),
        (checks.document, "checks.budget"),
    ):
        plain, filled = builder(base), builder(_filled(base, section))
        assert plain["statements"] == []
        assert filled["statements"][0]["section"] == section
        assert (
            "Statement by a person (Manager), recorded 2026-11-17 14:03 UTC"
            in (filled["markdown"])
        )
        assert plain["input_sha256"] != filled["input_sha256"]
        # A statement of another document is not this one's input.
        other = "risks.appetite" if section != "risks.appetite" else "charter.sponsor"
        assert builder(_filled(base, other))["input_sha256"] == plain["input_sha256"]
    stored = _Pack(*pack.build_documents(base))
    lint = pack.lint(stored, _filled(base, "risks.appetite"))
    assert {d["key"] for d in lint["documents"] if d["stale"]} == {"risks", "index"}


def test_a_spanish_pack_labels_the_statement_in_spanish():
    snapshot = _filled({**_snapshot(), "language": "es"}, "charter.budget")
    md = scope_docs.charter(snapshot)["markdown"]
    assert "Declaración de una persona (Manager), registrada el 2026-11-17" in md
    assert "Declaración de una persona" in md.split("## Fuentes")[1]


def test_the_snapshot_keeps_the_current_statement_and_no_address(sqlite_session):
    from services.planning.project_docs import collect

    company = _company(sqlite_session)
    for text in ("First.", "Second.", None):
        sqlite_session.add(
            PersonStatement(
                company_id=company.id,
                section="charter.sponsor",
                text=text,
                author_role="Owner",
                recorded_by="sponsor@example.com",
            )
        )
    sqlite_session.add(
        PersonStatement(
            company_id=company.id,
            section="charter.benefits",
            text="Faster reviews.",
            author_role="Manager",
            recorded_by="manager@example.com",
        )
    )
    sqlite_session.commit()
    snap = collect.snapshot(sqlite_session, company)
    assert list(snap["statements_charter"]) == ["charter.benefits"]
    assert snap["statements_risks"] == {} and snap["statements_checks"] == {}
    assert "example.com" not in str(snap)
