"""Tasks that likely repeat each other are detected (issue #766).

Sprint 3 of the factory's own run planned task 14 and carried over task 19,
the same work, and both ran. ``services/tasks/similarity.py`` scores two
titles without a model; the score is a claim, recorded as a *proposed*
``duplicate`` relation a person confirms or rejects. A carried task that
repeats one the new sprint already holds is blocked instead of queued; two
tasks of one plan only get the proposed relation.
"""

from __future__ import annotations

from uuid import uuid4

from models import BacklogItem, Company, OutboxEvent, Sprint
from services.loop import carry_over
from services.tasks import relations, similarity, task_events, task_projection
from sqlmodel import select

# The titles as they were recorded in sprints 2 and 3.
SPRINT_3_PLAN = "Deliver SSO tests with fake DNS and clock plus docs for configuration and operations"
SPRINT_2_TASK = (
    "Add SSO tests with fake DNS and clock, plus configuration and operations docs"
)
CARDS_CODE = "Add bench baseline reference to approval-rate and tokens-per-task cards"
CARDS_STRINGS = (
    "Add i18n es/en strings for approval-rate and tokens-per-task card baselines"
)


def _company(session):
    company = Company(company_name=f"Dup {uuid4().hex[:6]}", autonomy_level=2)
    session.add(company)
    session.commit()
    return company


def _sprint(session, company, status="active"):
    sprint = Sprint(company_id=company.id, sprint_name="S", status=status)
    session.add(sprint)
    session.commit()
    return sprint


def _task(session, sprint, title, status="todo", assignee=None):
    item = BacklogItem(
        sprint_id=sprint.id, title=title, status=status, assignee=assignee
    )
    session.add(item)
    session.commit()
    return item


def _queued_ids(session):
    return {
        int(row.aggregate_id)
        for row in session.exec(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == "backlog_item")
        ).all()
    }


def test_the_recorded_duplicate_scores_above_the_threshold_and_unrelated_work_below():
    assert similarity.likely_duplicate(SPRINT_3_PLAN, SPRINT_2_TASK)
    assert similarity.similarity(SPRINT_3_PLAN, SPRINT_2_TASK) >= 0.75
    # The same cards, one task for the code and one for its strings.
    assert not similarity.likely_duplicate(CARDS_CODE, CARDS_STRINGS)
    assert similarity.similarity("", SPRINT_2_TASK) == 0.0
    assert similarity.words("Adds the docs") == {"doc"}


def test_a_carried_task_that_repeats_one_in_the_new_sprint_is_held_not_queued(
    sqlite_session,
):
    company = _company(sqlite_session)
    old = _sprint(sqlite_session, company, status="completed")
    blocked = _task(sqlite_session, old, SPRINT_2_TASK, status="blocked")
    task_events.record(
        sqlite_session,
        blocked,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    sqlite_session.commit()
    new = _sprint(sqlite_session, company)
    planned = _task(sqlite_session, new, SPRINT_3_PLAN)

    [clone] = carry_over.carry_over(
        sqlite_session, company.id, [blocked.id], new, "ana", "Manager"
    )
    sqlite_session.commit()

    assert clone.status == "blocked"
    assert clone.id not in _queued_ids(sqlite_session)
    relation = relations.between(sqlite_session, clone.id, planned.id)
    assert (relation.kind, relation.state) == ("duplicate", "proposed")
    assert relation.proposed_by.startswith(similarity.RULE)
    assert relation.proposed_role == "system"
    comment = clone.comments[-1]["text"]
    assert comment.startswith(f"{carry_over.DUPLICATE_HOLD_PREFIX} task {planned.id}")
    # It waits for a person: the worker never retries it on its own.
    record = task_projection.record_of(sqlite_session, clone)
    assert record.next_step() is None
    assert task_events.verify_chain(sqlite_session, clone.id)["status"] == "intact"


def test_a_carried_task_with_no_match_is_queued_as_before(sqlite_session):
    company = _company(sqlite_session)
    old = _sprint(sqlite_session, company, status="completed")
    blocked = _task(sqlite_session, old, SPRINT_2_TASK, status="blocked")
    task_events.record(
        sqlite_session,
        blocked,
        "blocked",
        "system",
        status="blocked",
        text="QA quality gate failed — task blocked pending manual triage.",
    )
    sqlite_session.commit()
    new = _sprint(sqlite_session, company)
    _task(sqlite_session, new, CARDS_CODE)
    _task(sqlite_session, new, SPRINT_3_PLAN, status="done")  # done never counts

    [clone] = carry_over.carry_over(
        sqlite_session, company.id, [blocked.id], new, "ana", "Manager"
    )
    sqlite_session.commit()

    assert clone.status == "todo" and clone.id in _queued_ids(sqlite_session)
    assert [r.kind for r in relations.for_task(sqlite_session, clone.id)] == [
        "relates_to"
    ]


def test_two_tasks_of_one_plan_get_a_proposed_duplicate_and_both_still_run(
    sqlite_session,
):
    company = _company(sqlite_session)
    sprint = _sprint(sqlite_session, company)
    first = _task(sqlite_session, sprint, SPRINT_3_PLAN)
    second = _task(sqlite_session, sprint, SPRINT_2_TASK)
    other = _task(sqlite_session, sprint, CARDS_CODE)

    [relation] = relations.propose_likely_duplicates(
        sqlite_session, [first, second, other]
    )
    sqlite_session.commit()

    assert {relation.task_id, relation.related_task_id} == {first.id, second.id}
    assert relation.state == "proposed"
    assert first.status == second.status == "todo"
    # Asked again, the pair is already related: nothing new.
    assert relations.propose_likely_duplicates(sqlite_session, [first, second]) == []
