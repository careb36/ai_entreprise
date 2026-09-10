"""
Tests for CompanyService HITL task approval and rejection behavior.
"""

from sqlmodel import select

from models.company import Company
from models.sprint import Sprint, BacklogItem, OutboxEvent, TaskQueue
from services.company_service import CompanyService


def test_approve_task_rejection_requeues_backlog_item_with_feedback(sqlite_session):
    company = Company(company_name="Acme Corp")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)

    sprint = Sprint(company_id=company.id, sprint_name="Sprint Alpha", status="active")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)

    item = BacklogItem(
        id=101,
        sprint_id=sprint.id,
        title="Improve error handling",
        description="Add missing exception coverage",
        status="in_review",
        priority="high",
        assignee="Developer",
    )
    task = TaskQueue(
        id=101,
        interaction_id="interaction-123",
        status="pending",
    )
    sqlite_session.add(item)
    sqlite_session.add(task)
    sqlite_session.commit()

    result = CompanyService(sqlite_session).approve_task(
        task_id=task.id,
        approver_name="Manager",
        decision="rejected",
        feedback="The error handling is missing.",
    )

    assert result["new_status"] == "rejected"

    refreshed_item = sqlite_session.get(BacklogItem, item.id)
    assert refreshed_item is not None
    assert refreshed_item.status == "todo"

    event = sqlite_session.exec(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "backlog_item",
            OutboxEvent.aggregate_id == str(item.id),
        )
    ).one()
    assert event.payload["subject"] == "task.assign"
    assert event.payload["message"] == {
        "backlog_item_id": item.id,
        "interaction_id": "interaction-123",
        "agent": "Developer",
        "sprint_id": sprint.id,
        "human_review_feedback": "The error handling is missing.",
    }


