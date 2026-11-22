"""Team membership history and the usage report's team at the time of the work
(issue #702; ``docs/archive/design/TEAMS_AND_USAGE.md``, Team membership history).

When an Owner adds a member to a team, the backend opens a period; when the
member leaves the team, the team is deleted or the person leaves the company,
it closes the open period. A closed period is never edited and no period is
deleted: joining again opens a new one. The usage report attributes each
piece of work to the teams the person was in at the work's own timestamp;
work from before the history started (migration 0052) keeps today's teams,
and the report says which period that covers. Migration 0052 seeds one open
period per existing membership, starting when it ran."""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta

import auth_tokens
import pytest
from config import settings
from models import (
    AuditLog,
    BacklogItem,
    Company,
    CompanyMember,
    RunReview,
    Sprint,
    Team,
    TeamHistoryStart,
    TeamMember,
    TeamMembershipPeriod,
    User,
)
from services.models import usage_csv, usage_report
from services.platform import teams
from sqlmodel import select

NOW = datetime(2026, 11, 15, 12, 0, 0)


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)  # a throwaway signing key, per test
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _company(session) -> Company:
    row = Company(company_name=f"History {secrets.token_hex(3)}")
    session.add(row)
    session.commit()
    return row


def _person(session, company_id: int, role: str, name: str):
    user = User(
        email=f"{secrets.token_hex(4)}@example.test", full_name=name, password_hash="x"
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company_id, user_id=user.id, human_role=role))
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return user, {"Authorization": f"Bearer {token}"}


def _periods(session, user_id: int) -> list[TeamMembershipPeriod]:
    return list(
        session.exec(
            select(TeamMembershipPeriod)
            .where(TeamMembershipPeriod.user_id == user_id)
            .order_by(TeamMembershipPeriod.id)
        ).all()
    )


def _url(company_id: int, rest: str = "") -> str:
    return f"/v1/companies/{company_id}/teams{rest}"


# -- Written by the backend when an Owner changes a membership ---------------


def test_joining_opens_a_period_and_leaving_closes_it_never_editing_it(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    owner, headers = _person(sqlite_session, acme.id, "Owner", "Owner")
    ana, _ = _person(sqlite_session, acme.id, "Viewer", "Ana")
    team = test_client.post(
        _url(acme.id), json={"name": "Platform"}, headers=headers
    ).json()
    member = _url(acme.id, f"/{team['id']}/members/{ana.id}")

    assert test_client.put(member, headers=headers).status_code == 200
    # Adding a member already in the team changes nothing: no second period.
    assert test_client.put(member, headers=headers).status_code == 200
    [first] = _periods(sqlite_session, ana.id)
    assert (first.team_id, first.team_name, first.company_id) == (
        team["id"],
        "Platform",
        acme.id,
    )
    assert first.started_by == f"human:{owner.email}" and first.ended_at is None

    assert test_client.delete(member, headers=headers).status_code == 200
    sqlite_session.expire_all()
    [closed] = _periods(sqlite_session, ana.id)
    assert closed.ended_at is not None and closed.ended_at >= closed.started_at
    assert closed.ended_by == f"human:{owner.email}"
    ended_at = closed.ended_at

    assert test_client.put(member, headers=headers).status_code == 200
    sqlite_session.expire_all()
    old, new = _periods(sqlite_session, ana.id)
    assert old.ended_at == ended_at  # the closed period is left as it was
    assert new.ended_at is None and new.started_at >= ended_at
    # Audited as before: the audit rows are unchanged by the history.
    actions = sqlite_session.exec(
        select(AuditLog.action).where(AuditLog.interaction_id == f"teams:{acme.id}")
    ).all()
    assert actions.count("team_member_added") == 2
    assert actions.count("team_member_removed") == 1


def test_deleting_a_team_or_leaving_the_company_closes_the_periods(
    sqlite_session, test_client
):
    acme = _company(sqlite_session)
    _, headers = _person(sqlite_session, acme.id, "Owner", "Owner")
    ana, _ = _person(sqlite_session, acme.id, "Viewer", "Ana")
    bruno, _ = _person(sqlite_session, acme.id, "Viewer", "Bruno")
    one = test_client.post(_url(acme.id), json={"name": "One"}, headers=headers)
    two = test_client.post(_url(acme.id), json={"name": "Two"}, headers=headers)
    one, two = one.json()["id"], two.json()["id"]
    for team_id, user in ((one, ana), (two, ana), (two, bruno)):
        test_client.put(_url(acme.id, f"/{team_id}/members/{user.id}"), headers=headers)

    deleted = test_client.delete(_url(acme.id, f"/{two}"), headers=headers)
    assert deleted.status_code == 204
    sqlite_session.expire_all()
    for user in (ana, bruno):
        period = next(p for p in _periods(sqlite_session, user.id) if p.team_id == two)
        # The deleted team's periods stay, closed, with the name it had.
        assert period.ended_at is not None and period.team_name == "Two"
    assert [
        p.ended_at for p in _periods(sqlite_session, ana.id) if p.team_id == one
    ] == [None]

    removed = test_client.delete(
        f"/v1/company/{acme.id}/members/{ana.id}", headers=headers
    )
    assert removed.status_code in (200, 204)
    sqlite_session.expire_all()
    assert all(p.ended_at is not None for p in _periods(sqlite_session, ana.id))


def test_a_closed_period_is_never_edited_and_no_period_is_deleted(sqlite_session):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Viewer", "Ana")
    team = Team(company_id=acme.id, name="Platform")
    sqlite_session.add(team)
    sqlite_session.commit()
    teams.open_membership(sqlite_session, team, ana.id, by="human:owner", at=NOW)
    sqlite_session.commit()
    [period] = _periods(sqlite_session, ana.id)

    period.started_at = NOW - timedelta(days=30)  # rewriting when it started
    with pytest.raises(teams.HistoryRefused):
        sqlite_session.flush()
    sqlite_session.rollback()

    closed = teams.close_memberships(
        sqlite_session, acme.id, user_id=ana.id, by="human:owner", at=NOW
    )
    sqlite_session.commit()
    assert closed == 1
    # Closing again finds nothing open: the end is written once.
    assert (
        teams.close_memberships(
            sqlite_session, acme.id, user_id=ana.id, by="x", at=NOW + timedelta(1)
        )
        == 0
    )
    [period] = _periods(sqlite_session, ana.id)
    period.ended_at = NOW + timedelta(days=1)
    with pytest.raises(teams.HistoryRefused):
        sqlite_session.flush()
    sqlite_session.rollback()

    [period] = _periods(sqlite_session, ana.id)
    sqlite_session.delete(period)
    with pytest.raises(teams.HistoryRefused):
        sqlite_session.flush()
    sqlite_session.rollback()
    assert len(_periods(sqlite_session, ana.id)) == 1


# -- The report: the team at the time of the work -----------------------------


def test_the_team_at_the_time_is_pure_and_falls_back_before_the_history():
    started = datetime(2026, 11, 5)
    moved = datetime(2026, 11, 10, 9)
    periods = {
        7: [(1, started, moved), (2, moved, None)],
    }
    current = {7: [2]}

    def at(moment, person=7, start=started):
        return usage_report.teams_at(
            person, moment, current=current, periods=periods, started=start
        )

    assert at(datetime(2026, 11, 1)) == [2]  # before the history: today's team
    assert at(started) == [1]  # the history starts at its first instant
    assert at(moved - timedelta(seconds=1)) == [1]
    assert at(moved) == [2]  # an end is exclusive, a start inclusive
    assert at(datetime(2026, 11, 3), start=None) == [2]  # no history at all
    assert at(datetime(2026, 11, 12), person=8) == []  # in no team then
    assert at(datetime(2026, 11, 12), person=None) == []


def _work(session, company_id: int, person: User, at: datetime) -> None:
    """One task and one decision by *person*, both observed at *at*."""
    sprint = Sprint(
        company_id=company_id, sprint_name="s", started_by_user_id=person.id
    )
    session.add(sprint)
    session.commit()
    session.add(BacklogItem(sprint_id=sprint.id, title="t", created_at=at))
    session.add(
        RunReview(
            company_id=company_id,
            agent_run_id=0,
            event="approve",
            outcome="approval_recorded",
            author_name="a reviewer",
            author_user_id=person.id,
            created_at=at,
        )
    )
    session.commit()


def _history(session, company_id, ana, started):
    """Ana in Mobile from *started* to 10 Nov 09:00, then in Platform."""
    mobile = Team(company_id=company_id, name="Mobile")
    platform = Team(company_id=company_id, name="Platform")
    session.add_all([mobile, platform, TeamHistoryStart(started_at=started)])
    session.commit()
    session.add(TeamMember(company_id=company_id, team_id=platform.id, user_id=ana.id))
    moved = datetime(2026, 11, 10, 9)
    teams.open_membership(session, mobile, ana.id, by="migration:0052", at=started)
    teams.close_memberships(
        session, company_id, team_id=mobile.id, user_id=ana.id, by="o", at=moved
    )
    teams.open_membership(session, platform, ana.id, by="o", at=moved)
    session.commit()
    return mobile, platform


def _team_rows(report) -> dict[str, int]:
    return {row["label"]: row["tasks"] for row in report["by_team"]}


def test_the_report_keeps_the_team_a_person_was_in_at_the_time(sqlite_session):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Manager", "Ana")
    started = datetime(2026, 11, 5)
    mobile, _ = _history(sqlite_session, acme.id, ana, started)
    _work(sqlite_session, acme.id, ana, datetime(2026, 11, 3))  # before the history
    _work(sqlite_session, acme.id, ana, datetime(2026, 11, 6))  # in Mobile
    _work(sqlite_session, acme.id, ana, datetime(2026, 11, 10, 8, 59))  # in Mobile
    _work(sqlite_session, acme.id, ana, datetime(2026, 11, 11))  # in Platform

    report = usage_report.report(
        sqlite_session,
        acme.id,
        since=date(2026, 11, 1),
        until=date(2026, 11, 14),
        period="week",
        now=NOW,
    )
    assert _team_rows(report) == {"Mobile": 2, "Platform": 2}
    approvals = {row["label"]: row["approvals"] for row in report["by_team"]}
    assert approvals == {"Mobile": 2, "Platform": 2}
    assert report["team_history"] == {
        "started_at": "2026-11-05T00:00:00Z",
        "current_teams_cover": {
            "since": "2026-11-01",
            "before": "2026-11-05T00:00:00Z",
        },
    }

    # A deleted team keeps its name in the report.
    sqlite_session.delete(mobile)
    sqlite_session.commit()
    again = usage_report.report(
        sqlite_session,
        acme.id,
        since=date(2026, 11, 1),
        until=date(2026, 11, 14),
        period="week",
        now=NOW,
    )
    assert _team_rows(again) == {"Mobile": 2, "Platform": 2}

    # A range wholly inside the history uses no current team.
    later = usage_report.report(
        sqlite_session,
        acme.id,
        since=date(2026, 11, 6),
        until=date(2026, 11, 14),
        period="week",
        now=NOW,
    )
    assert later["team_history"]["current_teams_cover"] is None
    assert _team_rows(later) == {"Mobile": 2, "Platform": 1}
    text = usage_csv.render(later, "Acme")
    assert "# teams: each person's teams at the time of the work" in text


def test_without_a_history_every_piece_of_work_keeps_todays_team(sqlite_session):
    acme = _company(sqlite_session)
    ana, _ = _person(sqlite_session, acme.id, "Manager", "Ana")
    platform = Team(company_id=acme.id, name="Platform")
    sqlite_session.add(platform)
    sqlite_session.commit()
    sqlite_session.add(
        TeamMember(company_id=acme.id, team_id=platform.id, user_id=ana.id)
    )
    sqlite_session.commit()
    _work(sqlite_session, acme.id, ana, datetime(2026, 11, 3))
    report = usage_report.report(
        sqlite_session,
        acme.id,
        since=date(2026, 11, 1),
        until=date(2026, 11, 14),
        period="week",
        now=NOW,
    )
    assert _team_rows(report) == {"Platform": 1}
    assert report["team_history"] == {
        "started_at": None,
        "current_teams_cover": {
            "since": "2026-11-01",
            "before": "2026-11-15T00:00:00Z",
        },
    }
    text = usage_csv.render(report, "Acme")
    assert (
        "# teams: today's teams for work from 2026-11-01 to before "
        "2026-11-15T00:00:00Z; the teams at the time of the work after it" in text
    )


# -- Migration 0052 ----------------------------------------------------------


def test_the_migration_seeds_one_open_period_per_membership(tmp_path, monkeypatch):
    import migrations
    from sqlalchemy import text
    from sqlmodel import create_engine

    from alembic import command

    url = f"sqlite:///{tmp_path / 'history.sqlite'}"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    engine = create_engine(url)
    config = migrations.alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0051")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies (id, company_name, autonomy_level, "
                "default_scope_size, git_workflow) VALUES (1, 'Acme', 2, 'M', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash) VALUES "
                "(7, 'ana@acme.test', 'Ana', ''), (8, 'bruno@acme.test', 'Bruno', '')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO teams (id, company_id, name, role, created_at, "
                "updated_at) VALUES (3, 1, 'Platform', 'Viewer', '2026-01-01', "
                "'2026-01-01'), (4, 1, 'Mobile', 'Manager', '2026-01-01', "
                "'2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO team_members (company_id, team_id, user_id, added_by, "
                "added_at) VALUES (1, 3, 7, 'o', '2026-02-01'), "
                "(1, 4, 7, 'o', '2026-02-01'), (1, 4, 8, 'o', '2026-02-01')"
            )
        )
    before = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    migrations.upgrade_to_head(engine)
    assert migrations.current_revision(engine) == migrations.head_revision()
    assert migrations.schema_differences(engine) == []
    with engine.connect() as connection:
        [started] = connection.execute(
            text("SELECT started_at FROM team_history_start")
        ).scalars()
        rows = connection.execute(
            text(
                "SELECT company_id, team_id, team_name, user_id, started_at, "
                "started_by, ended_at FROM team_membership_periods "
                "ORDER BY team_id, user_id"
            )
        ).all()
    started = datetime.fromisoformat(str(started))
    assert started >= before - timedelta(seconds=1)
    assert [(r[0], r[1], r[2], r[3], r[5], r[6]) for r in rows] == [
        (1, 3, "Platform", 7, "migration:0052", None),
        (1, 4, "Mobile", 7, "migration:0052", None),
        (1, 4, "Mobile", 8, "migration:0052", None),
    ]
    assert {datetime.fromisoformat(str(r[4])) for r in rows} == {started}
