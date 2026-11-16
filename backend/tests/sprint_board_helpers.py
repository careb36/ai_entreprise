"""What the tests of the sprint board share (issue #439): a company with a
sprint and its tasks. The tests are
``test_sprint_board*.py``.
"""

from __future__ import annotations

from uuid import uuid4

from models import (
    BacklogItem,
    Company,
    Sprint,
)
from sqlmodel import Session

# ── The store, its events and the worker ─────────────────────────────────────


def _setup(session: Session, *, tasks: int = 2):
    company = Company(company_name=f"Board {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="Board")
    session.add(sprint)
    session.commit()
    items = [
        BacklogItem(
            sprint_id=sprint.id,
            title=f"Task number {i}",
            description="Work on src/store.py." if i == 0 else "",
            assignee="Developer",
        )
        for i in range(tasks)
    ]
    session.add_all(items)
    session.commit()
    return company, sprint, items
