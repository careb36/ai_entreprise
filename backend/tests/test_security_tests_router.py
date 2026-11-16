"""The security tests routes (issue #539, ``routers/security_tests.py``): a
Manager records a person's review of one WSTG test, a Viewer reads the plan
and its statuses, the review's digest is the backend's, and the pack's
snapshot reads the Verifier's WSTG items with the run's artifact digest.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from factories import company as _company
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models import AuditLog, Company, WstgReview
from models.agent_run import AgentRun, Verification
from services.planning.project_docs import collect
from test_documentation_router import _records


def _body(**overrides):
    return {
        "wstg_id": "ATHN-02",
        "result": "pass",
        "reviewer_name": "A. Reviewer",
        "reviewed_on": "2026-11-10",
        "artifact": "reports/pentest-2026-11.pdf",
        **overrides,
    }


@pytest.mark.usefixtures("enforcement")
def test_a_manager_records_a_review_and_a_viewer_reads_its_status(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    _records(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Owner")
    url = f"/v1/companies/{company.id}/security/wstg/reviews"

    assert test_client.post(url, json=_body(), headers=viewer).status_code == 403
    assert test_client.post(url, json=_body(), headers=outsider).status_code == 403
    created = test_client.post(
        url,
        json=_body(wstg_id="INFO-02", record_sha256="f" * 64),
        headers=manager,
    )
    assert created.status_code == 201, created.text
    review = created.json()
    assert review["wstg_id"] == "WSTG-v42-INFO-02"
    assert review["reviewer_role"] == "Manager"
    # The digest is the backend's, never the request's.
    assert review["record_sha256"] != "f" * 64
    assert len(review["record_sha256"]) == 64
    audit = sqlite_session.query(AuditLog).filter_by(company_id=company.id).all()
    assert any(a.action == "wstg_review_recorded" for a in audit)

    plan = test_client.get(f"/v1/companies/{company.id}/security/wstg", headers=viewer)
    assert plan.status_code == 200, plan.text
    data = plan.json()
    assert data["catalogue"]["licence"] == "CC BY-SA 4.0"
    row = next(r for r in data["rows"] if r["id"] == "WSTG-v42-INFO-02")
    assert row["status"] == "observed-pass"
    assert row["evidence"]["review_id"] == review["id"]
    assert sum(data["counts"].values()) == len(data["rows"]) == 117
    listed = test_client.get(url, headers=viewer).json()["reviews"]
    assert [r["id"] for r in listed] == [review["id"]]
    assert (
        test_client.get(
            f"/v1/companies/{company.id}/security/wstg", headers=outsider
        ).status_code
        == 403
    )


@pytest.mark.usefixtures("enforcement")
def test_a_review_is_refused_for_an_unknown_test_a_future_date_or_no_reason(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    url = f"/v1/companies/{company.id}/security/wstg/reviews"
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    for body in (
        _body(wstg_id="ZZZZ-01"),
        _body(reviewed_on=tomorrow),
        _body(result="not_applicable"),
        _body(result="maybe"),
        _body(artifact=""),
    ):
        response = test_client.post(url, json=body, headers=manager)
        assert response.status_code == 422, (body, response.text)
    ok = test_client.post(
        url,
        json=_body(result="not_applicable", note="internal\nonly"),
        headers=manager,
    )
    assert ok.status_code == 201 and ok.json()["note"] == "internal only"
    assert (
        sqlite_session.query(WstgReview).filter_by(company_id=company.id).count() == 1
    )


def test_the_snapshot_reads_the_verifiers_wstg_items_and_no_reviewer_name(
    sqlite_session,
):
    company = _company(sqlite_session)
    run = AgentRun(
        company_id=company.id,
        producer="le-fabrique-task-worker",
        external_ref="task-1",
        artifact_digest="7" * 64,
        artifact_size=1,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    sqlite_session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[
                {"name": "risk_tier", "outcome": "pass", "tier": "low"},
                {
                    "name": "security_probe",
                    "outcome": "refuted",
                    "observed_by": "daemon",
                    "wstg_commit": "87cf4a4688ba49a64068adf6246496cf5459f709",
                    "items": [
                        {
                            "id": "WSTG-v42-CLNT-07",
                            "status": "refuted",
                            "observation": "x",
                        },
                        {"id": "not-a-wstg-id", "status": "pass"},
                    ],
                },
            ],
        )
    )
    sqlite_session.add(
        WstgReview(
            company_id=company.id,
            wstg_id="WSTG-v42-INFO-02",
            result="pass",
            reviewer_name="Ana Example",
            reviewed_on="2026-11-10",
            artifact="reports/p.pdf",
            record_sha256="b" * 64,
        )
    )
    sqlite_session.commit()
    snap = collect.snapshot(sqlite_session, sqlite_session.get(Company, company.id))
    [obs] = snap["wstg_observations"]
    assert obs["artifact_sha256"] == "7" * 64 and obs["run_id"] == run.id
    assert obs["tool"] == "security_probe (WSTG 87cf4a4)"
    assert obs["items"] == [
        {"id": "WSTG-v42-CLNT-07", "status": "refuted", "observation": "x"}
    ]
    [review] = snap["wstg_reviews"]
    assert review["record_sha256"] == "b" * 64
    assert "Ana Example" not in json.dumps(snap)
