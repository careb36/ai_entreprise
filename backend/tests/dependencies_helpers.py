"""What the tests of dependencies between a sprint's tasks share: a company, a
sprint and a task with what it depends on. The tests are ``test_dependencies.py``,
``test_dependency_holds.py`` and ``test_dependency_history.py``.
"""

import secrets

from models import BacklogItem, Company, Sprint


def _company(session):
    company = Company(company_name=f"Deps {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _sprint(session, company, status="active"):
    sprint = Sprint(company_id=company.id, sprint_name="S", status=status)
    session.add(sprint)
    session.commit()
    return sprint


def _task(session, sprint, title, status="todo", depends_on=None, comments=None):
    item = BacklogItem(
        sprint_id=sprint.id,
        title=title,
        status=status,
        depends_on=depends_on,
        comments=comments or [],
    )
    session.add(item)
    session.commit()
    return item
