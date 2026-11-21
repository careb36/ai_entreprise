"""A required check that did not run is unverifiable, never a silent pass (issue #766).

On 2026-11-20 the factory's company held 66 verifications and none contained
a test run: with tests off nothing was recorded, the Verifier added
``tests_run`` only when ``test_run`` was present, and the verdict looked only
at the checks present. ``services/evidence/required_checks.py`` decides what a
delivery must show from the paths it changes.
"""

from __future__ import annotations

from uuid import uuid4

from models import Company
from models.agent_run import AgentRun
from services.evidence import required_checks
from services.evidence.verification import verify_agent_run

WRITTEN = {
    "workspace_writes": [
        {"path": "backend/app/sso.py", "status": "written", "sha256": "a" * 64}
    ],
    "expected": {"backend/app/sso.py": "a" * 64},
}


def test_code_needs_a_test_observation_and_docs_do_not():
    assert required_checks.required(["backend/app/sso.py"]) == ["tests_run"]
    assert required_checks.required(["docs/SSO.md", "changelog.d/1.md"]) == []
    assert required_checks.required(["LICENSE", "notes.txt"]) == []
    assert required_checks.required([]) == []


def test_a_missing_test_run_is_unverifiable_with_the_reason():
    [added] = required_checks.missing(["a.py", "b.ts", "README.md"], {}, [])
    assert added["name"] == "tests_run"
    assert added["outcome"] == "unverifiable"
    assert "a.py, b.ts" in added["observation"]
    assert "README.md" not in added["observation"]


def test_a_test_run_or_an_independent_ci_satisfies_it():
    ran = [{"name": "tests_run", "outcome": "pass"}]
    assert required_checks.missing(["a.py"], {}, ran) == []
    ci = {"ci": {"source": "github", "conclusion": "success"}}
    assert required_checks.missing(["a.py"], ci, []) == []
    # A CI entry without a conclusion observed nothing.
    assert required_checks.missing(["a.py"], {"ci": {"source": "x"}}, [])


def test_many_paths_are_named_briefly():
    paths = [f"m{i}.py" for i in range(8)]
    [added] = required_checks.missing(paths, {}, [])
    assert "and 3 more" in added["observation"]


def _run(session, files):
    company = Company(company_name=f"Req {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{uuid4().hex[:6]}",
        producer="le-fabrique-task-worker",
        claims={"files": files, "summary": "Done, tests pass."},
        artifact_text="".join(f"# Artifact: {f}\nx = 1\n" for f in files),
        artifact_digest="d",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    return run


def test_files_written_and_nothing_else_is_no_longer_verified(sqlite_session):
    """The shape of the 57 'verified' rows of sprint 3: files present, no test run."""
    run = _run(sqlite_session, ["backend/app/sso.py"])
    verification = verify_agent_run(sqlite_session, run, dict(WRITTEN))
    assert verification.verdict == "unverifiable"
    names = {c["name"]: c["outcome"] for c in verification.checks}
    assert names["files_written"] == "pass"
    assert names["tests_run"] == "unverifiable"
    assert verification.evidence["verdict_policy"]["required"] == ["tests_run"]


def test_the_reason_the_delivery_recorded_is_kept(sqlite_session):
    run = _run(sqlite_session, ["backend/app/sso.py"])
    evidence = dict(WRITTEN) | {
        "test_run": {
            "command": None,
            "reason": "tests are disabled for this deployment",
        }
    }
    verification = verify_agent_run(sqlite_session, run, evidence)
    tests_run = [c for c in verification.checks if c["name"] == "tests_run"]
    assert len(tests_run) == 1  # recorded once, not added twice
    assert "tests are disabled" in tests_run[0]["observation"]


def test_a_passing_run_is_verified_and_docs_need_none(sqlite_session):
    run = _run(sqlite_session, ["backend/app/sso.py"])
    evidence = dict(WRITTEN) | {
        "test_run": {"command": "pytest", "exit_code": 0, "output_tail": "1 passed"},
        "ci": {"source": "workspace daemon", "conclusion": "success"},
    }
    assert verify_agent_run(sqlite_session, run, evidence).verdict == "verified"

    docs = _run(sqlite_session, ["docs/SSO.md"])
    written = {
        "workspace_writes": [
            {"path": "docs/SSO.md", "status": "written", "sha256": "b" * 64}
        ],
        "expected": {"docs/SSO.md": "b" * 64},
    }
    verification = verify_agent_run(sqlite_session, docs, written)
    assert verification.verdict == "verified"
    assert verification.evidence["verdict_policy"]["required"] == []
