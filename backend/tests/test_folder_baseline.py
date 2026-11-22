"""A test that already failed in the shared folder is not this delivery's
failure (``services/evidence/test_baseline.py``, ``services/loop/folder_baseline.py``).

The cases are the bench's of 2026-11-22, spec 06: run 51 (task 53) was refuted
for a seed test task 52's file had broken, and run 61 (task 56) for a CLI test
task 54's refuted delivery had broken and left behind."""

import pytest
from models.agent_run import AgentRun, Verification
from services.evidence import test_baseline
from services.evidence.verification import check_tests_run
from services.loop import folder_baseline, refuted_undo
from test_git_workflow import _company

SEED = "tests/test_cart.py::test_a_sku_keeps_its_price"
REQ = "tests/test_discounts.py::test_req_2_1_save10_rounds_half_up"
CLI = "tests/test_cli.py::test_a_missing_file_exits_1"


def _evidence(failing, before, written, excused=("REQ-2.1",), count=None):
    return {
        "workspace_root": "/p",
        "workspace_writes": [{"path": p, "status": "written"} for p in written],
        "acceptance": {"excused": list(excused)},
        "test_run": {
            "command": "pytest",
            "exit_code": 1,
            "failing_tests": failing,
            "failing_count": len(failing) if count is None else count,
        },
        "test_baseline": {"run_id": 50, "failing_tests": before},
    }


def test_run_51_a_seed_test_another_task_broke_is_not_its_failure():
    evidence = _evidence([SEED, REQ], [SEED], ["tests/test_discounts.py"])

    check = check_tests_run(evidence)

    assert check["outcome"] == "pass"
    assert "already failed in the folder" in check["observation"]
    assert "run #50" in check["observation"] and SEED in check["observation"]
    assert "REQ-2.1" in check["observation"]


def test_run_61_a_cli_test_left_broken_is_not_its_failure():
    evidence = _evidence([CLI], [CLI], ["shopcart/discounts.py"], excused=())
    assert check_tests_run(evidence)["outcome"] == "pass"


@pytest.mark.parametrize(
    "evidence",
    [
        # A test that did not fail before is the delivery's.
        _evidence([SEED, CLI], [SEED], ["shopcart/cart.py"]),
        # A test in a file the delivery wrote is its own, failing before or not.
        _evidence([SEED], [SEED], ["tests/test_cart.py"]),
        # A cut list of failures cannot be shown to be all explained.
        _evidence([SEED], [SEED], [], count=60),
        # No baseline, or one without names, explains nothing.
        _evidence([SEED], [], []),
        # A criterion this task implements: its test failed before by design.
        {
            **_evidence([REQ], [REQ], ["shopcart/discounts.py"], excused=()),
            "acceptance": {"all": ["REQ-2.1"], "excused": []},
        },
        # Only acceptance tests: the existing excuse, not this one.
        _evidence([REQ], [SEED], []),
    ],
)
def test_what_is_still_the_deliverys_failure(evidence):
    explained, _, _ = test_baseline.explain(evidence)
    assert not explained
    only_excused = evidence["test_run"]["failing_tests"] == [REQ] and evidence[
        "acceptance"
    ].get("excused")
    if not only_excused:
        assert check_tests_run(evidence)["outcome"] == "refuted"


def test_older_evidence_without_a_count_is_not_excused():
    evidence = _evidence([SEED], [SEED], [])
    del evidence["test_run"]["failing_count"]
    assert check_tests_run(evidence)["outcome"] == "refuted"


# --- what the loop records -----------------------------------------------------


def _run(session, company, ref, evidence):
    run = AgentRun(
        company_id=company.id,
        external_ref=ref,
        producer="le-fabrique-task-worker",
        agent_role="Developer",
        artifact_text="x",
        artifact_digest="d",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict="refuted",
            checks=[],
            evidence=evidence,
        )
    )
    session.commit()
    return run


def _ran(failing, root="/p", **extra):
    return {
        "workspace_root": root,
        "test_run": {
            "exit_code": 1,
            "failing_tests": failing,
            "failing_count": len(failing),
        },
        **extra,
    }


def _observe(session, company, item_id=53):
    evidence = {"workspace_root": "/p"}
    folder_baseline.observe(session.get_bind(), company.id, item_id, evidence)
    return evidence.get("test_baseline")


def test_the_latest_run_of_another_task_in_the_folder_is_the_baseline(sqlite_session):
    company = _company(sqlite_session, on=False)
    _run(sqlite_session, company, "task-51", _ran([CLI]))
    latest = _run(sqlite_session, company, "task-52", _ran([SEED]))
    _run(sqlite_session, company, "task-53", _ran(["its own earlier attempt"]))
    _run(sqlite_session, company, "task-54", _ran(["elsewhere"], root="/other"))
    _run(sqlite_session, company, "task-55", _ran(["a branch"], git={"branch": "b"}))
    _run(
        sqlite_session,
        company,
        "task-56",
        {"workspace_root": "/p", "test_run": {"reason": "no tests"}},
    )

    baseline = _observe(sqlite_session, company)

    assert baseline == {
        "run_id": latest.id,
        "failing_tests": [SEED],
        "failing_count": 1,
    }


def test_a_run_whose_delivery_was_undone_is_skipped(
    sqlite_session, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = _company(sqlite_session, on=False)
    kept = _run(sqlite_session, company, "task-51", _ran([CLI]))
    undone = _run(
        sqlite_session, company, "task-52", _ran([SEED], snapshot={"id": "s-52"})
    )
    refuted_undo.record(sqlite_session, company.id, 52, undone.id, {"snapshot": "s-52"})

    assert _observe(sqlite_session, company)["run_id"] == kept.id


def test_a_delivery_on_a_branch_takes_no_baseline(sqlite_session):
    company = _company(sqlite_session, on=False)
    _run(sqlite_session, company, "task-52", _ran([SEED]))
    evidence = {"workspace_root": "/p", "git": {"branch": "b"}}
    folder_baseline.observe(sqlite_session.get_bind(), company.id, 53, evidence)
    assert "test_baseline" not in evidence
