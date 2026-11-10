"""The role catalogue (``services/roles.py``): the new roles registered in every
place that lists roles, and the specialised developers on the Developer's path.

BackendDeveloper, FrontendDeveloper, DevOps, Designer and DataEngineer join
PO, Architect, ScrumMaster, Developer, QA, SecOps and TechWriter. A coding
role delivers files and is verified exactly as a Developer, through the real
daemon; ``Developer`` keeps working for existing companies, and the older
spellings (``Dev``, ``BackendDev``, ``Data``) read as aliases.
"""

import asyncio

import pytest
from config import settings
from models import Company
from services.contracts import roles
from services.evidence import standards
from services.loop import edit_plan
from services.loop.worker import deliver as worker_deliver
from services.models import company_models, model_suggestions
from services.platform.orchestration_metrics import (
    canonical_kpi_role,
    should_consult_architect,
)
from services.tools import developer_tools, research_tools
from test_task_delivery import (  # noqa: F401 - the db fixture is used by name.
    TWO_FILE_ANSWER,
    _DaemonOverNATS,
    _latest,
    _load_daemon,
    _task_fixture,
    db,
)

from llm.model_factory import AgentRole, ModelFactory, default_model_for_role

CODING = ("BackendDeveloper", "FrontendDeveloper", "DevOps", "DataEngineer")


def test_the_catalogue_is_the_model_factorys_roles():
    assert roles.ROLES == tuple(
        r.value for r in AgentRole if r is not AgentRole.ENGRAM_PROCESSOR
    )
    assert roles.NEW_ROLES == (*CODING[:3], "Designer", "DataEngineer")
    assert set(roles.DEVELOPER_ROLES) == {"Developer", *CODING}
    for role in AgentRole:
        assert role in ModelFactory.ROLE_MODEL_MAP


def test_every_new_role_has_a_default_model_and_the_coding_ones_mirror_the_developer(
    monkeypatch,
):
    for name in ("AGENT_MODEL", "AGENT_MODEL_DEVELOPER", "NEMOTRON_ROLES"):
        monkeypatch.delenv(name, raising=False)
    developer = default_model_for_role("Developer")
    for role in CODING:
        assert default_model_for_role(role) == developer
    assert default_model_for_role("Designer") == default_model_for_role("Architect")
    # A deployment that set the Developer's model keeps it for its coding roles.
    monkeypatch.setenv("AGENT_MODEL_DEVELOPER", "dev-model")
    assert default_model_for_role("BackendDev") == "dev-model"
    monkeypatch.setenv("AGENT_MODEL_DEVOPS", "ops-model")
    assert default_model_for_role("DevOps") == "ops-model"
    assert default_model_for_role("Designer") != "dev-model"


def test_aliases_read_as_catalogue_roles_and_unknown_ones_are_left_alone():
    assert roles.canonical("Dev") == "Developer"
    assert roles.canonical("backend-dev") == "BackendDeveloper"
    assert roles.canonical("FrontendDev") == "FrontendDeveloper"
    assert roles.canonical("Data") == "DataEngineer"
    assert roles.canonical("SM") == "ScrumMaster"
    assert roles.canonical("UXResearcher") == "UXResearcher"
    assert roles.is_developer("devops") and not roles.is_developer("Designer")
    assert not roles.is_developer("TechWriter")


def test_the_company_model_lists_take_every_role_and_coding_roles_follow_the_developer():
    assert set(roles.ROLES) <= set(company_models.ROLES)
    grouped = (
        set(model_suggestions.DECIDING_ROLES)
        | set(model_suggestions.CODE_ROLES)
        | set(model_suggestions.WRITING_ROLES)
    )
    assert grouped == set(company_models.ROLES)
    assert set(CODING) <= set(model_suggestions.CODE_ROLES)

    company = Company(company_name="Models", role_models={"Developer": "dev-choice"})

    for role in (*CODING, "BackendDev"):
        assert company_models.company_choice(company, role) == "dev-choice"
    assert company_models.company_choice(company, "Designer") is None


def test_the_standards_bind_each_discipline_without_relaxing_any():
    developer = {s["id"] for s in standards.for_role("Developer")}
    for role in CODING:
        assert developer <= {s["id"] for s in standards.for_role(role)}
    assert "STD-DATA-1" in {s["id"] for s in standards.for_role("DataEngineer")}
    assert "STD-DATA-1" in {s["id"] for s in standards.for_role("BackendDeveloper")}
    assert "STD-OPS-2" in {s["id"] for s in standards.for_role("DevOps")}
    designer = {s["id"] for s in standards.for_role("Designer")}
    assert {"STD-UX-1", "STD-SEC-1", "STD-AUD-1"} <= designer
    assert "STD-DEV-1" not in designer
    assert "STD-UX-1" in {s["id"] for s in standards.for_role("FrontendDeveloper")}
    assert "STD-UX-1" in standards.prompt_section("Designer")


def test_the_file_roles_get_the_workspace_tools_and_the_designer_researches(
    monkeypatch,
):
    monkeypatch.setattr(settings, "DEVELOPER_TOOLS_ENABLED", True)
    for role in (*CODING, "Developer", "Dev", "QA", "Designer", "BackendDev"):
        assert developer_tools.offered_to(role), role
    for role in ("PO", "TechWriter", "ScrumMaster"):
        assert not developer_tools.offered_to(role)
    default = type(settings).model_fields["RESEARCH_ROLES"].default
    assert default.split(",") == list(roles.RESEARCH_ROLES)
    monkeypatch.setattr(settings, "RESEARCH_ROLES", default)
    assert "designer" in research_tools.research_roles()


@pytest.mark.parametrize("role", ["Developer", *CODING])
def test_a_coding_role_takes_the_developers_path(role, monkeypatch):
    item = {"title": "Refactor the API contract", "priority": "high", "comments": []}
    assert should_consult_architect(item, {"role": role})
    assert canonical_kpi_role(role) == "Developer"
    monkeypatch.setattr(edit_plan, "applies", lambda item: True)
    assert edit_plan.wanted(item, {"role": role})
    assert not should_consult_architect(item, {"role": "Designer"})


@pytest.mark.parametrize("role", ["Developer", *CODING])
def test_a_coding_agent_delivers_files_verified_exactly_like_the_developer(
    db,  # noqa: F811 - the fixture imported above.
    tmp_path,
    monkeypatch,
    role,
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    project = tmp_path / "project"
    project.mkdir()
    item, agent, sprint = _task_fixture(db, str(project))
    agent = {**agent, "role": role}
    nats = _DaemonOverNATS(_load_daemon())

    asyncio.run(
        worker_deliver._deliver_artifacts(nats, item, agent, sprint, TWO_FILE_ANSWER)
    )

    assert (project / "src" / "auth" / "login.py").read_text().startswith("def login(")
    run, verification, comments = _latest(db, item["id"])
    assert run.claims["files"] == ["src/auth/login.py", "tests/test_login.py"]
    assert verification.verdict == "verified"
    outcomes = {c["name"]: c["outcome"] for c in verification.checks}
    assert outcomes["files_written"] == "pass"
    assert comments[-1]["text"].startswith(f"Delivered to {project}")
