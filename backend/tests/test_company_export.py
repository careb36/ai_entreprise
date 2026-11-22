"""A company's configuration as one file, without its secrets
(``services/management/company_export.py``, from Paperclip's company
portability export). The fake secrets are built at run time so no scanner
mistakes this file for a leak."""

import json

import pytest
import test_agent_access
from models import Agent, Company, ProjectCredential, Routine
from models.audit import AuditLog
from models.provider import ProviderConnection
from services.management import company_export
from sqlmodel import select

company = test_agent_access.company
enforcement = test_agent_access.enforcement

FAKE_KEY = "sk-" + "Zq7" * 9
FAKE_GITHUB = "ghp_" + "x1Y2" * 10


@pytest.fixture(autouse=True)
def audit_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


@pytest.fixture
def furnished(sqlite_session, company):
    cid = company["id"]
    stored = sqlite_session.get(Company, cid)
    stored.sandbox_env = ["DATABASE_URL"]
    stored.limits = {"max_reworks": 2}
    agent = sqlite_session.get(Agent, company["agent"])
    agent.system_prompt = (
        f"Be careful.\nUse the key {FAKE_KEY} for the API.\nShip tests."
    )
    agent.persona = "Calm"
    sqlite_session.add_all(
        [
            stored,
            agent,
            ProviderConnection(
                company_id=cid,
                provider="openai_compatible",
                name="deepseek",
                base_url="https://user:hunter2pass@llm.example.com/v1",
                api_key="encrypted-ciphertext",
                headers='{"X-Org": "1"}',
            ),
            ProjectCredential(
                company_id=cid,
                credential_type="github_token",
                access_level="write",
                encrypted_value=FAKE_GITHUB,
            ),
            Routine(
                company_id=cid,
                title="Audit",
                description=f'token = "{FAKE_GITHUB}"',
                assignee="Developer",
                schedule={"every": "day", "at": "09:00", "timezone": "UTC"},
            ),
        ]
    )
    sqlite_session.commit()
    return company


def _export(test_client, company, headers):
    return test_client.get(f"/v1/companies/{company['id']}/export", headers=headers)


def test_only_an_owner_exports(test_client, furnished):
    assert _export(test_client, furnished, furnished["manager"]).status_code == 403
    assert _export(test_client, furnished, furnished["viewer"]).status_code == 403
    assert _export(test_client, furnished, furnished["outsider"]).status_code == 403


def test_no_secret_leaves_and_each_is_named(sqlite_session, furnished):
    body = company_export.export(sqlite_session, furnished["id"], {"by": "owner@x"})

    text = json.dumps(body)
    for secret in (
        FAKE_KEY,
        FAKE_GITHUB,
        "encrypted-ciphertext",
        "hunter2pass",
        "X-Org",
    ):
        assert secret not in text
    content = body["content"]
    [agent] = content["agents"]
    assert agent["system_prompt"].split("\n") == [
        "Be careful.",
        company_export.REDACTED_LINE,
        "Ship tests.",
    ]
    assert (
        content["provider_connections"][0]["base_url"] == "https://llm.example.com/v1"
    )
    kinds = sorted(r["kind"] for r in body["required_secrets"])
    assert kinds == [
        "environment_variable",
        "project_credential",
        "provider_api_key",
        "provider_headers",
    ]
    places = {r["at"] for r in body["redacted"]}
    assert "$.agents[0].system_prompt" in places
    assert "$.routines[0].description" in places
    assert "$.provider_connections[0].base_url" in places


def test_the_export_holds_the_configuration_and_no_people(sqlite_session, furnished):
    content = company_export.export(sqlite_session, furnished["id"], None)["content"]

    assert content["company"]["limits"] == {"max_reworks": 2}
    assert content["company"]["sandbox_env"] == ["DATABASE_URL"]
    assert "dependency_installs" not in content["company"]
    assert content["agents"][0]["persona"] == "Calm"
    assert content["routines"][0]["schedule"]["at"] == "09:00"
    assert "@example.com" not in json.dumps(content)


def test_the_digest_is_the_backends_and_is_audited(
    test_client, sqlite_session, furnished
):
    owner = test_agent_access._member(
        sqlite_session, sqlite_session.get(Company, furnished["id"]), "Owner"
    )

    body = _export(test_client, furnished, owner).json()

    assert body["manifest"]["sha256"] == company_export.digest(body["content"])
    [row] = sqlite_session.exec(
        select(AuditLog).where(AuditLog.action == company_export.AUDIT_ACTION)
    ).all()
    assert row.result["sha256"] == body["manifest"]["sha256"]
    assert row.result["redacted"] == len(body["redacted"]) and row.chain_hash


def test_an_unknown_company_is_refused(sqlite_session):
    with pytest.raises(ValueError):
        company_export.export(sqlite_session, 987654, None)
