"""What agents learn from earlier sprints: company-scoped memory and lessons."""

import secrets
from types import SimpleNamespace

import pytest
import worker_stages
from models import BacklogItem, Company, Sprint
from services.loop.worker import close as worker_close
from services.loop.worker import execute as worker_execute
from services.loop.worker import prompt
from services.models import model_factory
from services.platform import engram_processor


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _company(session, name="Lessons"):
    company = Company(company_name=f"{name} {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    return company


def _sprint(session, company, status="completed"):
    sprint = Sprint(company_id=company.id, sprint_name="S", status=status)
    session.add(sprint)
    session.commit()
    return sprint


def _item(session, sprint, title, comments, status="done"):
    item = BacklogItem(
        sprint_id=sprint.id, title=title, status=status, comments=comments
    )
    session.add(item)
    session.commit()
    return item


def _comment(author, text, minute):
    return {
        "author": author,
        "text": text,
        "created_at": f"2026-10-18T10:{minute:02d}:00",
    }


def test_memory_search_is_scoped_to_the_company(monkeypatch):
    calls = []

    class _Processor:
        def query_relevant_engrams(self, story, limit, role_filter, company_id):
            calls.append(
                {"story": story, "role": role_filter, "company_id": company_id}
            )
            return "past decisions"

    monkeypatch.setattr(engram_processor, "get_engram_processor", lambda: _Processor())
    item = {"title": "Add login", "description": "JWT"}

    assert (
        prompt._fetch_memory_context(item, {"role": "Developer"}, 7) == "past decisions"
    )
    assert calls == [{"story": "Add login JWT", "role": "Developer", "company_id": 7}]

    # No company: no search at all, rather than one across every tenant.
    assert prompt._fetch_memory_context(item, {"role": "Developer"}, None) == ""
    assert len(calls) == 1


def test_lessons_come_from_the_companys_latest_completed_sprints(db):
    company = _company(db)
    oldest = _sprint(db, company)
    _item(db, oldest, "Old task", [_comment("QA", "NEEDS WORK: an old lesson", 1)])
    previous = _sprint(db, company)
    _item(
        db,
        previous,
        "Login form",
        [
            _comment("Developer", "Here is the form", 2),
            _comment("QA", "NEEDS WORK: validate the email field", 3),
            _comment(
                "Verifier", "NEEDS WORK: tests failed — exit status 1. Fix the code", 4
            ),
            _comment(
                "Verifier", "NEEDS WORK: delivery unconfirmed — no daemon. Re-run", 5
            ),
            _comment("QA", "APPROVED: good now", 6),
        ],
    )
    last = _sprint(db, company)
    _item(
        db,
        last,
        "Reset password",
        [_comment("QA", "NEEDS WORK: never log the token", 7)],
    )
    _item(db, last, "Blocked one", [], status="blocked")
    current = _sprint(db, company, status="active")
    _item(
        db, current, "Now", [_comment("QA", "NEEDS WORK: current sprint feedback", 8)]
    )
    other = _sprint(db, _company(db, "Other"))
    _item(db, other, "Elsewhere", [_comment("QA", "NEEDS WORK: another tenant", 9)])

    lessons = prompt._load_recent_lessons(company.id, current.id)

    assert f"Sprint {last.id} retrospective" in lessons
    assert f"Sprint {previous.id} retrospective" in lessons
    assert "- Blocked: 1 tasks" in lessons
    assert "never log the token" in lessons
    assert "validate the email field" in lessons
    assert "tests failed" in lessons
    # Most recent first.
    assert lessons.index("never log the token") < lessons.index(
        "validate the email field"
    )
    for absent in (
        "delivery unconfirmed",  # infrastructure, not a lesson
        "good now",  # approvals are not rework
        "current sprint feedback",  # the sprint in progress
        "another tenant",  # another company
        "an old lesson",  # beyond the latest two completed sprints
        f"Sprint {oldest.id} retrospective",
    ):
        assert absent not in lessons


def test_no_lessons_without_company_or_history(db):
    company = _company(db)
    current = _sprint(db, company, status="active")
    assert prompt._load_recent_lessons(company.id, current.id) == ""
    assert prompt._load_recent_lessons(None, current.id) == ""


def test_lessons_reach_the_developer_prompt(monkeypatch):
    prompts = []

    class _Client:
        def __init__(self, base_url=None):
            self.timeout = None

        def chat(self, messages, model, role, bu_id=None):
            prompts.append(messages[-1]["content"])
            return {"choices": [{"message": {"content": "Done."}}]}

    monkeypatch.setattr(worker_execute, "LLMProxyClient", _Client)
    worker_execute._execute_task(
        {"id": 1, "title": "Do it"},
        {"role": "Developer"},
        "Sprint 1",
        lessons_context="Work sent back for rework:\n- validate the email field",
    )

    assert "## Lessons from Recent Sprints" in prompts[0]
    assert "not verified facts" in prompts[0]
    assert "validate the email field" in prompts[0]


def test_graph_search_shows_retrospectives_to_every_role():
    captured = {}

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, cypher, params):
            captured["cypher"] = cypher
            captured["params"] = params
            return []

    processor = engram_processor.DeepSeekEngramProcessor.__new__(
        engram_processor.DeepSeekEngramProcessor
    )
    processor._driver = SimpleNamespace(session=lambda: _Session())

    processor._query_graph_rag(
        "login form validation", limit=3, role_filter="Developer", company_id=7
    )

    assert (
        "e.agent_role = $role_filter OR e.agent_id STARTS WITH $retrospective_prefix"
        in captured["cypher"]
    )
    assert captured["params"]["company_id"] == 7
    assert (
        captured["params"]["retrospective_prefix"]
        == engram_processor.RETROSPECTIVE_AGENT_PREFIX
    )


def test_an_engram_is_analysed_by_the_model_its_caller_hands_in(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "NEO4J_ENABLED", False)
    prompts = []

    class _Model:
        def invoke(self, messages):
            prompts.append(messages[0].content)
            return SimpleNamespace(content="patterns: none")

    processor = engram_processor.DeepSeekEngramProcessor()
    monkeypatch.setattr(processor, "_store_in_vector_db", lambda *args: None)

    analysed = processor.burn_engram(
        "agent", "ScrumMaster", "Use Postgres", llm=_Model()
    )
    kept = processor.burn_engram("agent", "ScrumMaster", "Use Postgres")

    assert analysed["summary"] == "Use Postgres\n\n[AI Analysis]: patterns: none"
    assert "Decision: Use Postgres" in prompts[0]
    # Without a model the decision is kept as written, and nothing is asked.
    assert kept["summary"] == "Use Postgres"
    assert len(prompts) == 1


def test_the_retrospective_engram_gets_the_engram_processors_model(monkeypatch):
    burned = {}
    roles = []
    model = object()

    class _Processor:
        def burn_engram(self, **kwargs):
            burned.update(kwargs)
            return {"engram_id": "engram-1"}

    def _llm_for_role(role):
        roles.append(role)
        return model

    monkeypatch.setattr(engram_processor, "get_engram_processor", lambda: _Processor())
    monkeypatch.setattr(model_factory, "get_llm_for_role", _llm_for_role)

    worker_close._burn_retrospective_engram(5, {"company_id": 7, "sprint_id": 5})

    assert roles == ["EngramProcessor"]
    assert burned["llm"] is model
    assert burned["company_id"] == 7
    assert burned["agent_id"] == f"{engram_processor.RETROSPECTIVE_AGENT_PREFIX}5"
