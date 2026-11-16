"""The protected-path guard after review (PR #624): the list the backend ships,
failing closed, and the late flag.

- The backend image has no ``docs/``, so the list in force, the evidence's
  tests included, is runtime data under ``backend/app/``
  (``services/evidence/protected_list.json``), generated from the test map by
  ``scripts/protected_paths_data.py`` and kept in step here.
- Without that data the guard fails closed: the delivery is unverifiable and
  high risk, the Manager agent refuses it and QA holds it for a person.
- A flag turned on after a delivery was verified, before QA approved it, still
  holds it: QA re-matches the recorded run's paths under the current setting.
- The re-match covers what the first verification did: the paths in the
  claims' prose and the workspace edit tool's files, besides
  ``subject.changed_files`` and the daemon's observations.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest
from manager_agent_helpers import _company, _cycle, _hold, _policy, answer
from models import AgentRun, Company
from models.agent_run import Verification
from services.contracts import protected_paths
from services.contracts.tool_calls import WORKSPACE_SERVER
from services.evidence import protected_check
from services.evidence.verification import verify_agent_run
from sqlmodel import select
from test_risk_tier import RISK_HOLD_PREFIX, _approve, _item_in_review, db  # noqa: F401 - a fixture

REPO = Path(__file__).resolve().parents[2]
GRADER = "bench/specs/01-cli-wordcount/acceptance/test_x.py"


@pytest.fixture(autouse=True)
def _audit_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))


@pytest.fixture()
def no_list(monkeypatch, tmp_path):
    """The shipped list cannot be read, as in an image built without it."""
    monkeypatch.setattr(protected_check, "DATA_FILE", tmp_path / "absent.json")
    protected_check.patterns_in_force.cache_clear()
    yield
    protected_check.patterns_in_force.cache_clear()


def _host(session, company_id):
    company = session.get(Company, company_id)
    company.protected_paths_enabled = True
    session.add(company)
    session.commit()


# -- 1. The shipped list, and failing closed ----------------------------------


def test_the_list_in_force_does_not_depend_on_the_repository_s_docs(
    monkeypatch, tmp_path
):
    """In the image ``docs/architecture/contexts.json`` is absent."""
    monkeypatch.chdir(tmp_path)
    # Where the first version read the test map from; absent in the image.
    monkeypatch.setattr(protected_check, "_TEST_MAP", tmp_path / "x", raising=False)
    protected_check.patterns_in_force.cache_clear()
    try:
        patterns = protected_check.patterns_in_force()
    finally:
        protected_check.patterns_in_force.cache_clear()
    assert "backend/tests/test_secops_checks.py" in patterns


def test_the_shipped_list_is_the_test_map_s():
    test_map = json.loads(
        (REPO / "docs/architecture/contexts.json").read_text(encoding="utf-8")
    )["tests"]["files"]
    shipped = json.loads(protected_check.DATA_FILE.read_text(encoding="utf-8"))
    assert shipped["patterns"] == list(protected_paths.protected_patterns(test_map)), (
        "run: python3 scripts/protected_paths_data.py"
    )
    assert protected_check.DATA_FILE.is_relative_to(REPO / "backend/app")
    assert protected_paths.matches(
        ["backend/app/services/evidence/protected_list.json"]
    )


def test_without_the_list_a_delivery_is_held_not_passed(sqlite_session, no_list):
    company = _company(sqlite_session)
    _host(sqlite_session, company.id)
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{secrets.token_hex(3)}",
        producer="test",
        claims={"files": ["src/a.py"]},
        artifact_text="x",
        artifact_digest="0" * 64,
        artifact_size=1,
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    evidence = {"workspace_writes": [{"path": "src/a.py", "status": "written"}]}
    verification = verify_agent_run(sqlite_session, run, evidence)
    checks = {c["name"]: c for c in verification.checks}
    assert checks["protected_paths"]["outcome"] == "unverifiable"
    assert checks["risk_tier"]["tier"] == "high"
    assert verification.verdict != "verified"


def test_without_the_list_the_manager_agent_does_not_accept(sqlite_session, no_list):
    company = _company(sqlite_session, autonomy=2)
    _host(sqlite_session, company.id)
    item = _hold(sqlite_session, company, human_led=True, tier="low", files=1)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"


def test_without_the_list_qa_holds_for_a_person(db, monkeypatch, no_list):  # noqa: F811
    item = _item_in_review(db, "low", autonomy=3)
    _host(db, _company_of(db, item))
    item = _approve(monkeypatch, db, item)
    assert item.status == "blocked"
    assert item.comments[-1]["text"].startswith(RISK_HOLD_PREFIX)


# -- 2. The flag turned on between verification and QA ------------------------


def _company_of(session, item) -> int:
    run = session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).one()
    return run.company_id


def _recorded(session, item, claims=None, evidence=None):
    run = session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).one()
    if claims is not None:
        run.claims = claims
    verification = session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).one()
    if evidence is not None:
        verification.evidence = evidence
    session.add_all([run, verification])
    session.commit()
    return run


@pytest.mark.parametrize(
    "where",
    ["declared", "observed"],
)
def test_a_flag_turned_on_before_qa_approves_holds_the_task(
    db,  # noqa: F811
    monkeypatch,
    where,
):
    """Verified low risk with the flag off; the Owner turns it on; QA approves."""
    item = _item_in_review(db, "low", autonomy=3)
    if where == "declared":
        _recorded(db, item, claims={"files": [GRADER]})
    else:
        _recorded(
            db,
            item,
            claims={"files": []},
            evidence={"workspace_writes": [{"path": GRADER, "status": "written"}]},
        )
    _host(db, _company_of(db, item))
    item = _approve(monkeypatch, db, item)
    assert item.status == "blocked"
    assert item.comments[-1]["text"].startswith(RISK_HOLD_PREFIX)
    assert f"grader: {GRADER}" in item.comments[-1]["text"]


def test_without_the_flag_qa_still_closes_a_low_risk_bench_delivery(
    db,  # noqa: F811
    monkeypatch,
):
    item = _item_in_review(db, "low", autonomy=3)
    _recorded(db, item, claims={"files": [GRADER]})
    item = _approve(monkeypatch, db, item)
    assert item.status == "done"


# -- 3. The re-match covers what the first verification did -------------------


@pytest.mark.parametrize(
    ("claims", "evidence", "path"),
    [
        ({"summary": f"I rewrote {GRADER} to pass"}, {}, GRADER),
        (
            {"files": ["src/a.py"]},
            {
                "tool_calls": [
                    {
                        "server": WORKSPACE_SERVER,
                        "tool": "edit",
                        "path": "scripts/bench/compare.py",
                    }
                ]
            },
            "scripts/bench/compare.py",
        ),
    ],
)
def test_the_manager_agent_re_matches_prose_and_edited_paths(
    sqlite_session, claims, evidence, path
):
    company = _company(sqlite_session, autonomy=2)
    _host(sqlite_session, company.id)
    item = _hold(sqlite_session, company, human_led=True, tier="low", files=1)
    _recorded(sqlite_session, item, claims=claims, evidence=evidence)
    _policy(sqlite_session, company, "accept_delivery")
    _cycle(sqlite_session, company, answer("accept", "accept_delivery"))
    sqlite_session.refresh(item)
    assert item.status == "blocked"
    assert path in "\n".join(_cycle.prompts)
