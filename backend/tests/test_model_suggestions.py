"""The model suggested for each role (``services/model_suggestions.py``,
``GET /v1/companies/{id}/model_suggestions``; issue #356, step 6): a
deterministic rule over the catalogue's real fields; the roles that decide get
a model that reasons, the coding roles the largest context with tools, the
TechWriter the cheapest; a model without tools is never suggested, nor one the
provider did not list, nor any from a connection not validated, failed, or
that listed nothing; each suggestion is the connection's own
reference; a Viewer reads them, nobody outside the company does."""

from __future__ import annotations

import pytest
from models import ProviderConnection
from services.models import model_catalog, model_suggestions
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import _company

CATALOG = [
    {
        "id": "big-thinker",
        "name": "Big",
        "tool_call": True,
        "reasoning": True,
        "context": 1_000_000,
        "cost_input": 3.0,
    },
    {
        "id": "coder",
        "name": "Coder",
        "tool_call": True,
        "context": 2_000_000,
        "cost_input": 1.0,
    },
    {
        "id": "cheap",
        "name": "Cheap",
        "tool_call": True,
        "context": 64_000,
        "cost_input": 0.1,
    },
    {
        "id": "no-tools",
        "name": "Chat",
        "reasoning": True,
        "context": 9_000_000,
        "cost_input": 0.01,
    },
]


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(
        model_catalog, "models_for", lambda provider: [dict(m) for m in CATALOG]
    )


def _connection(session, company, name="acme", listed=None, ok=None):
    row = ProviderConnection(
        company_id=company.id,
        provider="openrouter",
        name=name,
        base_url="https://openrouter.example/api/v1",
        listed_models=listed,
        validation_ok=ok,
    )
    session.add(row)
    session.commit()
    return row


LISTED = ["big-thinker", "coder", "cheap", "no-tools"]


def test_each_role_gets_the_rule_s_model(sqlite_session, catalog):
    company = _company(sqlite_session)
    conn = _connection(sqlite_session, company, listed=LISTED, ok=True)
    models = model_suggestions.candidates([conn])
    assert {m["id"] for m in models} == {"big-thinker", "coder", "cheap"}
    suggestions = model_suggestions.suggest(models)
    assert suggestions["Architect"]["model"] == f"conn:{conn.id}:big-thinker"
    assert suggestions["ScrumMaster"]["model"] == f"conn:{conn.id}:big-thinker"
    assert suggestions["Developer"]["model"] == f"conn:{conn.id}:coder"
    assert suggestions["Reviewer"]["model"] == f"conn:{conn.id}:coder"
    assert suggestions["TechWriter"]["model"] == f"conn:{conn.id}:cheap"
    assert suggestions["Developer"]["connection"] == "acme"
    assert "tools" in suggestions["Developer"]["reason"]


def test_a_validated_provider_only_suggests_what_it_listed(sqlite_session, catalog):
    company = _company(sqlite_session)
    conn = _connection(sqlite_session, company, listed=["cheap", "no-tools"], ok=True)
    suggestions = model_suggestions.suggest(model_suggestions.candidates([conn]))
    assert suggestions["Developer"]["model"] == f"conn:{conn.id}:cheap"
    # Nothing listed reasons and calls tools: no suggestion for those roles.
    assert suggestions["PO"] is None


@pytest.mark.parametrize(
    ("listed", "ok"),
    [(None, None), (LISTED, False), ([], True)],
    ids=["not-validated", "failed", "listed-nothing"],
)
def test_an_unconfirmed_connection_suggests_nothing(
    sqlite_session, catalog, listed, ok
):
    company = _company(sqlite_session)
    conn = _connection(sqlite_session, company, listed=listed, ok=ok)
    assert model_suggestions.candidates([conn]) == []


def test_no_model_with_tools_means_no_suggestion():
    assert set(model_suggestions.suggest([]).values()) == {None}


def test_an_unpriced_model_is_not_taken_for_free():
    models = [
        {"id": "x", "ref": "conn:1:x", "connection": "c", "tool_call": True},
        {
            "id": "y",
            "ref": "conn:1:y",
            "connection": "c",
            "tool_call": True,
            "cost_input": 2.0,
        },
    ]
    assert model_suggestions.suggest(models)["TechWriter"]["model"] == "conn:1:y"


@pytest.mark.usefixtures("enforcement")
def test_a_viewer_reads_the_suggestions(sqlite_session, test_client, catalog):
    company, other = _company(sqlite_session), _company(sqlite_session)
    conn = _connection(sqlite_session, company, listed=LISTED, ok=True)
    _connection(
        sqlite_session, company, name="off", listed=LISTED, ok=True
    ).enabled = False
    sqlite_session.commit()
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/model_suggestions"
    assert test_client.get(url, headers=outsider).status_code == 403
    response = test_client.get(url, headers=viewer)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["candidates"] == 3
    assert data["suggestions"]["QA"]["model"] == f"conn:{conn.id}:coder"


REWORK = {"author": "QA", "text": "NEEDS WORK: cover the empty case"}


def _delivered(session, company, sprint_status, model, outcomes, role="Developer"):
    """A sprint whose tasks the task worker delivered with *model*."""
    from models import BacklogItem, Sprint
    from models.agent_run import TASK_WORKER_PRODUCER, AgentRun

    sprint = Sprint(company_id=company.id, sprint_name="S", status=sprint_status)
    session.add(sprint)
    session.commit()
    for status, rework in outcomes:
        item = BacklogItem(
            sprint_id=sprint.id, title="T", status=status, comments=[REWORK] * rework
        )
        session.add(item)
        session.commit()
        session.add(
            AgentRun(
                company_id=company.id,
                external_ref=f"task-{item.id}",
                producer=TASK_WORKER_PRODUCER,
                agent_role=role,
                model=model,
                artifact_digest="x",
                artifact_size=1,
            )
        )
    session.commit()


def test_the_companys_record_decides_once_it_has_one(sqlite_session, catalog):
    """``model_record``: counted from completed sprints, by the last delivery."""
    from services.models import model_record

    company = _company(sqlite_session)
    conn = _connection(sqlite_session, company, listed=LISTED, ok=True)
    models = model_suggestions.candidates([conn])
    # "coder" leads the rule for the Developer; the company's tasks say "cheap"
    # reworks less. Two tasks are not enough; an open sprint does not count.
    _delivered(sqlite_session, company, "completed", "coder", [("done", 2)] * 3)
    _delivered(sqlite_session, company, "completed", "cheap", [("done", 0)] * 2)
    _delivered(sqlite_session, company, "active", "cheap", [("done", 0)] * 5)
    record = model_record.record(sqlite_session, company.id)
    assert record[("Developer", "coder")] == {"tasks": 3, "done": 3, "rework": 6}
    assert record[("Developer", "cheap")]["tasks"] == 2
    first = model_suggestions.suggest(models, record)["Developer"]
    assert first["model"] == f"conn:{conn.id}:coder"
    assert first["reason"] == (
        "on 3 of the company's Developer tasks: 3 done, 2.0 rework per task"
    )
    _delivered(sqlite_session, company, "completed", "cheap", [("done", 1)])
    record = model_record.record(sqlite_session, company.id)
    suggestions = model_suggestions.suggest(models, record)
    assert suggestions["Developer"]["model"] == f"conn:{conn.id}:cheap"
    assert suggestions["Developer"]["measured"] == {
        "tasks": 3,
        "done": 3,
        "rework": 1,
    }
    # Other roles have no record: the rule stays.
    assert suggestions["QA"]["model"] == f"conn:{conn.id}:coder"
    assert "tools" in suggestions["QA"]["reason"]


def test_a_record_of_mostly_blocked_tasks_never_outranks_the_rule(
    sqlite_session, catalog
):
    from services.models import model_record

    company = _company(sqlite_session)
    conn = _connection(sqlite_session, company, listed=LISTED, ok=True)
    _delivered(
        sqlite_session,
        company,
        "completed",
        "cheap",
        [("blocked", 0), ("blocked", 0), ("done", 0)],
    )
    record = model_record.record(sqlite_session, company.id)
    developer = model_suggestions.suggest(model_suggestions.candidates([conn]), record)[
        "Developer"
    ]
    assert developer["model"] == f"conn:{conn.id}:coder"


def test_a_task_counts_for_the_model_of_its_last_delivery(sqlite_session):
    from models import BacklogItem, Sprint
    from models.agent_run import TASK_WORKER_PRODUCER, AgentRun
    from services.models import model_record

    company = _company(sqlite_session)
    sprint = Sprint(company_id=company.id, sprint_name="S", status="completed")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="T", status="done")
    sqlite_session.add(item)
    sqlite_session.commit()
    for version, model in enumerate(("first", "second"), start=1):
        sqlite_session.add(
            AgentRun(
                company_id=company.id,
                external_ref=f"task-{item.id}",
                version=version,
                producer=TASK_WORKER_PRODUCER,
                agent_role="Dev",
                model=model,
                artifact_digest="x",
                artifact_size=1,
            )
        )
    sqlite_session.commit()
    assert model_record.record(sqlite_session, company.id) == {
        ("Developer", "second"): {"tasks": 1, "done": 1, "rework": 0}
    }
