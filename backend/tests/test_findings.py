"""The findings of the self-improvement loop (``docs/design/rsi-loop.md``,
PR 9 of §8; REQ-18, REQ-20), negatives first:

- a specification whose cycle was reverted does not start one again — the
  sha256 of the *normalised* text decides, so other whitespace or line
  endings make no difference, while a real change does;
- a signed-in person may still ask for it; an agent, an API token's
  synthetic user, a user that was never persisted or nobody cannot;
- an outcome is an observation the platform writes: any other producer, an
  unknown decision or a missing sha is refused;
- another company's findings are not visible, and its reverts do not block;
- the sha256 is computed from the file as read, never taken from it;
- the routes are read-only: a Viewer of the company reads the list and the
  detail, an outsider and an anonymous caller do not, and no route writes
  an outcome or starts a cycle.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import auth_deps
import factories
import pytest
from factories import enforcement  # noqa: F401 - the fixture, by name
from models import Agent
from services.learning import findings
from services.platform import api_tokens
from sqlmodel import select

RETROSPECTIVE = "docs/retrospectives/2026-11-self-hosting.md"
SPEC = {
    "title": "The lint after an edit runs the edited file's folder's linter",
    "target_measure": "scripts/self_host/measures/test_folder_lint.py",
    "requirements": [
        {
            "title": "A folder's profile carries a lint command",
            "criteria": [
                {
                    "given": "a detected python folder",
                    "when": "the folders are resolved",
                    "then": "the folder carries its stack's lint command",
                }
            ],
        }
    ],
}


def _text(record: dict | None = None, indent: int = 2, eol: str = "\n") -> str:
    return json.dumps(SPEC if record is None else record, indent=indent).replace(
        "\n", eol
    )


def _spec_file(tmp_path, text: str | None = None, name: str = "specification.json"):
    path = tmp_path / name
    path.write_text(_text() if text is None else text, encoding="utf-8")
    return path


def _created(session, company_id, tmp_path, text: str | None = None, **kwargs):
    path = _spec_file(tmp_path, text)
    return findings.create_from_specification_file(
        session, company_id, path, source=RETROSPECTIVE, source_ref="F5", **kwargs
    )


def _revert(session, finding, **kwargs):
    return findings.record_outcome(
        session,
        finding,
        produced_by="platform",
        decision="revert",
        baseline_sha="b" * 40,
        candidate_sha="c" * 40,
        reasons=[{"guard": "target", "outcome": "revert", "code": "not_flipped"}],
        **kwargs,
    )


def _token_user(session, company_id, person):
    """The synthetic user an API token signs in as (``auth_deps``)."""
    _, raw = api_tokens.create_token(
        session, company_id, "ci", person.email, [], role="Manager"
    )
    return auth_deps._from_bearer(session, raw)


# -- REQ-20: the same text as a reverted cycle --------------------------------


def test_a_reverted_specification_does_not_start_a_cycle_again(
    sqlite_session, tmp_path
):
    company = factories.company(sqlite_session)
    finding = _created(sqlite_session, company.id, tmp_path)
    sqlite_session.commit()
    _revert(sqlite_session, finding)

    # The same text, however it is written: re-indented, CRLF, trailing
    # spaces on every line and a trailing blank line.
    variants = [
        _text(),
        _text(indent=4),
        _text(eol="\r\n"),
        "\n".join(line + "  " for line in _text().splitlines()) + "\n\n",
    ]
    for text in variants:
        answer = findings.may_start(sqlite_session, company.id, text)
        assert answer.allowed is False, text[:40]
        assert answer.reason == "specification_reverted"
        assert answer.reverted_finding_id == finding.id
    # The finding itself says the same.
    assert findings.may_start(sqlite_session, company.id, finding).allowed is False
    # A specification that really changed is not the reverted one.
    changed = {**SPEC, "summary": "…and names the folder in the observation"}
    answer = findings.may_start(sqlite_session, company.id, _text(changed))
    assert (answer.allowed, answer.reason) == (True, "no_reverted_cycle")
    # An empty text starts nothing either.
    assert findings.may_start(sqlite_session, company.id, "  ").allowed is False


def test_a_signed_in_person_may_ask_for_the_reverted_specification(
    sqlite_session, tmp_path
):
    company = factories.company(sqlite_session)
    person = factories.member(sqlite_session, company, "Manager")
    finding = _created(sqlite_session, company.id, tmp_path)
    sqlite_session.commit()
    _revert(sqlite_session, finding)

    answer = findings.may_start(sqlite_session, company.id, _text(), asked_by=person)
    assert (answer.allowed, answer.reason) == (True, "person_asked")
    assert answer.reverted_finding_id == finding.id


def test_an_agent_a_token_or_nobody_cannot_ask(sqlite_session, tmp_path):
    company = factories.company(sqlite_session)
    person = factories.member(sqlite_session, company, "Owner")
    finding = _created(sqlite_session, company.id, tmp_path)
    agent_row = Agent(company_id=company.id, name="Atlas", role="Developer")
    sqlite_session.add(agent_row)
    saved = factories.user(sqlite_session, "Viewer")
    ghost = type(saved)(
        id=saved.id + 100000,
        email="ghost@example.com",
        full_name="Ghost",
        password_hash="unused",
    )
    sqlite_session.commit()
    _revert(sqlite_session, finding)

    not_people = [
        None,  # nobody asked
        "ScrumMaster",  # an agent's role name
        "Manager",
        agent_row,  # an Agent row
        _token_user(sqlite_session, company.id, person),  # an API token
        ghost,  # a User that was never persisted
    ]
    for asked_by in not_people:
        answer = findings.may_start(
            sqlite_session, company.id, _text(), asked_by=asked_by
        )
        assert answer.allowed is False, asked_by
        assert answer.reason == "specification_reverted"
    assert findings.person_who_asked(sqlite_session, person, company.id) is not None
    for asked_by in not_people:
        assert findings.person_who_asked(sqlite_session, asked_by, company.id) is None


# -- The outcomes: observations only the platform writes -----------------------


def test_an_outcome_only_the_platform_writes(sqlite_session, tmp_path):
    company = factories.company(sqlite_session)
    finding = _created(sqlite_session, company.id, tmp_path)
    sqlite_session.commit()
    outcome = {
        "decision": "revert",
        "baseline_sha": "b" * 40,
        "candidate_sha": "c" * 40,
    }
    for producer in ["ScrumMaster", "the build under test", "agent", "", None]:
        with pytest.raises(findings.FindingRefused, match="the platform writes"):
            findings.record_outcome(
                sqlite_session, finding, produced_by=producer, **outcome
            )
    with pytest.raises(findings.FindingRefused, match="decision"):
        findings.record_outcome(
            sqlite_session,
            finding,
            produced_by="platform",
            **{**outcome, "decision": "KEEP"},
        )
    for missing in [{"baseline_sha": ""}, {"candidate_sha": None}]:
        with pytest.raises(findings.FindingRefused, match="sha"):
            findings.record_outcome(
                sqlite_session,
                finding,
                produced_by="platform",
                **{**outcome, **missing},
            )
    with pytest.raises(findings.FindingRefused, match="reasons"):
        findings.record_outcome(
            sqlite_session,
            finding,
            produced_by="platform",
            reasons="not a list",
            **outcome,
        )
    assert finding.outcomes is None and finding.status == "open"

    # The platform's observation is recorded, with what it names.
    when = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    findings.record_outcome(
        sqlite_session,
        finding,
        produced_by="platform",
        decision="revert",
        baseline_sha="b" * 40,
        candidate_sha="c" * 40,
        reasons=[{"guard": "target", "code": "not_flipped"}],
        cycle=1,
        date=when,
    )
    sqlite_session.commit()
    sqlite_session.refresh(finding)
    assert finding.status == "reverted"
    (entry,) = finding.outcomes
    assert entry == {
        "cycle": 1,
        "decision": "revert",
        "reasons": [{"guard": "target", "code": "not_flipped"}],
        "baseline_sha": "b" * 40,
        "candidate_sha": "c" * 40,
        "date": when.isoformat(),
        "produced_by": "platform",
    }
    # The outcomes stay a list: one entry per cycle, the status follows the
    # last decision.
    findings.record_outcome(
        sqlite_session,
        finding,
        produced_by="platform",
        decision="unmeasured",
        baseline_sha="b" * 40,
        candidate_sha="d" * 40,
        cycle=2,
    )
    assert [o["decision"] for o in finding.outcomes] == ["revert", "unmeasured"]
    assert finding.status == "unmeasured"


def test_a_person_closes_a_finding_and_no_outcome_follows(sqlite_session, tmp_path):
    company = factories.company(sqlite_session)
    person = factories.member(sqlite_session, company, "Manager")
    finding = _created(sqlite_session, company.id, tmp_path)
    sqlite_session.commit()

    findings.mark_in_cycle(sqlite_session, finding)
    assert finding.status == "in_cycle"
    with pytest.raises(findings.FindingRefused, match="not open"):
        findings.mark_in_cycle(sqlite_session, finding)

    with pytest.raises(findings.FindingRefused, match="signed-in person"):
        findings.close_by_person(sqlite_session, finding, "ScrumMaster")
    with pytest.raises(findings.FindingRefused, match="signed-in person"):
        findings.close_by_person(
            sqlite_session, finding, _token_user(sqlite_session, company.id, person)
        )
    findings.close_by_person(sqlite_session, finding, person)
    sqlite_session.commit()
    assert finding.status == "closed_by_person" and finding.closed_by == person.email
    with pytest.raises(findings.FindingRefused, match="already closed"):
        findings.close_by_person(sqlite_session, finding, person)
    with pytest.raises(findings.FindingRefused, match="settled"):
        findings.record_outcome(
            sqlite_session,
            finding,
            produced_by="platform",
            decision="keep",
            baseline_sha="b" * 40,
            candidate_sha="c" * 40,
        )


# -- Creating from a specification file ----------------------------------------


def test_a_finding_is_created_from_a_specification_file(sqlite_session, tmp_path):
    company = factories.company(sqlite_session)
    # The file's own claim about its sha256 is never read (rule 6).
    lying_record = {**SPEC, "specification_sha256": "0" * 64}
    lying = _text(lying_record)
    path = _spec_file(tmp_path, lying)
    finding = findings.create_from_specification_file(
        sqlite_session, company.id, path, source=RETROSPECTIVE, source_ref="F5"
    )
    sqlite_session.commit()
    assert finding.specification_sha256 == findings.specification_sha256(lying)
    assert finding.specification_sha256 != "0" * 64
    assert finding.title == SPEC["title"]
    assert finding.target_measure == SPEC["target_measure"]
    assert finding.specification_path == path.as_posix()
    assert (finding.source, finding.source_ref) == (RETROSPECTIVE, "F5")
    assert (finding.status, finding.outcomes) == ("open", None)
    assert finding.specification_text == lying

    # The same specification again is refused, however it is written.
    with pytest.raises(findings.FindingRefused, match="already carries"):
        _created(sqlite_session, company.id, tmp_path, text=lying)
    with pytest.raises(findings.FindingRefused, match="already carries"):
        findings.create_from_specification(
            sqlite_session,
            company.id,
            specification_text=_text(lying_record, indent=4, eol="\r\n"),
            title=SPEC["title"],
        )
    # A file without a target measure, no JSON, no file, no title, no text.
    without_measure = _spec_file(
        tmp_path,
        json.dumps({k: v for k, v in SPEC.items() if k != "target_measure"}),
        name="no-measure.json",
    )
    with pytest.raises(findings.FindingRefused, match="target_measure"):
        findings.create_from_specification_file(
            sqlite_session, company.id, without_measure
        )
    not_json = _spec_file(
        tmp_path, "the lint reads the root's profile", name="prose.txt"
    )
    with pytest.raises(findings.FindingRefused, match="not a JSON"):
        findings.create_from_specification_file(sqlite_session, company.id, not_json)
    with pytest.raises(findings.FindingRefused, match="could not be read"):
        findings.create_from_specification_file(
            sqlite_session, company.id, tmp_path / "absent.json"
        )
    with pytest.raises(findings.FindingRefused, match="title"):
        findings.create_from_specification(
            sqlite_session, company.id, specification_text=_text(), title="  "
        )
    with pytest.raises(findings.FindingRefused, match="empty"):
        findings.create_from_specification(
            sqlite_session, company.id, specification_text=" \n", title="t"
        )


def test_open_findings_lists_what_a_cycle_may_be_started_for(sqlite_session, tmp_path):
    company = factories.company(sqlite_session)
    ready = _created(sqlite_session, company.id, tmp_path)
    # A finding recorded before its grader was written: no target measure.
    drafted = findings.create_from_specification(
        sqlite_session,
        company.id,
        specification_text=_text({**SPEC, "title": "Another finding"}),
        title="Another finding",
    )
    settled = _created(
        sqlite_session, company.id, tmp_path, text=_text({**SPEC, "title": "Settled"})
    )
    sqlite_session.commit()
    findings.record_outcome(
        sqlite_session,
        settled,
        produced_by="platform",
        decision="keep",
        baseline_sha="b" * 40,
        candidate_sha="c" * 40,
    )

    assert [f.id for f in findings.open_findings(sqlite_session, company.id)] == [
        drafted.id,
        ready.id,
    ]
    assert [
        f.id
        for f in findings.open_findings(
            sqlite_session, company.id, selectable_only=True
        )
    ] == [ready.id]
    assert findings.list_findings(sqlite_session, company.id, status="kept") == [
        settled
    ]
    with pytest.raises(findings.FindingRefused, match="unknown status"):
        findings.list_findings(sqlite_session, company.id, status="done")


# -- Company scoping ------------------------------------------------------------


def test_another_companys_findings_are_not_visible_or_read(sqlite_session, tmp_path):
    acme = factories.company(sqlite_session)
    other = factories.company(sqlite_session)
    # Both companies carry the same specification text; only one reverted it.
    mine = _created(sqlite_session, acme.id, tmp_path)
    theirs = _created(sqlite_session, other.id, tmp_path)
    sqlite_session.commit()
    _revert(sqlite_session, theirs)
    sqlite_session.commit()

    rows = sqlite_session.exec(select(findings.Finding)).all()
    assert {row.id for row in rows} == {mine.id, theirs.id}
    assert [f.id for f in findings.list_findings(sqlite_session, acme.id)] == [mine.id]
    # The other company's revert of the same text does not block this one.
    answer = findings.may_start(sqlite_session, acme.id, _text())
    assert (answer.allowed, answer.reason) == (True, "no_reverted_cycle")
    answer = findings.may_start(sqlite_session, other.id, _text())
    assert answer.allowed is False and answer.reverted_finding_id == theirs.id


@pytest.mark.usefixtures("enforcement")
def test_the_routes_are_read_only_and_scoped(sqlite_session, test_client, tmp_path):
    company = factories.company(sqlite_session)
    viewer = factories.member_headers(sqlite_session, company.id, "Viewer")
    outsider = factories.member_headers(
        sqlite_session, factories.company(sqlite_session), "Owner"
    )
    finding = _created(sqlite_session, company.id, tmp_path)
    sqlite_session.commit()
    _revert(sqlite_session, finding)
    sqlite_session.commit()

    listed = test_client.get(
        "/v1/findings", params={"company_id": company.id}, headers=viewer
    )
    assert listed.status_code == 200, listed.text
    (row,) = listed.json()["findings"]
    assert row["title"] == SPEC["title"]
    assert row["target_measure"] == SPEC["target_measure"]
    assert row["status"] == "reverted" and row["company_id"] == company.id
    assert [o["decision"] for o in row["outcomes"]] == ["revert"]
    # The list stays light; the detail carries the specification's text.
    assert "specification_text" not in row
    detail = test_client.get(f"/v1/findings/{finding.id}", headers=viewer)
    assert detail.status_code == 200
    assert detail.json()["specification_text"] == _text()

    # The status filter, and a status that is not one.
    kept = test_client.get(
        "/v1/findings",
        params={"company_id": company.id, "status": "kept"},
        headers=viewer,
    )
    assert kept.status_code == 200 and kept.json()["findings"] == []
    bad = test_client.get(
        "/v1/findings",
        params={"company_id": company.id, "status": "done"},
        headers=viewer,
    )
    assert bad.status_code == 400

    # Nobody else reads it: an outsider is refused, an anonymous caller too.
    assert (
        test_client.get(
            "/v1/findings", params={"company_id": company.id}, headers=outsider
        ).status_code
        == 403
    )
    assert (
        test_client.get(f"/v1/findings/{finding.id}", headers=outsider).status_code
        == 403
    )
    assert (
        test_client.get("/v1/findings", params={"company_id": company.id}).status_code
        == 401
    )
    assert test_client.get("/v1/findings/100000", headers=viewer).status_code == 404

    # Read-only: no route writes a finding or an outcome.
    assert test_client.post("/v1/findings", json={}, headers=viewer).status_code == 405
    assert (
        test_client.post(
            f"/v1/findings/{finding.id}/outcomes", json={}, headers=viewer
        ).status_code
        == 404
    )
