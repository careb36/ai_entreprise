"""
The review loop decides ``done`` from evidence and an explicit verdict.

- A delivery whose ``files_written`` check is refuted or unverifiable goes back
  for rework without asking the QA model (POSITIONING rule 5).
- Only a reply starting ``APPROVED:`` closes a task; any other reply is a
  failed review that is retried, never an approval.
"""

import asyncio
from uuid import uuid4

import pytest
import worker_stages
from models.agent_run import AgentRun, Verification
from models.company import Company
from models.event import TaskEvent
from models.sprint import BacklogItem, Sprint
from services.loop import task_worker
from services.loop.worker import execute as worker_execute
from services.loop.worker import intake, qa_review
from services.loop.worker import review as worker_review
from sqlmodel import Session, select

DELIVERABLE = "# Artifact: src/a.py\n\nprint('a')\n" + "word " * 120


class _FakeNATS:
    jetstream_enabled = False

    def __init__(self):
        self.published = []

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, payload))

    async def request(self, subject, payload, timeout=5.0):
        raise TimeoutError


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _item_in_review(session: Session, files_written: str | None) -> int:
    company = Company(company_name=f"Gate {files_written}", autonomy_level=2)
    session.add(company)
    session.commit()
    session.refresh(company)
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    session.add(sprint)
    session.commit()
    session.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id,
        title=f"Write a {uuid4().hex[:8]}",
        status="in_review",
        assignee="Developer",
        comments=[{"author": "Dev", "text": DELIVERABLE, "created_at": ""}],
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    if files_written is not None:
        run = AgentRun(
            company_id=company.id,
            external_ref=f"task-{item.id}",
            producer=task_worker.TASK_WORKER_PRODUCER,
            claims={"files": ["src/a.py"]},
            artifact_digest="d",
            artifact_size=1,
            status=files_written,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict=files_written,
                checks=[
                    {"name": "artifact_nonempty", "outcome": "pass", "observation": ""},
                    {
                        "name": "files_written",
                        "outcome": files_written,
                        "observation": "src/a.py content on disk differs from what was sent",
                    },
                ],
            )
        )
        session.commit()
    return item.id


def _run_cycle(monkeypatch, reply: str | None, session: Session, item_id: int):
    # The shared test database may hold other in_review items; only count the
    # QA calls about this test's item.
    title = session.get(BacklogItem, item_id).title
    calls = []

    def _review(agent, prompt, **kwargs):
        if title in prompt:
            calls.append(prompt)
        return reply

    monkeypatch.setattr(qa_review, "_call_llm_for_review", _review)
    asyncio.run(worker_review._review_cycle(_FakeNATS()))
    return calls


def _state(session: Session, item_id: int):
    session.expire_all()
    item = session.get(BacklogItem, item_id)
    return item.status, [(c["author"], c["text"]) for c in item.comments]


@pytest.mark.parametrize("outcome", ["refuted", "unverifiable"])
def test_unconfirmed_delivery_is_reworked_without_asking_qa(db, monkeypatch, outcome):
    item_id = _item_in_review(db, outcome)

    calls = _run_cycle(monkeypatch, "APPROVED: looks great", db, item_id)

    status, comments = _state(db, item_id)
    assert calls == []
    assert status == "todo"
    author, text = comments[-1]
    assert author == "Verifier"
    assert text.startswith("NEEDS WORK: delivery")
    assert "differs from what was sent" in text


def test_delivery_gate_blocks_after_the_rework_limit(db, monkeypatch):
    item_id = _item_in_review(db, "refuted")
    item = db.get(BacklogItem, item_id)
    history = []
    for attempt in range(qa_review.MAX_REWORKS):
        history.append(
            {
                "author": "Verifier",
                "text": "NEEDS WORK: delivery refuted — x",
                "created_at": "",
            }
        )
        history.append(
            {
                "author": "Dev",
                "text": f"{DELIVERABLE} retry {attempt}",
                "created_at": "",
            }
        )
    item.comments = item.comments + history
    db.commit()

    _run_cycle(monkeypatch, "APPROVED: fine", db, item_id)

    status, comments = _state(db, item_id)
    assert status == "blocked"
    assert "blocked for manual triage" in comments[-1][1]


def test_verified_delivery_goes_to_qa_and_explicit_approval_closes_it(db, monkeypatch):
    item_id = _item_in_review(db, "pass")

    calls = _run_cycle(monkeypatch, "APPROVED: complete and tested", db, item_id)

    status, comments = _state(db, item_id)
    assert len(calls) == 1
    assert status == "done"
    assert comments[-1] == ("QA", "APPROVED: complete and tested")


@pytest.mark.parametrize("reply", ["Looks fine to me.", "", None])
def test_reply_without_a_verdict_is_a_failed_review_not_an_approval(
    db, monkeypatch, reply
):
    item_id = _item_in_review(db, None)

    _run_cycle(monkeypatch, reply, db, item_id)

    status, comments = _state(db, item_id)
    assert status == "in_review"
    assert comments[-1][0] == "System"
    assert comments[-1][1].startswith("QA review failed")
    assert "without APPROVED:/NEEDS WORK:" in comments[-1][1]


def _events(session: Session, item_id: int) -> list[TaskEvent]:
    """The task's transitions; the effort observed at sprint close (issue
    #534, ``test_calibration.py``) is not one."""
    return session.exec(
        select(TaskEvent)
        .where(TaskEvent.task_id == item_id, TaskEvent.event_type != "effort_observed")
        .order_by(TaskEvent.id)
    ).all()


def test_after_too_many_failed_reviews_the_task_is_blocked_without_asking_qa(
    db, monkeypatch
):
    """Each unusable answer is a failed review; once ``MAX_QA_REVIEW_FAILURES``
    are recorded the next cycle blocks the task with the reason instead of
    asking QA again, even if QA would now approve."""
    item_id = _item_in_review(db, None)
    for _ in range(worker_review.MAX_QA_REVIEW_FAILURES):
        assert len(_run_cycle(monkeypatch, "Looks fine to me.", db, item_id)) == 1
        assert _state(db, item_id)[0] == "in_review"

    calls = _run_cycle(monkeypatch, "APPROVED: fine now", db, item_id)

    assert calls == []
    assert _state(db, item_id)[0] == "blocked"
    events = _events(db, item_id)
    assert [e.event_type for e in events].count(
        "review_failed"
    ) == worker_review.MAX_QA_REVIEW_FAILURES
    last = events[-1]
    assert (last.event_type, last.task_status, last.payload.get("reason")) == (
        "blocked",
        "blocked",
        "review retries exhausted",
    )


def test_a_task_qa_blocks_is_not_retried_by_the_same_cycle(db, monkeypatch):
    """QA's last rework blocks the task for a person; the cycle's retry pass,
    which runs right after, neither re-queues it nor sends it back to review."""
    item_id = _item_in_review(db, None)
    item = db.get(BacklogItem, item_id)
    item.comments = (
        item.comments
        + [
            {"author": "QA", "text": "NEEDS WORK: earlier", "created_at": ""}
            for _ in range(qa_review.MAX_REWORKS)
        ]
        + [{"author": "Dev", "text": DELIVERABLE, "created_at": ""}]
    )
    db.add(item)
    db.commit()
    title = item.title
    monkeypatch.setattr(
        qa_review,
        "_call_llm_for_review",
        lambda agent, prompt, **kwargs: (
            "NEEDS WORK: still no tests" if title in prompt else None
        ),
    )
    nats = _FakeNATS()

    asyncio.run(worker_review._review_cycle(nats))

    assert _state(db, item_id)[0] == "blocked"
    kinds = [e.event_type for e in _events(db, item_id)]
    assert kinds[kinds.index("reviewed") :] == ["reviewed", "blocked", "escalated"]
    # No task.assign for it, and no retry or recovery on Glassroom.
    assert not [p for _s, p in nats.published if p.get("backlog_item_id") == item_id]
    assert not [
        p
        for _s, p in nats.published
        if p.get("task_id") == item_id
        and p.get("semantic_type") in {"task.retry_queued", "task.recovered"}
    ]


def test_needs_work_still_schedules_rework(db, monkeypatch):
    item_id = _item_in_review(db, None)

    _run_cycle(monkeypatch, "NEEDS WORK: add input validation", db, item_id)

    status, comments = _state(db, item_id)
    assert status == "todo"
    assert ("QA", "NEEDS WORK: add input validation") in comments


def test_reworked_task_is_dispatched_again_not_bounced_back_to_review(db, monkeypatch):
    item_id = _item_in_review(db, None)
    item = db.get(BacklogItem, item_id)
    item.comments = item.comments + [
        {"author": "QA", "text": "NEEDS WORK: add input validation", "created_at": ""}
    ]
    item.status = "todo"
    db.commit()
    nats = _FakeNATS()

    asyncio.run(intake._dispatch_todo_items(nats))

    db.expire_all()
    assert db.get(BacklogItem, item_id).status != "in_review"
    assert not any(
        payload.get("semantic_type") == "task.recovered"
        and payload.get("task_id") == item_id
        for _subject, payload in nats.published
    )


def test_rework_prompt_carries_the_review_feedback(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, base_url):
            self.timeout = None

        def chat(self, messages, model, role):
            captured["prompt"] = messages[1]["content"]
            return {"choices": [{"message": {"content": "done"}}]}

    monkeypatch.setattr(worker_execute, "LLMProxyClient", _Client)
    item = {
        "id": 1,
        "title": "Write a",
        "comments": [
            {"author": "Dev", "text": "first attempt"},
            {"author": "QA", "text": "NEEDS WORK: add input validation"},
            {
                "author": "System",
                "text": "Autopilot rework scheduled after QA feedback (1/2).",
            },
        ],
    }

    worker_execute._execute_task(item, {"role": "Developer"}, "Sprint 1")

    assert "## Review feedback to address" in captured["prompt"]
    assert "QA: NEEDS WORK: add input validation" in captured["prompt"]
    assert "first attempt" not in captured["prompt"]


def test_another_companys_run_cannot_gate_this_task(db, monkeypatch):
    item_id = _item_in_review(db, None)
    intruder = Company(company_name="Intruder", autonomy_level=1)
    db.add(intruder)
    db.commit()
    db.refresh(intruder)
    # POST /v1/agent_runs accepts any producer and reference from any company.
    run = AgentRun(
        company_id=intruder.id,
        external_ref=f"task-{item_id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        claims={"files": ["src/a.py"]},
        artifact_digest="d",
        artifact_size=1,
        version=99,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    db.add(
        Verification(
            company_id=intruder.id,
            agent_run_id=run.id,
            verdict="refuted",
            checks=[
                {"name": "files_written", "outcome": "refuted", "observation": "x"}
            ],
        )
    )
    db.commit()

    calls = _run_cycle(monkeypatch, "APPROVED: complete", db, item_id)

    status, _comments = _state(db, item_id)
    assert len(calls) == 1
    assert status == "done"


def test_qa_reads_the_company_s_run_files_whole_and_never_another_company_s(
    db, monkeypatch
):
    from services.loop import review_digest

    item_id = _item_in_review(db, "verified")
    item = db.get(BacklogItem, item_id)
    company_id = db.get(Sprint, item.sprint_id).company_id
    whole = "\n".join(f"line {i}" for i in range(1000))
    targets = [{"path": "src/a.py", "content": whole}]
    item.comments = [
        {"author": "Dev", "text": review_digest.build(targets), "created_at": ""}
    ]
    db.add(item)
    run = db.exec(select(AgentRun).where(AgentRun.company_id == company_id)).one()
    run.artifact_text = f"Done.\n\n```python path=src/a.py\n{whole}\n```"
    intruder = Company(company_name="Intruder view", autonomy_level=1)
    db.add_all([run, intruder])
    db.commit()
    db.refresh(intruder)
    db.add(
        AgentRun(
            company_id=intruder.id,
            external_ref=f"task-{item_id}",
            producer=task_worker.TASK_WORKER_PRODUCER,
            artifact_text="```python path=src/a.py\nINTRUDER\n```",
            artifact_digest="d",
            artifact_size=1,
            version=99,
        )
    )
    db.commit()

    calls = _run_cycle(monkeypatch, "APPROVED: complete", db, item_id)

    assert len(calls) == 1
    assert "line 999" in calls[0] and "not shown here" not in calls[0]
    assert "INTRUDER" not in calls[0]
