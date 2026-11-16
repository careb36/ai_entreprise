"""What the tests of a posted run's verification share: a company, an
AgentRun payload, and the two routes that ingest and verify it. The tests are
``test_verification*.py``.
"""

from models.company import Company


def _make_company(session, name="Verification Test"):
    company = Company(company_name=name)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company.id


def _payload(company_id, **overrides):
    payload = {
        "company_id": company_id,
        "external_ref": "github:careb36/le-fabrique#41",
        "producer": "copilot",
        "artifact": "diff contents",
        "claims": {"summary": "opened a PR"},
    }
    payload.update(overrides)
    return payload


def _ingest(test_client, monkeypatch, tmp_path, payload):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))
    return test_client.post("/v1/agent_runs", json=payload)


def _verify(test_client, run_id, company_id, evidence=None):
    return test_client.post(
        f"/v1/agent_runs/{run_id}/verify",
        json={"company_id": company_id, "evidence": evidence},
    )
