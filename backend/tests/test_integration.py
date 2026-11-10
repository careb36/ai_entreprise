"""Integration planning for a sprint's task branches (services.release.integration)."""

import secrets

from models import BacklogItem, Company, Sprint
from services.release import integration


def _company(session):
    company = Company(company_name=f"C {secrets.token_hex(3)}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def test_plan_lists_branches_in_dependency_order(sqlite_session):
    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S1", scope_size="S")
    session = sqlite_session
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    a = BacklogItem(sprint_id=sprint.id, title="A", depends_on=[])
    b = BacklogItem(sprint_id=sprint.id, title="B", depends_on=[])
    session.add(a)
    session.add(b)
    session.commit()
    session.refresh(a)
    session.refresh(b)
    b.depends_on = [a.id]
    session.add(b)
    session.commit()

    plan = integration.plan(session, sprint.id)
    assert plan["integration_branch"] == f"fabrique/sprint-{sprint.id}"
    assert [t["task_id"] for t in plan["task_branches"]] == plan["merge_order"]
    # A must come before B.
    assert plan["merge_order"].index(a.id) < plan["merge_order"].index(b.id)
    assert plan["task_branches"][0]["branch"].startswith("fabrique/task-")

    # Reversed input still orders by dependency.
    ordered = integration._topological([b, a])
    assert [i.id for i in ordered] == [a.id, b.id]
