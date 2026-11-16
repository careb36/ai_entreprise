"""The sprint board in the loop (issue #439, ``services/tasks/sprint_board.py``,
``services/loop/worker/sprint_notes.py``): a delivery writes its notes with
the author, a later note supersedes an earlier one, the prompt section shows
the relevant active notes with what was observed beside them, bounded and cut
by the budget before the required sections; the observation is a field apart
from the claim; the retrospective counts rework naming a contradiction and
offers the notes worth keeping. The shared rows are in
``sprint_board_helpers``.
"""

from __future__ import annotations

from config import settings
from models import (
    AgentRun,
    BacklogItem,
    SprintBoardNote,
    TaskEvent,
    Verification,
)
from models.agent_run import TASK_WORKER_PRODUCER
from services.learning import retrospective
from services.loop.worker import prompt_budget, sprint_notes
from services.tasks import sprint_board, task_projection
from sprint_board_helpers import _setup
from sqlmodel import select


def _verified_run(session, company, item, paths, verdict="verified"):
    earlier = session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).all()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=len(earlier) + 1,
        producer=TASK_WORKER_PRODUCER,
        subject={"backlog_item_id": item.id, "title": item.title},
        artifact_digest="0" * 64,
        artifact_size=0,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            evidence={
                "workspace_writes": [{"path": p, "status": "written"} for p in paths]
            },
        )
    )
    session.commit()
    return run


def test_a_delivery_writes_its_notes_with_the_author_and_records_the_rest(
    sqlite_session,
):
    engine = sqlite_session.get_bind()
    company, sprint, items = _setup(sqlite_session)
    deliverable = (
        "NOTE kind=decision files=src/store.py\nUTC everywhere\nEND\n"
        "NOTE kind=rumour\nnope\nEND\n"
    )
    item = {"id": items[0].id, "assignee": "Developer"}
    agent = {"role": "Developer", "name": "Dev"}
    sprint_ref = {"id": sprint.id, "company_id": company.id}

    first = sprint_notes.record_from_delivery(
        engine, item, agent, sprint_ref, deliverable
    )
    # A rework attempt that repeats the note does not write it twice.
    again = sprint_notes.record_from_delivery(
        engine, item, agent, sprint_ref, deliverable
    )

    assert len(first["notes"]) == 1 and again["notes"] == []
    assert [r["reason"] for r in first["rejected"]] == ["unknown kind 'rumour'"]
    sqlite_session.expire_all()
    note = sqlite_session.get(SprintBoardNote, first["notes"][0])
    assert (note.author_role, note.author_name, note.author_task_id) == (
        "Developer",
        "Dev",
        items[0].id,
    )
    assert note.company_id == company.id and note.status == "active"
    events = sqlite_session.exec(
        select(TaskEvent).where(TaskEvent.task_id == items[0].id)
    ).all()
    added = [e for e in events if e.event_type == "board_note"]
    assert len(added) == 1 and added[0].actor == "Developer"
    assert added[0].payload["note_id"] == note.id and added[0].payload["claim"] is True
    assert any(e.event_type == "board_note_rejected" for e in events)
    # A note is advice to other tasks: it says nothing of this task's state,
    # and renders no comment on its thread.
    assert sqlite_session.get(BacklogItem, items[0].id).comments in (None, [])
    record = task_projection.records_for(sqlite_session, [items[0]])[items[0].id]
    assert record.rework_request_count() == 0


def test_a_later_note_supersedes_an_earlier_one_and_a_bad_link_is_dropped(
    sqlite_session,
):
    engine = sqlite_session.get_bind()
    company, sprint, items = _setup(sqlite_session)
    sprint_ref = {"id": sprint.id, "company_id": company.id}
    agent = {"role": "Developer"}
    old = sprint_notes.record_from_delivery(
        engine, {"id": items[0].id}, agent, sprint_ref, "NOTE kind=decision\nA\nEND"
    )["notes"][0]
    new = sprint_notes.record_from_delivery(
        engine,
        {"id": items[1].id},
        agent,
        sprint_ref,
        f"NOTE kind=decision supersedes={old}\nB\nEND\n"
        "NOTE kind=gotcha supersedes=9999\nC\nEND",
    )
    sqlite_session.expire_all()
    assert sqlite_session.get(SprintBoardNote, old).status == "superseded"
    assert len(new["notes"]) == 2
    assert "not an active note" in new["rejected"][0]["reason"]
    kept = sqlite_session.get(SprintBoardNote, new["notes"][1])
    assert kept.text == "C" and kept.supersedes_id is None


def test_the_prompt_section_shows_relevant_active_notes_with_what_was_observed(
    sqlite_session, monkeypatch
):
    monkeypatch.setattr(settings, "SPRINT_BOARD_MAX_CHARS", 4000)
    engine = sqlite_session.get_bind()
    company, sprint, items = _setup(sqlite_session, tasks=3)
    run = _verified_run(sqlite_session, company, items[1], ["src/store.py"])
    _verified_run(sqlite_session, company, items[1], ["src/api.py"], "refuted")
    add = sprint_board.add
    on_store = add(
        sqlite_session,
        sprint,
        kind="decision",
        text="UTC everywhere",
        author_role="Developer",
        item=items[1],
        links={"files": ["src/store.py", "src/api.py"]},
    )
    wrong = add(
        sqlite_session,
        sprint,
        kind="gotcha",
        text="Wrong idea",
        author_role="Developer",
        item=items[2],
    )
    add(
        sqlite_session,
        sprint,
        kind="gotcha",
        text="Elsewhere",
        author_role="QA",
        item=items[2],
        links={"files": ["web/app.ts"]},
    )
    sprint_board.mark_wrong(sqlite_session, wrong, "lead@example.com", "not so")
    sqlite_session.commit()

    text = sprint_notes.board_section({"id": items[0].id}, company.id, engine=engine)

    assert text.startswith("\n\n## Sprint board")
    assert (
        f"note #{on_store.id} · decision · claimed by Developer, task #{items[1].id}"
        in text
    )
    assert "relevant by files: UTC everywhere" in text
    assert (
        f"src/store.py: observed: changed by verified run #{run.id} (task #{items[1].id})"
        in text
    )
    # A refuted run's write is not an observation that the file changed.
    assert "src/api.py: not observed" in text
    assert "Wrong idea" not in text and "Elsewhere" not in text
    assert "NOTE kind=" in text  # how to write one


def test_the_section_is_bounded_and_off_at_zero(sqlite_session, monkeypatch):
    engine = sqlite_session.get_bind()
    company, sprint, items = _setup(sqlite_session)
    for i in range(30):
        sprint_board.add(
            sqlite_session,
            sprint,
            kind="decision",
            text=f"Decision {i} " + "y" * 200,
            author_role="Developer",
            item=items[1],
        )
    sqlite_session.commit()
    monkeypatch.setattr(settings, "SPRINT_BOARD_MAX_CHARS", 2000)
    text = sprint_notes.board_section({"id": items[0].id}, company.id, engine=engine)
    assert len(text) <= 2000
    assert "Decision 29" in text and "Decision 0 " not in text
    assert "more relevant notes on the board were left out" in text
    monkeypatch.setattr(settings, "SPRINT_BOARD_MAX_CHARS", 0)
    assert (
        sprint_notes.board_section({"id": items[0].id}, company.id, engine=engine) == ""
    )


def test_the_budget_cuts_the_board_before_the_required_sections():
    Section = prompt_budget.Section
    sections = [
        Section("output_format", "f" * 400),
        Section("task", "t" * 400),
        Section("repository", "r" * 400, 3),
        Section(sprint_notes.SECTION_NAME, "b" * 2000, sprint_notes.PRIORITY),
        Section("review_feedback", "v" * 400),
    ]
    text, measure = prompt_budget.fit(sections, budget=500)
    assert measure["cut"][0] == sprint_notes.SECTION_NAME
    assert "f" * 400 in text and "t" * 400 in text and "v" * 400 in text


def test_the_developer_prompt_carries_the_board_section(monkeypatch):
    from services.loop.worker import execute

    monkeypatch.setattr(
        execute.sprint_notes,
        "board_section",
        lambda item, cid, engine: "\n\n## Sprint board\nX",
    )
    monkeypatch.setattr(
        execute.developer_tools, "repository_section", lambda *a, **k: ""
    )
    monkeypatch.setattr(execute.related, "related_code_section", lambda *a, **k: "")
    text, _feedback, measure = execute._developer_prompt(
        {"id": 1, "title": "T", "comments": []},
        {"role": "Developer"},
        "S",
        None,
        None,
        None,
        None,
        1,
        None,
        None,
        None,
    )
    assert "## Sprint board\nX" in text
    entry = next(s for s in measure["sections"] if s["name"] == "sprint_board")
    assert entry["priority"] == sprint_notes.PRIORITY


def test_the_observation_is_a_field_apart_from_the_claim(sqlite_session):
    company, sprint, items = _setup(sqlite_session)
    run = _verified_run(sqlite_session, company, items[0], ["src/store.py"])
    note = sprint_board.add(
        sqlite_session,
        sprint,
        kind="interface_change",
        text="save() takes a dict",
        author_role="Developer",
        item=items[0],
        links={"files": ["src/store.py", "src/other.py"]},
    )
    sqlite_session.commit()
    claim = sprint_board.as_dict(note)
    shown = sprint_notes.with_observations(sqlite_session, company.id, [claim])[0]
    assert "observed" not in claim
    assert shown["links"] == claim["links"] and shown["text"] == claim["text"]
    assert shown["observed"] == {
        "files": {
            "src/store.py": {"run_id": run.id, "backlog_item_id": items[0].id},
            "src/other.py": None,
        }
    }
    # The stored note has no field for an observation at all.
    assert "observed" not in SprintBoardNote.model_fields


# ── The measure ──────────────────────────────────────────────────────────────


def test_counts_rework_that_names_a_contradiction_with_another_task(sqlite_session):
    _company, sprint, items = _setup(sqlite_session, tasks=3)
    note = sprint_board.add(
        sqlite_session,
        sprint,
        kind="decision",
        text="UTC",
        author_role="Developer",
        item=items[1],
        links={"files": ["src/clock.py"]},
    )
    items[0].comments = [
        {"author": "QA", "text": f"NEEDS WORK: contradicts task #{items[1].id}"},
        {"author": "QA", "text": "NEEDS WORK: conflicts with src/clock.py"},
        {"author": "QA", "text": f"NEEDS WORK: inconsistent with note #{note.id}"},
        {"author": "QA", "text": "NEEDS WORK: conflicts with Task number 2's output"},
        # Not counted: no contradiction word, or no other task named.
        {"author": "QA", "text": f"NEEDS WORK: see task #{items[1].id}"},
        {"author": "QA", "text": f"NEEDS WORK: conflicts with task #{items[0].id}"},
        {"author": "QA", "text": "NEEDS WORK: conflict somewhere"},
        {"author": "QA", "text": "Looks contradictory to task #1 but fine"},
    ]
    sqlite_session.add(items[0])
    sqlite_session.commit()
    metrics = sprint_board.sprint_metrics(sqlite_session, sprint.id)
    assert metrics["contradiction_rework"] == 4
    assert metrics["rework"] == 7
    assert (
        metrics["notes"]["total"] == 1 and metrics["notes"]["by_kind"]["decision"] == 1
    )


def test_the_retrospective_offers_the_notes_worth_keeping(sqlite_session):
    engine = sqlite_session.get_bind()
    _company, sprint, items = _setup(sqlite_session)
    keep = sprint_board.add(
        sqlite_session,
        sprint,
        kind="gotcha",
        text="Cache is per process",
        author_role="Developer",
        item=items[0],
    )
    sprint_board.add(
        sqlite_session,
        sprint,
        kind="question",
        text="Who owns auth?",
        author_role="Developer",
        item=items[0],
    )
    wrong = sprint_board.add(
        sqlite_session,
        sprint,
        kind="decision",
        text="Use XML",
        author_role="Developer",
        item=items[1],
    )
    sprint_board.mark_wrong(sqlite_session, wrong, "lead@example.com")
    sqlite_session.commit()
    metrics = retrospective.build(engine, sprint.id)
    assert [n["id"] for n in metrics["promotable_notes"]] == [keep.id]
    assert metrics["board_notes"]["marked_wrong"] == 1
    summary = retrospective.summary(metrics)
    assert "Board notes worth keeping (agents' claims, not verified" in summary
    assert "Cache is per process" in summary and "Use XML" not in summary
