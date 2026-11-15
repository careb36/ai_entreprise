"""The weight of each prompt section, read back from the runs (#443, "Show").

The task worker keeps its measure of every prompt on the run's evidence
(``prompt_sections``); ``services/models/prompt_weight.py`` adds it up for a
run, a task and a sprint, and the cost routes carry it as ``prompt_weight``.
"""

import secrets
from datetime import datetime

import auth_tokens
import pytest
from config import settings
from models import BacklogItem, Company, CompanyMember, Sprint, User
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from services.models import prompt_weight


def _measure(sections, system_tokens=100, cut=()):
    return {
        "budget": None,
        "tokens": sum(t for _, t in sections if _ not in cut),
        "system_tokens": system_tokens,
        "sections": [
            {"name": n, "chars": t * 4, "tokens": t, "priority": 1, "cut": n in cut}
            for n, t in sections
        ],
        "cut": list(cut),
    }


def test_summarize_adds_sections_up_heaviest_first_and_counts_the_cuts():
    one = _measure([("task", 50), ("repository", 300), ("lessons", 50)])
    two = _measure(
        [("task", 70), ("repository", 500), ("lessons", 80)], cut=("lessons",)
    )
    weight = prompt_weight.summarize([one, None, two])
    assert weight["calls"] == 2
    assert weight["system_tokens"] == 200
    assert [s["name"] for s in weight["sections"]] == ["repository", "task", "lessons"]
    repo, _task, lessons = weight["sections"]
    assert (repo["tokens"], repo["chars"], repo["calls"], repo["cut"]) == (
        800,
        3200,
        2,
        0,
    )
    assert (lessons["tokens"], lessons["calls"], lessons["cut"]) == (50, 1, 1)
    assert repo["share"] == pytest.approx(800 / 970, abs=1e-4)
    assert sum(s["share"] for s in weight["sections"]) == pytest.approx(1, abs=1e-3)
    assert weight["estimate"] == "chars/4"


def test_a_run_without_a_measure_weighs_nothing():
    empty = prompt_weight.summarize([])
    assert (empty["calls"], empty["sections"], empty["tokens"]) == (0, [], 0)
    for evidence in (None, {}, {"prompt_sections": "x"}, {"prompt_sections": {}}):
        assert prompt_weight.summarize([prompt_weight._measure(evidence)])["calls"] == 0


@pytest.fixture
def signing_key(monkeypatch):
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    return key


def _headers(session, company_id):
    user = User(
        email=f"weight-{secrets.token_hex(8)}@example.com",
        full_name="Weight reader",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    session.add(
        CompanyMember(company_id=company_id, user_id=user.id, human_role="Viewer")
    )
    session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def _run(session, company_id, task_id, version, measure, producer=TASK_WORKER_PRODUCER):
    run = AgentRun(
        company_id=company_id,
        external_ref=f"task-{task_id}",
        version=version,
        producer=producer,
        artifact_digest="0" * 64,
        artifact_size=1,
        ingested_at=datetime(2026, 11, 15, 12, version),
    )
    session.add(run)
    session.commit()
    # An older verification without the measure, then the latest with it:
    # only the latest counts.
    for evidence in ({}, {"prompt_sections": measure} if measure else {}):
        session.add(
            Verification(
                company_id=company_id,
                agent_run_id=run.id,
                verdict="verified",
                evidence=evidence,
            )
        )
        session.commit()
    return run


def test_the_cost_routes_carry_the_weight_of_their_runs_tasks_and_sprint(
    test_client, sqlite_session, signing_key
):
    s = sqlite_session
    company = Company(company_name="Weighed")
    other = Company(company_name="Weighed elsewhere")
    s.add_all([company, other])
    s.commit()
    sprint = Sprint(company_id=company.id, sprint_name="Heavy")
    s.add(sprint)
    s.commit()
    one = BacklogItem(sprint_id=sprint.id, title="One")
    two = BacklogItem(sprint_id=sprint.id, title="Two")
    s.add_all([one, two])
    s.commit()
    first = _run(s, company.id, one.id, 1, _measure([("task", 10), ("repository", 90)]))
    _run(s, company.id, one.id, 2, _measure([("task", 10), ("repository", 30)]))
    _run(s, company.id, two.id, 1, _measure([("task", 20)]))
    _run(s, company.id, two.id, 2, None)  # a run from before the measure
    # Another tenant's run naming the same task and another producer's never count.
    _run(s, other.id, one.id, 1, _measure([("repository", 10**6)]))
    _run(s, company.id, one.id, 3, _measure([("repository", 10**6)]), producer="other")
    headers = _headers(s, company.id)

    run = test_client.get(
        f"/v1/agent_runs/{first.id}/cost",
        params={"company_id": company.id},
        headers=headers,
    )
    assert run.status_code == 200, run.text
    assert run.json()["prompt_weight"]["calls"] == 1
    assert run.json()["prompt_weight"]["sections"][0] == {
        "name": "repository",
        "chars": 360,
        "tokens": 90,
        "calls": 1,
        "cut": 0,
        "share": 0.9,
    }
    task = test_client.get(f"/v1/tasks/{one.id}/cost", headers=headers).json()
    assert task["prompt_weight"]["calls"] == 2
    assert {s["name"]: s["tokens"] for s in task["prompt_weight"]["sections"]} == {
        "repository": 120,
        "task": 20,
    }
    assert "cost_cents" in task  # the cost is still there, beside the weight
    sprint_body = test_client.get(
        f"/v1/sprints/{sprint.id}/cost", headers=headers
    ).json()
    weight = sprint_body["prompt_weight"]
    assert weight["calls"] == 3
    assert {s["name"]: s["tokens"] for s in weight["sections"]} == {
        "repository": 120,
        "task": 40,
    }
