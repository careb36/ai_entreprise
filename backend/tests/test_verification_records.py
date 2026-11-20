"""What a verification leaves behind: a re-verification appends to the history
(oldest first) and never rewrites an earlier row, no model is called, one
linked audit row is written and the run's status updated, and another
company's run is a 404 that writes nothing. The shared rows are in
``verification_helpers``.
"""

from models.agent_run import AgentRun, Verification
from models.audit import AuditLog
from sqlmodel import select
from verification_helpers import _ingest, _make_company, _payload, _verify


def test_reverification_appends_history_and_returns_oldest_first(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, claims={"summary": "75 tests passed"}),
    ).json()

    first_response = _verify(test_client, run["id"], company_id)
    first_id = first_response.json()["id"]
    first_before = sqlite_session.get(Verification, first_id)
    first_columns = {
        column.name: getattr(first_before, column.name)
        for column in Verification.__table__.columns
    }
    second_response = _verify(
        test_client,
        run["id"],
        company_id,
        {
            "ci": {
                "conclusion": "success",
                "source": "github-check-run",
                "ref": "run/125",
            }
        },
    )
    history_response = test_client.get(
        f"/v1/agent_runs/{run['id']}/verifications",
        params={"company_id": company_id},
    )
    verification_audits = [
        audit
        for audit in sqlite_session.exec(
            select(AuditLog).where(AuditLog.action == "agent_run_verified")
        ).all()
        if audit.details.get("company_id") == str(company_id)
    ]

    assert first_response.json()["verdict"] == "unverifiable"
    assert second_response.json()["verdict"] == "verified"
    assert len(verification_audits) == 2
    assert sqlite_session.get(Verification, second_response.json()["id"]).evidence == {
        "ci": {"conclusion": "success", "source": "github-check-run", "ref": "run/125"},
        # The policy the verdict was computed under (issue #766).
        "verdict_policy": {"version": "v1", "required": []},
    }
    assert {
        column.name: getattr(sqlite_session.get(Verification, first_id), column.name)
        for column in Verification.__table__.columns
    } == first_columns
    assert [item["id"] for item in history_response.json()["verifications"]] == [
        first_id,
        second_response.json()["id"],
    ]


def test_verification_does_not_call_llm(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    import llm_proxy_client

    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, claims={"summary": "backend/app/crypto.py"}),
    ).json()

    def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(llm_proxy_client.LLMProxyClient, "chat", unexpected_llm_call)
    monkeypatch.setattr(
        llm_proxy_client.LLMProxyClient, "chat_async", unexpected_llm_call
    )
    response = _verify(test_client, run["id"], company_id)

    assert response.status_code == 201


def test_verification_writes_one_linked_audit_and_updates_run_status(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, claims={"summary": "backend/app/crypto.py"}),
    ).json()

    response = _verify(test_client, run["id"], company_id)
    verification = sqlite_session.get(Verification, response.json()["id"])
    audits = [
        audit
        for audit in sqlite_session.exec(
            select(AuditLog).where(AuditLog.action == "agent_run_verified")
        ).all()
        if audit.details.get("company_id") == str(company_id)
    ]
    stored_run = sqlite_session.get(AgentRun, run["id"])
    assert response.status_code == 201
    assert len(audits) == 1
    assert audits[0].details["company_id"] == str(company_id)
    assert verification.audit_log_id == audits[0].id
    assert stored_run.status == response.json()["verdict"]


def test_cross_tenant_verification_returns_404_without_writes(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_a = _make_company(sqlite_session, name="Company A")
    company_b = _make_company(sqlite_session, name="Company B")
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_b, claims={"summary": "work"}),
    ).json()

    response = _verify(test_client, run["id"], company_a)

    assert response.status_code == 404
    assert (
        sqlite_session.exec(
            select(Verification).where(Verification.agent_run_id == run["id"])
        ).all()
        == []
    )
    assert [
        audit
        for audit in sqlite_session.exec(
            select(AuditLog).where(AuditLog.action == "agent_run_verified")
        ).all()
        if audit.details.get("company_id") == str(company_a)
    ] == []
