"""The findings of the self-improvement loop, continued
(``docs/design/rsi-loop.md``, PR 9 of §8; REQ-18, REQ-20), negatives first:

- the specification's sha256 is of its canonical form: a JSON specification
  rewritten pretty or compact, or with its keys in another order, is the same
  one, while whitespace inside a quoted string is a different requirement;
  a text that is not JSON only forgets its line endings and trailing spaces;
- after a revert, only a signed-in Manager or Owner of the finding's company
  retries it: an agent, an API token, a Viewer, a person of another company
  or nobody cannot; the retry keeps the earlier outcomes, is audited, and
  refuses a running, a kept or a person-closed finding;
- a person who asks for a reverted specification belongs to the company;
- a person's close is audited, and only a member closes;
- a finding of another company is not judged against this one's reverts.
"""

from __future__ import annotations

import json

import factories
import pytest
from models import Agent, AuditLog
from services.learning import findings
from services.platform import api_tokens
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

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


def _pretty(record: dict | None = None) -> str:
    return json.dumps(SPEC if record is None else record, indent=2)


def _compact(record: dict | None = None) -> str:
    return json.dumps(SPEC if record is None else record, separators=(",", ":"))


def _finding(session, company_id, text: str | None = None):
    return findings.create_from_specification(
        session,
        company_id,
        specification_text=_pretty() if text is None else text,
        title=SPEC["title"],
        target_measure=SPEC["target_measure"],
    )


def _outcome(session, finding, decision: str = "revert"):
    return findings.record_outcome(
        session,
        finding,
        produced_by="platform",
        decision=decision,
        baseline_sha="b" * 40,
        candidate_sha="c" * 40,
        reasons=[{"guard": "target", "code": "not_flipped"}],
    )


def _token_user(session, company_id, person):
    import auth_deps

    _, raw = api_tokens.create_token(
        session, company_id, "ci", person.email, [], role="Manager"
    )
    return auth_deps._from_bearer(session, raw)


def _audits(session, company_id, action):
    return session.exec(
        select(AuditLog).where(
            AuditLog.company_id == company_id, AuditLog.action == action
        )
    ).all()


# -- The specification's canonical form ---------------------------------------


def test_a_json_specification_is_hashed_in_its_canonical_form():
    pretty = _pretty()
    reordered = json.dumps(dict(reversed(list(SPEC.items()))), indent=4)
    crlf = pretty.replace("\n", "\r\n") + "\r\n"
    for same in (_compact(), reordered, crlf):
        assert findings.specification_sha256(same) == findings.specification_sha256(
            pretty
        )
    # Whitespace inside a quoted string is part of the requirement.
    spaced = json.loads(pretty)
    spaced["requirements"][0]["criteria"][0]["then"] = (
        "the folder carries its  stack's lint command"
    )
    padded = {**SPEC, "title": SPEC["title"] + " "}
    multiline = {**SPEC, "title": SPEC["title"].replace(" ", "\n", 1)}
    for different in (spaced, padded, multiline):
        assert findings.specification_sha256(
            _pretty(different)
        ) != findings.specification_sha256(pretty)
    # Non-ASCII text is kept as it is, escaped or not.
    accented = {**SPEC, "title": "La revisión del linter"}
    assert findings.specification_sha256(
        json.dumps(accented, ensure_ascii=True)
    ) == findings.specification_sha256(json.dumps(accented, ensure_ascii=False))


def test_a_text_specification_only_forgets_line_endings_and_trailing_spaces():
    text = "Given a folder\n  when it is linted\nthen  its linter runs"
    same = ["Given a folder  \r\n  when it is linted\t\r\nthen  its linter runs\n\n"]
    for variant in same:
        assert findings.specification_sha256(variant) == findings.specification_sha256(
            text
        )
    different = [
        "Given a folder\n  when it is linted\nthen its linter runs",
        "Given a folder\nwhen it is linted\nthen  its linter runs",
        "Given a folder when it is linted then  its linter runs",
    ]
    for variant in different:
        assert findings.specification_sha256(variant) != findings.specification_sha256(
            text
        )


def test_a_compact_rewrite_of_a_reverted_specification_starts_no_cycle(
    sqlite_session,
):
    company = factories.company(sqlite_session)
    finding = _finding(sqlite_session, company.id)
    sqlite_session.commit()
    _outcome(sqlite_session, finding)

    answer = findings.may_start(sqlite_session, company.id, _compact())
    assert (answer.allowed, answer.reason) == (False, "specification_reverted")
    assert answer.reverted_finding_id == finding.id
    # Nor may it be recorded again as a second finding.
    with pytest.raises(findings.FindingRefused, match="already carries"):
        _finding(sqlite_session, company.id, _compact())
    # A changed string value is another specification.
    changed = {**SPEC, "title": SPEC["title"].replace(" ", "  ", 1)}
    answer = findings.may_start(sqlite_session, company.id, _compact(changed))
    assert (answer.allowed, answer.reason) == (True, "no_reverted_cycle")


def test_the_database_holds_one_finding_per_specification(sqlite_session):
    company = factories.company(sqlite_session)
    _finding(sqlite_session, company.id)
    sqlite_session.commit()
    twin = findings.Finding(
        company_id=company.id,
        title="A twin written past the service",
        specification_sha256=findings.specification_sha256(_pretty()),
    )
    sqlite_session.add(twin)
    with pytest.raises(IntegrityError):
        sqlite_session.commit()
    sqlite_session.rollback()


def test_a_specification_file_whose_title_is_not_text_is_refused(
    sqlite_session, tmp_path
):
    company = factories.company(sqlite_session)
    for title in (123, ["a title"], {"en": "a title"}):
        path = tmp_path / "specification.json"
        path.write_text(_pretty({**SPEC, "title": title}), encoding="utf-8")
        with pytest.raises(findings.FindingRefused, match="no title"):
            findings.create_from_specification_file(sqlite_session, company.id, path)


# -- A person retries a reverted finding --------------------------------------


def test_a_person_retries_a_reverted_finding_and_its_outcomes_stay(sqlite_session):
    company = factories.company(sqlite_session)
    person = factories.member(sqlite_session, company, "Manager")
    finding = _finding(sqlite_session, company.id)
    sqlite_session.commit()
    _outcome(sqlite_session, finding)
    sqlite_session.commit()
    assert finding.status == "reverted"

    # REQ-20: nobody's request starts nothing; the person's does.
    assert findings.may_start(sqlite_session, company.id, finding).allowed is False
    with pytest.raises(findings.FindingRefused, match="not open"):
        findings.mark_in_cycle(sqlite_session, finding)
    answer = findings.may_start(sqlite_session, company.id, finding, asked_by=person)
    assert (answer.allowed, answer.reason) == (True, "person_asked")

    findings.retry_by_person(sqlite_session, finding, person)
    sqlite_session.commit()
    sqlite_session.refresh(finding)
    assert finding.status == "in_cycle"
    assert [o["decision"] for o in finding.outcomes] == ["revert"]
    (audit,) = _audits(sqlite_session, company.id, "finding.retry")
    assert audit.agent_id == f"human:{person.email}"
    assert audit.result["finding_id"] == finding.id
    assert audit.result["from_status"] == "reverted"

    # The cycle's next outcome is appended to the earlier one.
    _outcome(sqlite_session, finding, decision="unmeasured")
    assert [o["decision"] for o in finding.outcomes] == ["revert", "unmeasured"]
    # An unmeasured finding is retried by a person too.
    findings.retry_by_person(sqlite_session, finding, person)
    assert finding.status == "in_cycle"


def test_only_a_manager_of_the_company_retries(sqlite_session):
    company = factories.company(sqlite_session)
    owner = factories.member(sqlite_session, company, "Owner")
    viewer = factories.member(sqlite_session, company, "Viewer")
    outsider = factories.member(sqlite_session, factories.company(sqlite_session))
    other_owner = factories.member(
        sqlite_session, factories.company(sqlite_session), "Owner"
    )
    stranger = factories.member(sqlite_session, None)
    agent_row = Agent(company_id=company.id, name="Atlas", role="Developer")
    sqlite_session.add(agent_row)
    finding = _finding(sqlite_session, company.id)
    sqlite_session.commit()
    _outcome(sqlite_session, finding)
    sqlite_session.commit()

    not_allowed = [
        None,
        "ScrumMaster",
        agent_row,
        _token_user(sqlite_session, company.id, owner),
        viewer,
        outsider,
        other_owner,
        stranger,
    ]
    for asked_by in not_allowed:
        with pytest.raises(findings.FindingRefused, match="Manager or Owner"):
            findings.retry_by_person(sqlite_session, finding, asked_by)
        answer = findings.may_start(
            sqlite_session, company.id, finding, asked_by=asked_by
        )
        assert answer.allowed is False, asked_by
    assert finding.status == "reverted"
    assert _audits(sqlite_session, company.id, "finding.retry") == []
    findings.retry_by_person(sqlite_session, finding, owner)
    assert finding.status == "in_cycle"


def test_a_running_kept_open_or_closed_finding_is_not_retried(sqlite_session):
    company = factories.company(sqlite_session)
    person = factories.member(sqlite_session, company, "Owner")
    texts = [_pretty({**SPEC, "title": f"Finding {n}"}) for n in range(4)]
    opened, running, kept, closed = (
        _finding(sqlite_session, company.id, text) for text in texts
    )
    sqlite_session.commit()
    findings.mark_in_cycle(sqlite_session, running)
    _outcome(sqlite_session, kept, decision="keep")
    _outcome(sqlite_session, closed)
    findings.close_by_person(sqlite_session, closed, person)
    sqlite_session.commit()

    for finding, status in [
        (opened, "open"),
        (running, "in_cycle"),
        (kept, "kept"),
        (closed, "closed_by_person"),
    ]:
        with pytest.raises(findings.FindingRefused, match=status):
            findings.retry_by_person(sqlite_session, finding, person)
        assert finding.status == status
    assert [o["decision"] for o in closed.outcomes] == ["revert"]


# -- A person's close; another company's finding ------------------------------


def test_a_close_is_audited_and_only_a_member_closes(sqlite_session):
    company = factories.company(sqlite_session)
    person = factories.member(sqlite_session, company, "Manager")
    viewer = factories.member(sqlite_session, company, "Viewer")
    other_owner = factories.member(
        sqlite_session, factories.company(sqlite_session), "Owner"
    )
    finding = _finding(sqlite_session, company.id)
    sqlite_session.commit()

    for asked_by in (viewer, other_owner):
        with pytest.raises(findings.FindingRefused, match="Manager or Owner"):
            findings.close_by_person(sqlite_session, finding, asked_by)
    assert finding.status == "open"
    findings.close_by_person(sqlite_session, finding, person)
    sqlite_session.commit()
    (audit,) = _audits(sqlite_session, company.id, "finding.close")
    assert audit.agent_id == f"human:{person.email}"
    assert audit.result["finding_id"] == finding.id


def test_a_person_of_another_company_does_not_lift_a_revert(sqlite_session):
    company = factories.company(sqlite_session)
    other = factories.company(sqlite_session)
    other_owner = factories.member(sqlite_session, other, "Owner")
    finding = _finding(sqlite_session, company.id)
    sqlite_session.commit()
    _outcome(sqlite_session, finding)

    answer = findings.may_start(
        sqlite_session, company.id, _pretty(), asked_by=other_owner
    )
    assert (answer.allowed, answer.reason) == (False, "specification_reverted")
    assert findings.person_who_asked(sqlite_session, other_owner, company.id) is None
    # A finding is judged in its own company only.
    answer = findings.may_start(sqlite_session, other.id, finding)
    assert (answer.allowed, answer.reason) == (False, "another_company")
