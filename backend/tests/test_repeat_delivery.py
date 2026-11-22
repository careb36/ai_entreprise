"""A rework that delivers the same bytes as the refuted attempt before is
blocked for a person, not sent back again (``services/loop/repeat_delivery.py``,
from Paperclip's rewake throttle). The digests are the backend's own record
of what it sent (``evidence["expected"]``)."""

import asyncio
from uuid import uuid4

import pytest
import worker_stages
from models.agent_run import AgentRun, Verification
from models.company import Company
from models.sprint import BacklogItem, Sprint
from services.loop import repeat_delivery, task_worker
from services.loop.worker import qa_review
from services.loop.worker import review as worker_review
from test_review_gate import DELIVERABLE, _FakeNATS

TESTS_FAILED = {
    "name": "tests_run",
    "outcome": "refuted",
    "observation": "`pytest` exited 1: failing tests: tests/test_a.py::test_req_1_1",
}
WRITTEN = {
    "name": "files_written",
    "outcome": "pass",
    "observation": "1 file(s) written",
}
UNCONFIRMED = {
    "name": "files_written",
    "outcome": "refuted",
    "observation": "src/a.py content on disk differs from what was sent",
}


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    monkeypatch.setattr(
        qa_review, "_call_llm_for_review", lambda *a, **k: "APPROVED: x"
    )
    return sqlite_session


def _item(session):
    company = Company(company_name=f"Repeat {uuid4().hex[:6]}", autonomy_level=2)
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", scope_size="S")
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title=f"Write {uuid4().hex[:8]}",
        status="in_review",
        assignee="Developer",
        comments=[{"author": "Dev", "text": DELIVERABLE, "created_at": ""}],
    )
    session.add(item)
    session.commit()
    return company, item


def _delivery(session, company, item, version, digests, checks):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer=task_worker.TASK_WORKER_PRODUCER,
        version=version,
        claims={"files": sorted(digests)},
        artifact_digest="d",
        artifact_size=1,
        status="refuted",
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="refuted",
            checks=checks,
            evidence={"expected": digests},
        )
    )
    session.commit()
    return run


def _cycle(session, item):
    asyncio.run(worker_review._review_cycle(_FakeNATS()))
    session.expire_all()
    item = session.get(BacklogItem, item.id)
    return item.status, item.comments[-1]["text"]


def test_the_same_bytes_again_are_blocked_for_a_person(db):
    company, item = _item(db)
    first = _delivery(db, company, item, 1, {"src/a.py": "aa"}, [WRITTEN, TESTS_FAILED])
    second = _delivery(
        db, company, item, 2, {"src/a.py": "aa"}, [WRITTEN, TESTS_FAILED]
    )

    status, text = _cycle(db, item)

    assert status == "blocked"
    assert f"run #{second.id} sent the same 1 file(s)" in text
    assert f"as run #{first.id}" in text and "blocked for manual triage" in text


def test_a_changed_delivery_goes_back_for_another_attempt(db):
    company, item = _item(db)
    _delivery(db, company, item, 1, {"src/a.py": "aa"}, [WRITTEN, TESTS_FAILED])
    _delivery(db, company, item, 2, {"src/a.py": "bb"}, [WRITTEN, TESTS_FAILED])

    status, text = _cycle(db, item)

    assert status == "todo" and text.startswith("NEEDS WORK: tests failed")


def test_an_unconfirmed_delivery_may_be_sent_again(db):
    company, item = _item(db)
    _delivery(db, company, item, 1, {"src/a.py": "aa"}, [UNCONFIRMED])
    _delivery(db, company, item, 2, {"src/a.py": "aa"}, [UNCONFIRMED])

    status, text = _cycle(db, item)

    assert status == "todo" and text.startswith("NEEDS WORK: delivery")


def test_what_counts_as_the_same(db):
    company, item = _item(db)
    assert repeat_delivery.repeated(db, company.id, item.id) is None
    _delivery(db, company, item, 1, {"src/a.py": "aa"}, [WRITTEN])
    assert repeat_delivery.repeated(db, company.id, item.id) is None  # one attempt
    _delivery(db, company, item, 2, {"src/a.py": "aa", "src/b.py": "cc"}, [WRITTEN])
    assert repeat_delivery.repeated(db, company.id, item.id) is None  # a file more
    _delivery(db, company, item, 3, {"src/a.py": "aa", "src/b.py": "cc"}, [WRITTEN])
    assert repeat_delivery.repeated(db, company.id, item.id)["files"] == 2
    _delivery(db, company, item, 4, {}, [WRITTEN])
    _delivery(db, company, item, 5, {}, [WRITTEN])
    assert repeat_delivery.repeated(db, company.id, item.id) is None  # no files
