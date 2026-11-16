"""The Verifier's verdict on a posted run (``POST /v1/agent_runs/{id}/verify``):
an empty artifact, failed or missing CI evidence, claimed paths present or
absent from the artifact, and evidence that leaves the daemon's report out.
The history and audit of a verification are in
``test_verification_records.py``, the checks read without the API in
``test_verification_checks.py``; the shared rows in ``verification_helpers``.
"""

import pytest

from verification_helpers import _ingest, _make_company, _payload, _verify


def test_empty_artifact_claiming_full_implementation_is_refuted(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    claims = {
        "body": (
            "Implements backend/app/crypto.py with Fernet encrypt/decrypt and "
            "16 tests in backend/tests/test_crypto.py; suite 59 -> 75 passed"
        )
    }
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, artifact="", claims=claims),
    ).json()

    response = _verify(test_client, run["id"], company_id)

    assert response.status_code == 201
    body = response.json()
    assert body["verdict"] == "refuted"
    artifact_check = next(c for c in body["checks"] if c["name"] == "artifact_nonempty")
    assert artifact_check["outcome"] == "refuted"
    assert "empty" in artifact_check["observation"]


def test_failed_ci_evidence_refutes_green_build_claim(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    artifact = "+++ b/backend/app/crypto.py\n+++ b/backend/tests/test_crypto.py\n"
    claims = {"summary": "backend/app/crypto.py added; build is green"}
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, artifact=artifact, claims=claims),
    ).json()

    response = _verify(
        test_client,
        run["id"],
        company_id,
        {
            "ci": {
                "conclusion": "failure",
                "source": "github-check-run",
                "ref": "run/123",
            }
        },
    )

    assert response.json()["verdict"] == "refuted"
    test_check = next(
        c for c in response.json()["checks"] if c["name"] == "claimed_tests_evidenced"
    )
    assert test_check["outcome"] == "refuted"
    assert "failure" in test_check["observation"]


def test_test_claim_without_independent_evidence_is_unverifiable(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, claims={"summary": "75 tests passed"}),
    ).json()

    response = _verify(test_client, run["id"], company_id)

    assert response.json()["verdict"] == "unverifiable"
    assert response.json()["verdict"] not in {"verified", "refuted"}


@pytest.mark.parametrize(
    ("evidence", "outcome", "observation_text"),
    [
        (None, "unverifiable", "no CI evidence supplied"),
        ({}, "unverifiable", "no CI evidence supplied"),
        ({"ci": "success"}, "unverifiable", "no conclusion"),
        ({"ci": {}}, "unverifiable", "no conclusion"),
        ({"ci": {"conclusion": None}}, "unverifiable", "no conclusion"),
        (
            {
                "ci": {
                    "conclusion": "success",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "pass",
            "reports success (run/1)",
        ),
        (
            {
                "ci": {
                    "conclusion": "failure",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "refuted",
            "reports failure (run/1)",
        ),
        (
            {
                "ci": {
                    "conclusion": "timed_out",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "refuted",
            "reports timed_out (run/1)",
        ),
        (
            {
                "ci": {
                    "conclusion": "cancelled",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "unverifiable",
            "neither confirms nor refutes",
        ),
        (
            {
                "ci": {
                    "conclusion": "skipped",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "unverifiable",
            "neither confirms nor refutes",
        ),
        (
            {
                "ci": {
                    "conclusion": "neutral",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "unverifiable",
            "neither confirms nor refutes",
        ),
        (
            {
                "ci": {
                    "conclusion": "action_required",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "unverifiable",
            "neither confirms nor refutes",
        ),
        (
            {
                "ci": {
                    "conclusion": "stale",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "unverifiable",
            "neither confirms nor refutes",
        ),
        (
            {
                "ci": {
                    "conclusion": "new-conclusion",
                    "source": "github-check-run",
                    "ref": "run/1",
                }
            },
            "unverifiable",
            "neither confirms nor refutes",
        ),
    ],
)
def test_ci_evidence_conclusion_verdicts(
    evidence,
    outcome,
    observation_text,
    sqlite_session,
    test_client,
    monkeypatch,
    tmp_path,
):
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, claims={"summary": "all tests pass"}),
    ).json()

    response = _verify(test_client, run["id"], company_id, evidence)

    check = next(
        c for c in response.json()["checks"] if c["name"] == "claimed_tests_evidenced"
    )
    assert check["outcome"] == outcome
    assert observation_text in check["observation"]
    assert "None" not in check["observation"]


def test_test_claim_with_successful_ci_and_present_paths_is_verified(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    artifact = "diff --git a/backend/app/crypto.py b/backend/app/crypto.py\n"
    claims = {"summary": "backend/app/crypto.py added; all tests pass"}
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, artifact=artifact, claims=claims),
    ).json()

    response = _verify(
        test_client,
        run["id"],
        company_id,
        {
            "ci": {
                "conclusion": "success",
                "source": "github-check-run",
                "ref": "run/124",
            }
        },
    )

    assert response.json()["verdict"] == "verified"


def test_claimed_path_absent_from_artifact_is_refuted(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, claims={"summary": "Added backend/app/crypto.py"}),
    ).json()

    response = _verify(test_client, run["id"], company_id)

    assert response.json()["verdict"] == "refuted"
    file_check = next(
        c for c in response.json()["checks"] if c["name"] == "claimed_files_present"
    )
    assert file_check["outcome"] == "refuted"
    assert "backend/app/crypto.py" in file_check["observation"]


def test_claimed_path_in_new_file_diff_line_is_present(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    path = "backend/app/crypto.py"
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(
            company_id,
            artifact=f"+++ b/{path}\n",
            claims={"summary": f"Added {path}"},
        ),
    ).json()

    response = _verify(test_client, run["id"], company_id)

    assert response.json()["verdict"] == "verified"
    file_check = next(
        c for c in response.json()["checks"] if c["name"] == "claimed_files_present"
    )
    assert file_check["outcome"] == "pass"


def test_no_claims_and_no_artifact_is_verified(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, artifact="", claims=None),
    ).json()

    response = _verify(test_client, run["id"], company_id)

    assert response.json()["verdict"] == "verified"
    artifact_check = next(
        c for c in response.json()["checks"] if c["name"] == "artifact_nonempty"
    )
    assert artifact_check["observation"] == "no claims and no artifact"


def test_posted_evidence_omitting_the_daemon_report_cannot_verify_writes(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    """A caller must not get a verified run by leaving the daemon's report out.

    ``POST /v1/agent_runs/{id}/verify`` takes evidence from whoever calls it.
    Gating the disk check on the *presence* of ``workspace_writes`` let an
    omitted key read as "nothing to check"; the claim alone must open the check
    so its absence settles as unverifiable.
    """
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(
            company_id,
            artifact="diff --git a/app/api.py b/app/api.py\n+++ b/app/api.py\n",
            claims={"summary": "created the endpoint", "files": ["app/api.py"]},
        ),
    ).json()

    response = _verify(
        test_client, run["id"], company_id, evidence={"reason": "nothing to report"}
    )

    assert response.json()["verdict"] == "unverifiable"
    file_check = next(
        c for c in response.json()["checks"] if c["name"] == "files_written"
    )
    assert file_check["outcome"] == "unverifiable"


def test_a_run_that_claims_no_files_is_unaffected_by_the_gate(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    """The gate change must not raise a disk check where no file was claimed."""
    company_id = _make_company(sqlite_session)
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, claims={"summary": "looked at the repository"}),
    ).json()

    response = _verify(test_client, run["id"], company_id)

    assert response.json()["verdict"] == "verified"
    assert not [c for c in response.json()["checks"] if c["name"] == "files_written"]
