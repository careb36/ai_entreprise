"""Task lifecycle events (services.events)."""

import secrets

from models import Company, Sprint
from services import events


def _company(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def test_record_and_list_events(sqlite_session):
    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)

    events.record(
        sqlite_session, company.id, sprint.id, None, "sprint.started", {"x": 1}
    )
    rows = events.list_for_sprint(sqlite_session, sprint.id)
    assert len(rows) == 1
    assert rows[0].event_type == "sprint.started"
