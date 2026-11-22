"""The usage report (issue #512, phase 5; ``docs/archive/design/TEAMS_AND_USAGE.md``,
T7–T10): who used what, read back from the records.

On fixture data: tasks, runs, verdicts, rework, people's decisions and the
proxy's turns sum by period, person, team, role, producer and served model;
an unknown cost is counted apart, never as 0; periods are UTC (a week from
Monday) and the range is inclusive; the same records give the same report
twice; the CSV names the range and the generation time and escapes every
cell a spreadsheet would run as a formula; the JSON and the CSV need a
Manager (a team can make one), and the export is audited."""

from __future__ import annotations

import csv
import io
import secrets
from datetime import UTC, date, datetime

import auth_tokens
import pytest
from config import settings
from models import (
    AgentRun,
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    RunReview,
    Sprint,
    Team,
    TeamMember,
    User,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from services.models import model_catalog, usage_csv, usage_report
from sqlmodel import select

CATALOG = {
    "source": "models.dev",
    "providers": {
        "deepseek": {"models": {"coder": {"cost_input": 1.0, "cost_output": 4.0}}}
    },
}
NOW = datetime(2026, 11, 15, 12, 0, 0)


@pytest.fixture(autouse=True)
def fixed_catalog(monkeypatch):
    monkeypatch.setattr(model_catalog, "catalog", lambda: CATALOG)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _user(session, company_id, role, name):
    user = User(
        email=f"{secrets.token_hex(4)}@example.test", full_name=name, password_hash="x"
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    return user


def _headers(user):
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def _turn(session, company_id, task_id, at, status, **details):
    tokens = details.pop("tokens", 0)
    session.add(
        AuditLog(
            company_id=company_id,
            interaction_id=f"task-{task_id}",
            action=details.pop("action", "execute_task"),
            details={
                "tokens": status,
                "agent_role": details.pop("role", "Developer"),
                **details,
            },
            cost_tokens=tokens,
            created_at=at,
        )
    )


def _fixture(session):
    """Two people (Ana in Platform, Bruno in none), two sprints, three tasks."""
    acme = Company(company_name=f"Usage {secrets.token_hex(3)}")
    session.add(acme)
    session.commit()
    ana = _user(session, acme.id, "Manager", "=Ana")  # a name a spreadsheet would run
    bruno = _user(session, acme.id, "Viewer", "Bruno")
    platform = Team(company_id=acme.id, name="Platform", role="Viewer")
    session.add(platform)
    session.commit()
    session.add(TeamMember(company_id=acme.id, team_id=platform.id, user_id=ana.id))
    s1 = Sprint(company_id=acme.id, sprint_name="one", started_by_user_id=ana.id)
    s2 = Sprint(company_id=acme.id, sprint_name="two")  # opened by the platform
    session.add_all([s1, s2])
    session.commit()
    # Sunday 8 Nov 23:59:59 and Monday 9 Nov 00:00 fall in two ISO weeks.
    t1 = BacklogItem(
        sprint_id=s1.id,
        title="a",
        assignee="Developer",
        created_at=datetime(2026, 11, 8, 23, 59, 59),
        comments=[{"author": "QA", "text": "NEEDS WORK: tests"}],
    )
    t2 = BacklogItem(
        sprint_id=s1.id, title="b", assignee="QA", created_at=datetime(2026, 11, 9)
    )
    t3 = BacklogItem(
        sprint_id=s2.id,
        title="c",
        assignee="Developer",
        created_at=datetime(2026, 11, 9, 23, 30),
    )
    outside = BacklogItem(
        sprint_id=s1.id, title="late", created_at=datetime(2026, 11, 15, 0, 0)
    )
    session.add_all([t1, t2, t3, outside])
    session.commit()
    r1 = AgentRun(
        company_id=acme.id,
        external_ref=f"task-{t1.id}",
        producer=TASK_WORKER_PRODUCER,
        model="coder",
        agent_role="Developer",
        artifact_digest="0" * 64,
        artifact_size=1,
        ingested_at=datetime(2026, 11, 9, 10),
    )
    r2 = AgentRun(
        company_id=acme.id,
        external_ref=f"task-{t1.id}",
        version=2,
        producer="console-producer",
        model=None,
        agent_role="Developer",
        artifact_digest="1" * 64,
        artifact_size=1,
        ingested_at=datetime(2026, 11, 10, 10),
    )
    r3 = AgentRun(
        company_id=acme.id,
        external_ref=f"task-{t3.id}",
        producer=TASK_WORKER_PRODUCER,
        model="coder",
        agent_role="Developer",
        artifact_digest="2" * 64,
        artifact_size=1,
        ingested_at=datetime(2026, 11, 11, 10),
    )
    session.add_all([r1, r2, r3])
    session.commit()
    session.add_all(
        [
            # r1 refuted, then verified: the latest verdict counts.
            Verification(
                company_id=acme.id,
                agent_run_id=r1.id,
                verdict="refuted",
                observed_at=datetime(2026, 11, 9, 11),
            ),
            Verification(
                company_id=acme.id,
                agent_run_id=r1.id,
                verdict="verified",
                observed_at=datetime(2026, 11, 9, 12),
            ),
            Verification(
                company_id=acme.id,
                agent_run_id=r3.id,
                verdict="refuted",
                observed_at=datetime(2026, 11, 11, 11),
            ),
        ]
    )
    # Priced: 1M in + 250k out at $1/$4 per million = 200 cents.
    _turn(
        session,
        acme.id,
        t1.id,
        datetime(2026, 11, 9, 9),
        "reported",
        model="coder",
        provider="deepseek",
        input_tokens=1_000_000,
        output_tokens=250_000,
        tokens=1_250_000,
    )
    # Reported, but the catalogue has no price for this model: unknown, not 0.
    _turn(
        session,
        acme.id,
        t1.id,
        datetime(2026, 11, 10, 9),
        "reported",
        model="mystery",
        provider="deepseek",
        input_tokens=10,
        output_tokens=5,
        tokens=15,
        role="QA",
        action="qa_review",
    )
    # Estimated: counted apart, never priced.
    _turn(
        session,
        acme.id,
        t3.id,
        datetime(2026, 11, 11, 9),
        "estimated",
        model="coder",
        estimated_tokens=400,
    )
    # Unavailable.
    _turn(
        session,
        acme.id,
        t3.id,
        datetime(2026, 11, 11, 9, 30),
        "unavailable",
        model="coder",
    )
    session.add_all(
        [
            RunReview(
                company_id=acme.id,
                agent_run_id=r1.id,
                event="approve",
                outcome="approved",
                author_name="Bruno",
                author_user_id=bruno.id,
                created_at=datetime(2026, 11, 12, 8),
            ),
            RunReview(
                company_id=acme.id,
                agent_run_id=r3.id,
                event="request_changes",
                outcome="sent_back",
                author_name="Ana",
                author_user_id=ana.id,
                created_at=datetime(2026, 11, 12, 9),
            ),
            RunReview(
                company_id=acme.id,
                agent_run_id=r3.id,
                event="comment",
                outcome="recorded",
                author_name="Ana",
                author_user_id=ana.id,
                created_at=datetime(2026, 11, 12, 10),
            ),
        ]
    )
    session.commit()
    return acme, ana, bruno, platform


def _build(
    session,
    company_id,
    period="week",
    since=date(2026, 11, 2),
    until=date(2026, 11, 14),
):
    return usage_report.report(
        session, company_id, since=since, until=until, period=period, now=NOW
    )


def _row(rows, key):
    return next(r for r in rows if r["key"] == key)


def test_the_report_sums_the_records(sqlite_session):
    acme, ana, bruno, platform = _fixture(sqlite_session)
    report = _build(sqlite_session, acme.id)
    totals = report["totals"]
    assert {
        k: totals[k]
        for k in ("tasks", "rework", "runs", "verified", "refuted", "unverified")
    } == {
        "tasks": 3,
        "rework": 1,
        "runs": 3,
        "verified": 1,
        "refuted": 1,
        "unverified": 1,
    }
    assert (totals["approvals"], totals["changes_requested"]) == (1, 1)
    assert {
        k: totals[k]
        for k in (
            "turns",
            "reported_turns",
            "estimated_turns",
            "unavailable_turns",
            "input_tokens",
            "output_tokens",
            "reported_tokens",
            "estimated_tokens",
        )
    } == {
        "turns": 4,
        "reported_turns": 2,
        "estimated_turns": 1,
        "unavailable_turns": 1,
        "input_tokens": 1_000_010,
        "output_tokens": 250_005,
        "reported_tokens": 1_250_015,
        "estimated_tokens": 400,
    }
    # The priced turn only; the three others are counted apart, never as 0.
    assert totals["cost_cents"] == 200.0
    assert totals["cost_unknown_turns"] == 3

    # By person: Ana started sprint one (t1, t2, r1, r2, two turns) and asked for
    # changes; Bruno approved; sprint two's work is unattributed.
    by_person = report["by_person"]
    assert [r["key"] for r in by_person] == sorted([ana.id, bruno.id]) + [None]
    a = _row(by_person, ana.id)
    assert (a["label"], a["tasks"], a["runs"], a["turns"], a["cost_cents"]) == (
        "=Ana",
        2,
        2,
        2,
        200.0,
    )
    assert (a["approvals"], a["changes_requested"], a["cost_unknown_turns"]) == (
        0,
        1,
        1,
    )
    b = _row(by_person, bruno.id)
    assert (b["tasks"], b["approvals"], b["turns"], b["cost_cents"]) == (0, 1, 0, None)
    nobody = _row(by_person, None)
    assert (nobody["label"], nobody["tasks"], nobody["runs"], nobody["turns"]) == (
        None,
        1,
        1,
        2,
    )
    assert nobody["cost_cents"] is None and nobody["cost_unknown_turns"] == 2

    # By team: Ana's facts under Platform; Bruno's and the unattributed under none.
    by_team = report["by_team"]
    assert [r["key"] for r in by_team] == [platform.id, None]
    assert _row(by_team, platform.id)["label"] == "Platform"
    assert _row(by_team, platform.id)["tasks"] == 2
    assert _row(by_team, None)["approvals"] == 1

    # By role, producer and served model.
    by_role = {r["key"]: r for r in report["by_role"]}
    assert (
        by_role["Developer"]["tasks"],
        by_role["QA"]["tasks"],
        by_role["QA"]["turns"],
    ) == (2, 1, 1)
    by_producer = {r["key"]: r for r in report["by_producer"]}
    assert by_producer["console-producer"]["runs"] == 1
    assert (
        by_producer["console-producer"]["turns"] == 0
    )  # its provider's usage is not seen
    assert by_producer[TASK_WORKER_PRODUCER]["turns"] == 4
    by_model = {r["key"]: r for r in report["by_model"]}
    assert by_model["coder"]["cost_cents"] == 200.0
    assert by_model["mystery"]["cost_cents"] is None
    assert by_model["mystery"]["cost_unknown_turns"] == 1


def test_periods_are_utc_and_a_week_starts_on_monday(sqlite_session):
    acme, *_ = _fixture(sqlite_session)
    weeks = {r["key"]: r["tasks"] for r in _build(sqlite_session, acme.id)["by_period"]}
    # t1 on Sunday 8 Nov, 23:59:59: the week of 2 Nov; t2 and t3 (Monday 9 Nov
    # 00:00 and 23:30) the week of 9 Nov.
    assert weeks == {"2026-11-02": 1, "2026-11-09": 2}
    days = {
        r["key"]: r["tasks"]
        for r in _build(sqlite_session, acme.id, "day")["by_period"]
        if r["tasks"]
    }
    assert days == {"2026-11-08": 1, "2026-11-09": 2}
    months = _build(sqlite_session, acme.id, "month")["by_period"]
    assert [r["key"] for r in months] == ["2026-11-01"]
    # The range is inclusive of `until` and stops before the next day.
    only_sunday = _build(
        sqlite_session, acme.id, "day", date(2026, 11, 8), date(2026, 11, 8)
    )
    assert only_sunday["totals"]["tasks"] == 1
    assert only_sunday["totals"]["runs"] == 0
    with_saturday = _build(
        sqlite_session, acme.id, "day", date(2026, 11, 2), date(2026, 11, 15)
    )
    assert with_saturday["totals"]["tasks"] == 4  # 15 Nov 00:00 is in
    assert (
        usage_report.bucket(datetime(2026, 11, 15, 23, 0, tzinfo=UTC), "week")
        == "2026-11-09"
    )
    # An aware time in another zone is read in UTC: 01:30+02:00 is 23:30 the day before.
    aware = datetime.fromisoformat("2026-11-10T01:30:00+02:00")
    assert usage_report.bucket(aware, "day") == "2026-11-09"
    assert (
        usage_report.bucket(
            datetime.fromisoformat("2026-12-01T00:30:00+01:00"), "month"
        )
        == "2026-11-01"
    )
    with pytest.raises(usage_report.UsageRangeError):
        usage_report.window(date(2026, 11, 2), date(2026, 11, 1))
    with pytest.raises(usage_report.UsageRangeError):
        usage_report.window(date(2025, 1, 1), date(2026, 11, 1))


def test_the_same_records_give_the_same_report_twice(sqlite_session):
    acme, *_ = _fixture(sqlite_session)
    first, second = _build(sqlite_session, acme.id), _build(sqlite_session, acme.id)
    assert first == second
    assert usage_csv.render(first, "Acme") == usage_csv.render(second, "Acme")
    assert first["generated_at"] == "2026-11-15T12:00:00Z"


def test_another_company_sees_none_of_it(sqlite_session):
    _fixture(sqlite_session)
    other = Company(company_name="other")
    sqlite_session.add(other)
    sqlite_session.commit()
    report = _build(sqlite_session, other.id)
    assert report["totals"]["tasks"] == 0 and report["totals"]["turns"] == 0
    assert report["by_person"] == []


def test_the_csv_names_the_range_and_escapes_formulas():
    assert usage_csv.safe_text("=SUM(A1)") == "'=SUM(A1)"
    for start in ("+1", "-1", "@cmd", "\tx", "\rx"):
        assert usage_csv.safe_text(start).startswith("'")
    assert usage_csv.safe_text("Ana") == "Ana"
    assert usage_csv.safe_text(None) == ""


def test_the_csv_document(sqlite_session):
    acme, ana, *_ = _fixture(sqlite_session)
    text = usage_csv.render(_build(sqlite_session, acme.id), '@Acme, "Ltd"')
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["# La Fabrique usage report"]
    assert rows[1] == [f'# company: {acme.id} (@Acme, "Ltd")']
    assert rows[2] == ["# period: 2026-11-02 to 2026-11-14 inclusive, UTC, by week"]
    assert rows[3] == ["# generated: 2026-11-15T12:00:00Z"]
    assert rows[5][0].startswith("# teams: ")
    header = rows[6]
    assert header[:3] == ["dimension", "key", "label"] and "cost_usd" in header
    body = [dict(zip(header, r, strict=True)) for r in rows[7:]]
    total = body[0]
    assert (total["dimension"], total["tasks"], total["cost_usd"]) == (
        "total",
        "3",
        "2.000000",
    )
    assert total["cost_unknown_turns"] == "3"
    person = next(
        r for r in body if r["dimension"] == "person" and r["key"] == str(ana.id)
    )
    assert person["label"] == "'=Ana"  # never a formula
    unknown = next(
        r for r in body if r["dimension"] == "person" and r["key"] == "(unattributed)"
    )
    assert unknown["cost_usd"] == ""  # unknown, never 0
    assert not any(cell.startswith(("=", "+", "-", "@")) for r in rows for cell in r)


def test_the_endpoints_need_a_manager_and_the_export_is_audited(
    sqlite_session, test_client, auth_on
):
    acme, ana, bruno, _ = _fixture(sqlite_session)
    query = "?since=2026-11-02&until=2026-11-14&period=week"
    url = f"/v1/companies/{acme.id}/usage"
    assert test_client.get(url + query).status_code == 401
    assert test_client.get(url + query, headers=_headers(bruno)).status_code == 403
    assert (
        test_client.get(url + ".csv" + query, headers=_headers(bruno)).status_code
        == 403
    )
    read = test_client.get(url + query, headers=_headers(ana))
    assert read.status_code == 200, read.text
    assert read.json()["totals"]["tasks"] == 3
    # A Manager team opens it to Bruno.
    manager_team = Team(company_id=acme.id, name="Leads", role="Manager")
    sqlite_session.add(manager_team)
    sqlite_session.commit()
    sqlite_session.add(
        TeamMember(company_id=acme.id, team_id=manager_team.id, user_id=bruno.id)
    )
    sqlite_session.commit()
    exported = test_client.get(url + ".csv" + query, headers=_headers(bruno))
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.text.startswith("# La Fabrique usage report")
    audit = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == acme.id, AuditLog.action == "usage_report_exported"
        )
    ).one()
    assert audit.agent_id == f"human:{bruno.email}"
    assert (audit.result["since"], audit.result["until"], audit.result["period"]) == (
        "2026-11-02",
        "2026-11-14",
        "week",
    )
    # A Manager token may export too, and the row names the token, not a person.
    from services.platform import api_tokens

    token, raw = api_tokens.create_token(sqlite_session, acme.id, "ci", role="Manager")
    by_token = test_client.get(
        url + ".csv" + query, headers={"Authorization": f"Bearer {raw}"}
    )
    assert by_token.status_code == 200, by_token.text
    rows = sqlite_session.exec(
        select(AuditLog)
        .where(
            AuditLog.company_id == acme.id, AuditLog.action == "usage_report_exported"
        )
        .order_by(AuditLog.id)
    ).all()
    assert rows[-1].agent_id == f"api-token:{token.id}"
    # Bad ranges are refused.
    assert (
        test_client.get(
            url + "?since=2026-11-05&until=2026-11-01", headers=_headers(ana)
        ).status_code
        == 422
    )
    assert (
        test_client.get(url + "?period=year", headers=_headers(ana)).status_code == 422
    )


def test_start_sprint_records_who_started_it(sqlite_session, test_client, auth_on):
    acme = Company(company_name="starter")
    sqlite_session.add(acme)
    sqlite_session.commit()
    ana = _user(sqlite_session, acme.id, "Manager", "Ana")
    response = test_client.post(
        "/v1/start_sprint",
        json={
            "company_id": acme.id,
            "sprint_name": "s",
            "start_date": "2026-11-15",
            "backlog_items": [],
        },
        headers=_headers(ana),
    )
    assert response.status_code == 200, response.text
    sprint = sqlite_session.exec(
        select(Sprint).where(Sprint.company_id == acme.id)
    ).one()
    assert sprint.started_by_user_id == ana.id
