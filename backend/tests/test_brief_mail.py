"""The client's brief by mail (issue #331, services/management/brief_mail.py)."""

from __future__ import annotations

import secrets
import smtplib
from datetime import UTC, datetime, timedelta

import pytest
from config import settings
from models import AuditLog, BacklogItem, Sprint
from models.company import Company, CompanyMember, User
from models.manager import ManagerPolicy
from services.management import brief_mail
from sqlmodel import Session, SQLModel, create_engine, select

NOW = datetime(2026, 11, 8, 12, 0, 0)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "MANAGER_BRIEF_INTERVAL_HOURS", 24.0)
    eng = create_engine(
        f"sqlite:///{tmp_path / 'brief.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _member(session: Session, company: Company, role: str) -> User:
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(3)}@example.test",
        full_name=role,
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.add(CompanyMember(company_id=company.id, user_id=user.id, human_role=role))
    session.commit()
    return user


def _company(session: Session, *, policy: bool = True) -> Company:
    company = Company(company_name=f"Client {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    if policy:
        session.add(ManagerPolicy(company_id=company.id, rules=[]))
        session.commit()
    return company


class Outbox:
    def __init__(self, fail: Exception | None = None):
        self.sent = []
        self.fail = fail

    def __call__(self, message) -> None:
        if self.fail:
            raise self.fail
        self.sent.append(message)


def _audits(session: Session, company: Company) -> list[dict]:
    return [
        row.result
        for row in session.exec(
            select(AuditLog)
            .where(
                AuditLog.company_id == company.id, AuditLog.action == brief_mail.ACTION
            )
            .order_by(AuditLog.id)
        ).all()
    ]


def test_the_brief_goes_to_owners_and_managers_once_per_interval(engine):
    with Session(engine) as session:
        company = _company(session)
        owner = _member(session, company, "Owner")
        manager = _member(session, company, "Manager")
        viewer = _member(session, company, "Viewer")
        sprint = Sprint(
            company_id=company.id, sprint_name="S1", goal="g", status="active"
        )
        session.add(sprint)
        session.commit()
        session.add(
            BacklogItem(
                sprint_id=sprint.id,
                title="Greeting endpoint",
                status="done",
                comments=[
                    {
                        "author": "QA",
                        "text": "APPROVED: fine",
                        "timestamp": (NOW - timedelta(hours=2)).isoformat(),
                    }
                ],
                updated_at=NOW - timedelta(hours=2),
            )
        )
        session.commit()
        emails = {owner.email, manager.email}
        viewer_email = viewer.email
        company_id = company.id
    outbox = Outbox()

    assert brief_mail.brief_cycle(engine, send=outbox, now=NOW) == 1
    message = outbox.sent[0]
    assert set(message["To"].split(", ")) == emails
    assert viewer_email not in message["To"]
    text = message.get_content()
    assert "Tasks done: 1" in text and "Greeting endpoint" in text
    assert "no model wrote this brief" in text

    # Not due again within the interval; due once it passed, from where the last ended.
    assert (
        brief_mail.brief_cycle(engine, send=outbox, now=NOW + timedelta(hours=1)) == 0
    )
    assert (
        brief_mail.brief_cycle(engine, send=outbox, now=NOW + timedelta(hours=25)) == 1
    )
    assert len(outbox.sent) == 2
    with Session(engine) as session:
        rows = _audits(session, session.get(Company, company_id))
    assert [r["sent"] for r in rows] == [True, True]
    assert rows[1]["since"] == rows[0]["until"]
    assert rows[0]["recipients"] == 2
    # The audit never carries an address.
    assert not any("@" in str(r) for r in rows)


def test_a_failed_send_is_audited_and_retried_from_the_same_start(engine):
    with Session(engine) as session:
        company = _company(session)
        _member(session, company, "Owner")
        company_id = company.id

    failing = Outbox(fail=smtplib.SMTPRecipientsRefused({"a@b": (550, b"no")}))
    assert brief_mail.brief_cycle(engine, send=failing, now=NOW) == 0
    outbox = Outbox()
    # Retried once an hour, not on every check.
    assert (
        brief_mail.brief_cycle(engine, send=outbox, now=NOW + timedelta(minutes=5)) == 0
    )
    assert (
        brief_mail.brief_cycle(engine, send=outbox, now=NOW + timedelta(hours=1)) == 1
    )
    with Session(engine) as session:
        rows = _audits(session, session.get(Company, company_id))
    assert rows[0]["sent"] is False and rows[0]["error"] == "SMTPRecipientsRefused"
    assert "a@b" not in str(rows[0])
    assert rows[1]["sent"] is True
    assert rows[1]["since"] == rows[0]["since"]


def test_nothing_is_sent_without_smtp_a_policy_or_a_recipient(engine, monkeypatch):
    with Session(engine) as session:
        without_policy = _company(session, policy=False)
        _member(session, without_policy, "Owner")
        nobody = _company(session)
        _member(session, nobody, "Viewer")
        nobody_id = nobody.id
    outbox = Outbox()

    assert brief_mail.brief_cycle(engine, send=outbox, now=NOW) == 0
    assert outbox.sent == []
    with Session(engine) as session:
        rows = _audits(session, session.get(Company, nobody_id))
    assert rows == [
        {
            "since": (NOW - timedelta(hours=24)).isoformat(),
            "until": NOW.isoformat(),
            "sent": False,
            "error": "no Owner or Manager with an email",
        }
    ]

    monkeypatch.setattr(settings, "SMTP_HOST", "")
    with Session(engine) as session:
        company = _company(session)
        _member(session, company, "Owner")
    assert brief_mail.brief_cycle(engine, send=outbox, now=NOW) == 0
    assert outbox.sent == []


def test_the_render_lists_what_the_agent_decided_and_what_waits():
    company = Company(company_name="Acme", autonomy_level=1)
    subject, text = brief_mail.render(
        company,
        {
            "since": "2026-11-07T12:00:00",
            "until": "2026-11-08T12:00:00",
            "autonomy_level": 1,
            "done": {"count": 0, "tasks": []},
            "agent": {
                "suggested": 3,
                "taken_alone": 1,
                "overridden": 0,
                "decisions": [
                    {
                        "category": "accept_delivery",
                        "decision": "accept",
                        "rule": "accept_delivery",
                        "reason": "Verified, low risk.",
                        "decided_at": "2026-11-08T10:00:00",
                    },
                    {
                        "category": "approve_run",
                        "decision": "approve",
                        "rule": "approve_run",
                        "reason": "Only a suggestion.",
                        "decided_at": None,
                    },
                ],
            },
            "waiting": {"blocked_tasks": 2, "proposals": 0, "specifications": 1},
            "cost": {"tokens_since": 1200, "quota_limit": 10000, "quota_used": 4000},
            "record": {"flagged_rules": ["approve_run"]},
        },
    )
    assert subject == "La Fabrique: Acme: 0 done, 3 waiting for you"
    assert "accept_delivery: accept (rule accept_delivery): Verified, low risk." in text
    assert "Only a suggestion." not in text
    assert "blocked tasks: 2" in text and "specifications: 1" in text
    assert "proposals" not in text
    assert "quota used 4000 of 10000" in text
    assert "Rules suspended after overrides: approve_run" in text


def test_the_window_starts_where_the_last_sent_brief_ended(engine):
    with Session(engine) as session:
        company = _company(session)
        until = (NOW - timedelta(hours=30)).replace(tzinfo=UTC)
        session.add(
            AuditLog(
                company_id=company.id,
                action=brief_mail.ACTION,
                result={"sent": True, "until": until.isoformat()},
            )
        )
        session.commit()
        assert brief_mail.due(session, company.id, NOW) == NOW - timedelta(hours=30)
        assert brief_mail.due(session, company.id, NOW - timedelta(hours=10)) is None


def test_smtp_send_uses_starttls_and_login(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append(("quit",))

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["Subject"]))

    monkeypatch.setattr(brief_mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 2525)
    monkeypatch.setattr(settings, "SMTP_USER", "mailer")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "pw")
    monkeypatch.setattr(settings, "SMTP_SSL", False)
    monkeypatch.setattr(settings, "SMTP_STARTTLS", True)
    message = brief_mail.EmailMessage()
    message["Subject"] = "Brief"
    message.set_content("x")
    brief_mail.smtp_send(message)
    assert calls == [
        ("connect", "smtp.test", 2525, settings.SMTP_TIMEOUT_SECONDS),
        ("starttls",),
        ("login", "mailer"),
        ("send", "Brief"),
        ("quit",),
    ]
