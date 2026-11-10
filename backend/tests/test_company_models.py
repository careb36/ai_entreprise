"""A company's models (``services/company_models.py``, ``routers/model_settings.py``;
issue #356, step 4): an agent's own model wins, then the company's model for
its role, then the company's default, then the deployment's ``AGENT_MODEL``
settings; the Reviewer follows QA's choice unless it has its own; a Manager
writes the choice and a Viewer reads it, a connection model must be one of the
company's; the ScrumMaster, the PO, the Architect, the task worker (Developer,
Architect consultation, QA review) and the Reviewer agent all run the
company's choice; a company's choice is never re-routed by difficulty."""

from __future__ import annotations

import pytest
from models import Agent, Company, ProviderConnection
from services import company_models, model_routing
from services.learning import scrum_master
from services.loop.worker import execute as worker_execute
from services.planning import spec_author
from test_git_workflow import _member, enforcement  # noqa: F401
from test_llm_routing import _board, db, llm  # noqa: F401
from test_specifications import _company

from llm.model_factory import AgentRole


@pytest.fixture(autouse=True)
def _deployment(monkeypatch):
    monkeypatch.delenv("NEMOTRON_ROLES", raising=False)
    monkeypatch.delenv("AGENT_MODEL_POLICY", raising=False)
    for role in AgentRole:
        monkeypatch.delenv(f"AGENT_MODEL_{role.name}", raising=False)
    monkeypatch.setenv("AGENT_MODEL", "deployment-model")


def _url(company_id: int) -> str:
    return f"/v1/companies/{company_id}/model_settings"


def _choose(session, company, default=None, roles=None):
    company.default_model = default
    company.role_models = roles
    session.add(company)
    session.commit()
    return company


def test_the_agent_then_the_role_then_the_company_then_the_deployment(sqlite_session):
    company = _company(sqlite_session)
    resolve = company_models.resolve
    assert resolve(sqlite_session, company.id, "QA") == "deployment-model"
    _choose(sqlite_session, company, default="company-default")
    assert resolve(sqlite_session, company.id, "QA") == "company-default"
    _choose(sqlite_session, company, "company-default", {"QA": "qa-model"})
    assert resolve(sqlite_session, company.id, "QA") == "qa-model"
    assert resolve(sqlite_session, company.id, "QA", " agent-model ") == "agent-model"
    # Another role takes the default; an alias reads its role's entry.
    assert resolve(sqlite_session, company.id, "PO") == "company-default"
    _choose(sqlite_session, company, None, {"Developer": "dev-model"})
    assert resolve(sqlite_session, company.id, "Dev") == "dev-model"
    assert resolve(sqlite_session, company.id, "BackendDev") == "dev-model"
    assert resolve(sqlite_session, None, "Dev") == "deployment-model"


def test_the_reviewer_follows_qa_unless_it_has_its_own(sqlite_session):
    company = _choose(sqlite_session, _company(sqlite_session), None, {"QA": "qa"})
    assert company_models.model_for(sqlite_session, company.id, "Reviewer") == "qa"
    _choose(sqlite_session, company, None, {"QA": "qa", "Reviewer": "judge"})
    assert company_models.model_for(sqlite_session, company.id, "Reviewer") == "judge"
    assert company_models.model_for(sqlite_session, company.id, "QA") == "qa"


@pytest.mark.usefixtures("enforcement")
def test_a_manager_sets_the_models_and_a_viewer_reads_them(sqlite_session, test_client):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    body = {"default_model": "deepseek-v4-flash", "role_models": {"QA": "qa-1"}}
    assert (
        test_client.put(_url(company.id), json=body, headers=viewer).status_code == 403
    )
    assert (
        test_client.put(_url(company.id), json=body, headers=outsider).status_code
        == 403
    )
    saved = test_client.put(_url(company.id), json=body, headers=manager)
    assert saved.status_code == 200, saved.text
    data = test_client.get(_url(company.id), headers=viewer).json()
    assert data["default_model"] == "deepseek-v4-flash"
    assert data["role_models"] == {"QA": "qa-1"}
    assert data["effective"]["QA"] == {"model": "qa-1", "source": "role"}
    assert data["effective"]["Reviewer"] == {"model": "qa-1", "source": "role"}
    assert data["effective"]["PO"] == {
        "model": "deepseek-v4-flash",
        "source": "company",
    }
    assert "ScrumMaster" in data["roles"]
    assert test_client.get(_url(company.id), headers=outsider).status_code == 403
    # Empty values clear the choice; the deployment's settings come back.
    cleared = test_client.put(
        _url(company.id),
        json={"default_model": "", "role_models": {"QA": " "}},
        headers=manager,
    ).json()
    assert cleared["default_model"] is None and cleared["role_models"] == {}
    assert cleared["effective"]["QA"] == {
        "model": "deployment-model",
        "source": "deployment",
    }


@pytest.mark.usefixtures("enforcement")
def test_a_choice_the_company_cannot_make_is_refused(sqlite_session, test_client):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    theirs = ProviderConnection(
        company_id=other.id, provider="deepseek", name="d", base_url="https://x/v1"
    )
    ours = ProviderConnection(
        company_id=company.id, provider="deepseek", name="d", base_url="https://x/v1"
    )
    sqlite_session.add_all([theirs, ours])
    sqlite_session.commit()
    cases = [
        ({"role_models": {"Janitor": "m"}}, "unknown role"),
        ({"default_model": "a\nb"}, "not a model id"),
        ({"default_model": "x" * 201}, "not a model id"),
        ({"default_model": "conn:abc"}, "conn:<id>:<model>"),
        ({"default_model": f"conn:{theirs.id}:m"}, "not one of the company's"),
        ({"role_models": {"QA": "conn:99999:m"}}, "not one of the company's"),
    ]
    for body, message in cases:
        response = test_client.put(_url(company.id), json=body, headers=manager)
        assert response.status_code == 422 and message in response.text, body
    good = test_client.put(
        _url(company.id), json={"default_model": f"conn:{ours.id}:m"}, headers=manager
    )
    assert (
        good.status_code == 200 and good.json()["default_model"] == f"conn:{ours.id}:m"
    )


def test_the_deciding_roles_run_the_companys_choice(sqlite_session):
    company = _choose(
        sqlite_session,
        _company(sqlite_session),
        "company-default",
        {"ScrumMaster": "sm-model"},
    )
    assert scrum_master.profile(sqlite_session, company.id)[0] == "sm-model"
    assert spec_author.po_profile(sqlite_session, company.id)[0] == "company-default"
    from services.planning import arch_author

    assert arch_author.architect_profile(sqlite_session, company.id)[0] == (
        "company-default"
    )
    # The agent's own model still wins.
    sqlite_session.add(
        Agent(company_id=company.id, name="SM", role="ScrumMaster", model="own")
    )
    sqlite_session.commit()
    assert scrum_master.profile(sqlite_session, company.id)[0] == "own"


@pytest.mark.parametrize("agent_model", ["", None], ids=["empty-row", "no-row"])
def test_the_worker_gives_a_task_the_companys_model(db, agent_model):  # noqa: F811
    company, _sprint, item = _board(db, role="QA", agent_model=agent_model)
    _choose(db, db.get(Company, company.id), "company-default", {"QA": "qa-model"})
    from services import task_worker

    ctx = task_worker._load_task_context(item.id)
    assert ctx["agent"]["model"] == "qa-model"
    assert (
        model_routing.resolve_agent_model(ctx["agent"], item=ctx["item"]) == "qa-model"
    )


def test_the_agents_own_model_wins_in_the_worker(db):  # noqa: F811
    company, _sprint, item = _board(db, role="QA", agent_model="own-model")
    _choose(db, db.get(Company, company.id), None, {"QA": "qa-model"})
    from services import task_worker

    assert task_worker._load_task_context(item.id)["agent"]["model"] == "own-model"


def test_the_architect_consultation_runs_the_companys_model(llm, db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(worker_execute, "record_agent_turn", lambda **kw: None)
    llm.response = {"choices": [{"message": {"content": "Use a service layer."}}]}
    company, sprint, item = _board(db, role="Architect", agent_model="")
    _choose(db, db.get(Company, company.id), None, {"Architect": "arch-model"})
    worker_execute._consult_architect(
        {"id": item.id, "title": item.title},
        {"role": "Developer"},
        {"id": sprint.id, "company_id": company.id, "name": "Routing"},
    )
    assert llm.calls[0]["model"] == "arch-model"


def test_the_reviewer_agent_asks_with_the_companys_reviewer_model(
    sqlite_session, monkeypatch
):
    from services import reviewer_agent
    from test_reviewer_agent import APPROVE, _pending_only, _run
    from test_reviewer_agent import _company as reviewer_company

    company = reviewer_company(sqlite_session)
    _choose(sqlite_session, company, None, {"Reviewer": "judge-model"})
    run = _run(sqlite_session, company)
    _pending_only(sqlite_session, {run.id})
    asked = []

    def fake_ask(prompt, model=None, company_id=None):
        asked.append((model, company_id))
        return APPROVE

    monkeypatch.setattr(reviewer_agent, "ask_llm", fake_ask)
    reviewer_agent.review_cycle(sqlite_session.get_bind())
    # The company travels with the model, so a connection model routes with its key.
    assert asked == [("judge-model", company.id)]


@pytest.mark.usefixtures("enforcement")
def test_an_agent_runs_only_a_connection_of_its_company(sqlite_session, test_client):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    ours, theirs = (
        ProviderConnection(
            company_id=owner.id, provider="deepseek", name="d", base_url="https://x/v1"
        )
        for owner in (company, other)
    )
    sqlite_session.add_all([ours, theirs])
    sqlite_session.commit()
    body = {"company_id": company.id, "name": "Dev", "role": "Developer"}
    refused = test_client.post(
        "/v1/agents", json={**body, "model": f"conn:{theirs.id}:m"}, headers=manager
    )
    assert refused.status_code == 422 and "not one of the company's" in refused.text
    hired = test_client.post(
        "/v1/agents", json={**body, "model": f"conn:{ours.id}:m"}, headers=manager
    )
    assert hired.status_code == 200, hired.text
    agent_id = hired.json()["id"]
    patch = test_client.patch(
        f"/v1/agents/{agent_id}",
        json={"model": f"conn:{theirs.id}:m"},
        headers=manager,
    )
    assert patch.status_code == 422 and "not one of the company's" in patch.text
    assert sqlite_session.get(Agent, agent_id).model == f"conn:{ours.id}:m"
    # A plain id and the role's default are still accepted.
    for model in ("deepseek-v4-flash", ""):
        response = test_client.patch(
            f"/v1/agents/{agent_id}", json={"model": model}, headers=manager
        )
        assert response.status_code == 200 and response.json()["model"] == model


def test_the_planners_call_the_proxy_with_the_pos_company(sqlite_session):
    """An agent's own model may be a connection; its company routes it."""
    from services.planning import spec_planning, sprint_planner

    company = _company(sqlite_session)
    sqlite_session.add(
        Agent(company_id=company.id, name="PO", role="PO", model="conn:1:m")
    )
    sqlite_session.commit()
    _, agents, _ = spec_planning.planning_context_of(sqlite_session, company.id)
    po = sprint_planner._find_po_agent(agents)
    assert po["model"] == "conn:1:m"
    assert sprint_planner._client(po).company_id == company.id
