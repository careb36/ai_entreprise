"""An acceptance test per criterion through the real daemon
(``services.evidence.acceptance``): QA's failing tests are expected before
their implementation, the implementation that makes them pass verifies the
criteria on the specification, a failing one is refuted, an implementation
that edits an acceptance test is refuted and the test restored, and the
latest run across tasks speaks for a criterion. Reading the runner's output,
the plan's test-writing tasks and the independent history check are in
``test_acceptance_plan.py``; the shared rows in ``acceptance_helpers``.
"""

import asyncio
import secrets
import sys

import history_check
import pytest
import worker_stages
from acceptance_helpers import (
    ACCEPTANCE_TESTS,
    BOARD,
    MOVE_TESTS,
    SPEC,
    _answer,
    _checks,
    _load_daemon,
)
from config import settings
from models import BacklogItem, Company, ProjectDiscovery, Sprint
from models.agent_run import AgentRun, Verification
from models.specification import Specification
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import record as worker_record
from services.planning import spec_planning
from sqlmodel import select

# --- through the real daemon ------------------------------------------------


class _DaemonOverNATS:
    jetstream_enabled = False

    def __init__(self, daemon):
        self.daemon = daemon
        self.commands = []

    async def request(self, subject, payload, timeout=5.0):
        if subject == "workspace.generate":
            return {
                "ok": True,
                "files": self.daemon.write_files(
                    payload["workspace_root"], payload["files"]
                ),
            }
        if subject == "workspace.execute":
            self.commands.append(payload["command"])
            return self.daemon.execute_shell_command(
                payload["command"], payload["workspace_root"]
            )
        if subject == "workspace.inspect":
            return self.daemon.inspect_project(payload["path"], payload["snapshot"])
        if subject == "workspace.hash":
            return {
                "files": self.daemon.file_hash.hash_files(
                    payload["workspace_root"],
                    payload["paths"],
                    self.daemon._resolve_target_path,
                )
            }
        raise AssertionError(subject)

    async def publish(self, subject, payload, msg_id=None):
        pass


BROKEN_BOARD = 'def tasks_of(company):\n    return ["a"]\n'


@pytest.fixture()
def factory(monkeypatch, sqlite_session, tmp_path):
    bind = sqlite_session.get_bind()
    for table in (ProjectDiscovery.__table__, Specification.__table__):
        table.create(bind, checkfirst=True)
    worker_stages.use_engine(monkeypatch, bind)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "DELIVERY_TEST_COMMAND",
        f"{sys.executable} -m pytest -q -p no:cacheprovider",
    )
    root = tmp_path / "project"
    root.mkdir()
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    company = Company(
        company_name=f"Acceptance {secrets.token_hex(3)}", autonomy_level=2
    )
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path=str(root)))
    spec = Specification(company_id=company.id, status="approved", **SPEC)
    sqlite_session.add(spec)
    sqlite_session.commit()
    sprint = Sprint(
        company_id=company.id, sprint_name="A", scope_size="S", specification_id=spec.id
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    tests_task = BacklogItem(
        sprint_id=sprint.id,
        title="Acceptance tests for REQ-1",
        status="in_progress",
        assignee="QA",
        covers=["REQ-1.1", "REQ-1.2"],
        kind="acceptance_tests",
    )
    board_task = BacklogItem(
        sprint_id=sprint.id, title="Board", status="todo", covers=["REQ-1.1", "REQ-1.2"]
    )
    move_tests_task = BacklogItem(
        sprint_id=sprint.id,
        title="Acceptance tests for REQ-2",
        status="in_progress",
        assignee="QA",
        covers=["REQ-2.1"],
        kind="acceptance_tests",
    )
    sqlite_session.add_all([tests_task, board_task, move_tests_task])
    sqlite_session.commit()
    for row in (tests_task, board_task, move_tests_task):
        sqlite_session.refresh(row)
    nats = _DaemonOverNATS(_load_daemon())
    return sqlite_session, spec, sprint, nats, tests_task, board_task, move_tests_task


def _deliver(nats, item, sprint, files):
    asyncio.run(
        worker_deliver._deliver_artifacts(
            nats,
            {"id": item.id, "title": item.title},
            {"role": item.assignee or "Developer", "served_model": "m"},
            {"id": sprint.id, "company_id": sprint.company_id},
            _answer(files),
        )
    )


def test_failing_tests_first_then_the_implementation_that_verifies_the_criteria(
    factory,
):
    session, spec, sprint, nats, tests_task, board_task, move_tests_task = factory

    # QA's tests fail before the implementation: expected, and every criterion ran.
    _deliver(nats, tests_task, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    verification, checks = _checks(session, tests_task)
    assert nats.commands[-1].endswith("-p no:cacheprovider -v")
    assert checks["tests_run"]["outcome"] == "pass"
    assert (
        "only the acceptance tests of criteria not implemented yet failed (REQ-1.1, REQ-1.2)"
        in (checks["tests_run"]["observation"])
    )
    assert checks["criteria_tests"] == {
        "name": "criteria_tests",
        "outcome": "pass",
        "observation": "a test ran for every criterion: REQ-1.1 fail, REQ-1.2 fail",
        "results": {"REQ-1.1": "fail", "REQ-1.2": "fail"},
    }
    assert "acceptance_tests_untouched" not in checks
    assert verification.verdict == "verified"
    assert worker_record._delivery_gate(sprint.company_id, tests_task.id) is None
    tests_task.status = "done"
    session.add(tests_task)
    session.commit()

    # Nothing is verified yet on the specification; the tests are listed apart.
    cov = spec_planning.coverage(session, spec)
    assert (cov["verified"], cov["done"]) == (0, 0)
    assert [t["id"] for t in cov["criteria"][0]["tests"]] == [tests_task.id]
    assert cov["criteria"][0]["tasks"] == [
        {
            "id": board_task.id,
            "sprint_id": sprint.id,
            "title": "Board",
            "status": "todo",
        }
    ]

    # The Developer's prompt names the test file and the test names.
    section = spec_planning.requirements_for(session, sprint, board_task)
    assert "## Acceptance tests to make pass" in section
    assert "- tests/test_req_1.py" in section
    assert "- REQ-1.1: a test named test_req_1_1_…" in section
    assert "Acceptance tests to make pass" not in spec_planning.requirements_for(
        session, sprint, tests_task
    )

    # An implementation that leaves a criterion failing is refuted and sent back.
    _deliver(
        nats, board_task, sprint, {"src/__init__.py": "", "src/board.py": BROKEN_BOARD}
    )
    verification, checks = _checks(session, board_task)
    assert checks["criteria_tests"]["outcome"] == "refuted"
    assert checks["criteria_tests"]["results"] == {"REQ-1.1": "fail", "REQ-1.2": "fail"}
    assert (
        checks["tests_run"]["outcome"] == "refuted"
    )  # its own criteria are never excused
    assert verification.verdict == "refuted"
    assert (
        worker_record._delivery_gate(sprint.company_id, board_task.id)[0]
        == "tests failed"
    )
    assert spec_planning.coverage(session, spec)["verified"] == 0

    # The one that makes them pass verifies the criteria, with REQ-2's tests
    # (not implemented yet) still failing, excused.
    _deliver(nats, move_tests_task, sprint, {"tests/test_req_2.py": MOVE_TESTS})
    _deliver(nats, board_task, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    verification, checks = _checks(session, board_task)
    assert checks["tests_run"]["outcome"] == "pass"
    assert "(REQ-2.1)" in checks["tests_run"]["observation"]
    assert checks["criteria_tests"] == {
        "name": "criteria_tests",
        "outcome": "pass",
        "observation": "REQ-1.1 pass, REQ-1.2 pass",
        "results": {"REQ-1.1": "pass", "REQ-1.2": "pass"},
    }
    assert checks["acceptance_tests_untouched"]["outcome"] == "pass"
    assert verification.verdict == "verified"
    assert worker_record._delivery_gate(sprint.company_id, board_task.id) is None
    cov = spec_planning.coverage(session, spec)
    assert cov["verified"] == 2
    assert [(c["id"], c["verified"], c["last_result"]) for c in cov["criteria"]] == [
        ("REQ-1.1", True, "pass"),
        ("REQ-1.2", True, "pass"),
        ("REQ-2.1", False, None),
    ]
    assert spec_planning.acceptance_summary(session, board_task) == (
        '**Acceptance:** 2 of 3 criteria of "Task board" verified by a passing test '
        "in an observed run (67 %); this task's: 2 of 2."
    )

    # The checker reads the same runs: nothing illegal.
    session.expire_all()
    assert not [
        v
        for run in session.exec(select(AgentRun)).all()
        for v in history_check.check_task(
            {"id": run.id, "status": "done", "comments": []},
            [
                {
                    "version": run.version,
                    "status": run.status,
                    **history_check._run_facts(
                        session.exec(
                            select(Verification)
                            .where(Verification.agent_run_id == run.id)
                            .order_by(Verification.id.desc())
                        ).first()
                    ),
                }
            ],
            [],
        )
        if v.startswith("criteria_verified_without_run")
    ]


def test_an_implementation_that_edits_an_acceptance_test_is_refuted_and_the_test_restored(
    factory, tmp_path
):
    session, _spec, sprint, nats, tests_task, board_task, _move = factory
    _deliver(nats, tests_task, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    tests_task.status = "done"
    session.add(tests_task)
    session.commit()

    weakened = ACCEPTANCE_TESTS.replace('["a", "b"]', '["a"]').replace("== 2", "== 1")
    _deliver(
        nats,
        board_task,
        sprint,
        {
            "src/__init__.py": "",
            "src/board.py": BROKEN_BOARD,
            "tests/test_req_1.py": weakened,
        },
    )
    verification, checks = _checks(session, board_task)
    # The weakened test was put back before the run: QA's tests judge the
    # criteria (and fail), the edit is refuted, never silently accepted.
    assert (
        tmp_path / "project" / "tests" / "test_req_1.py"
    ).read_text() == ACCEPTANCE_TESTS
    assert checks["criteria_tests"]["results"] == {"REQ-1.1": "fail", "REQ-1.2": "fail"}
    assert checks["acceptance_tests_untouched"] == {
        "name": "acceptance_tests_untouched",
        "outcome": "refuted",
        "observation": (
            "edits the acceptance test tests/test_req_1.py: a change to a criterion's "
            "test is the specification's to decide; restored from QA's delivery: "
            "tests/test_req_1.py"
        ),
    }
    assert verification.evidence["acceptance"]["tampered"] == ["tests/test_req_1.py"]
    assert verification.verdict == "refuted"
    problem, observation = worker_record._delivery_gate(
        sprint.company_id, board_task.id
    )
    assert problem == "acceptance test edited" and "tests/test_req_1.py" in observation

    # A file changed behind the loop's back (not in a delivery) is seen the same way.
    (tmp_path / "project" / "tests" / "test_req_1.py").write_text(
        "def test_req_1_1_x():\n    pass\n"
    )
    _deliver(nats, board_task, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    verification, checks = _checks(session, board_task)
    assert (
        tmp_path / "project" / "tests" / "test_req_1.py"
    ).read_text() == ACCEPTANCE_TESTS
    assert checks["acceptance_tests_untouched"]["outcome"] == "refuted"
    assert checks["criteria_tests"]["results"] == {"REQ-1.1": "pass", "REQ-1.2": "pass"}
    assert verification.verdict == "refuted"
    # The tests passed in that run, but the delivery was refuted: the
    # criteria read as passed and are not verified by it.
    cov = spec_planning.coverage(session, _spec)
    assert [(c["last_result"], c["verified"]) for c in cov["criteria"][:2]] == [
        ("pass", False),
        ("pass", False),
    ]

    # Left alone, the next delivery is verified against QA's tests.
    _deliver(nats, board_task, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    verification, checks = _checks(session, board_task)
    assert checks["acceptance_tests_untouched"]["outcome"] == "pass"
    assert verification.verdict == "verified"
    assert spec_planning.coverage(session, _spec)["verified"] == 2


def test_an_acceptance_test_delivered_as_edits_is_never_restored_from_the_edit_text(
    factory, tmp_path
):
    """Only a whole file can be put back: SEARCH/REPLACE text is not the test."""
    session, _spec, sprint, nats, tests_task, board_task, _move = factory
    _deliver(nats, tests_task, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    edited = ACCEPTANCE_TESTS.replace('== ["a", "b"]', '== ["a", "b"]  # REQ-1.1')
    asyncio.run(
        worker_deliver._deliver_artifacts(
            nats,
            {"id": tests_task.id, "title": tests_task.title},
            {"role": tests_task.assignee or "QA", "served_model": "m"},
            {"id": sprint.id, "company_id": sprint.company_id},
            "Delivered.\n\n```python path=tests/test_req_1.py edit\n"
            '<<<<<<< SEARCH\n    assert tasks_of("acme") == ["a", "b"]\n=======\n'
            '    assert tasks_of("acme") == ["a", "b"]  # REQ-1.1\n'
            ">>>>>>> REPLACE\n```\n",
        )
    )
    test_file = tmp_path / "project" / "tests" / "test_req_1.py"
    assert test_file.read_text() == edited
    tests_task.status = "done"
    session.add(tests_task)
    session.commit()

    weakened = edited.replace("== 2", "== 1")
    _deliver(
        nats,
        board_task,
        sprint,
        {"src/__init__.py": "", "src/board.py": BOARD, "tests/test_req_1.py": weakened},
    )
    verification, checks = _checks(session, board_task)
    acceptance = verification.evidence["acceptance"]
    assert acceptance["tampered"] == ["tests/test_req_1.py"]
    assert acceptance["restored"] == []
    assert "<<<<<<<" not in test_file.read_text()
    assert checks["acceptance_tests_untouched"]["outcome"] == "refuted"
    assert verification.verdict == "refuted"


def test_the_latest_run_across_tasks_speaks_for_a_criterion(factory):
    session, spec, sprint, nats, tests_task, board_task, _move = factory
    _deliver(nats, tests_task, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    # A second implementation task covering the same criteria, created later.
    again = BacklogItem(
        sprint_id=sprint.id,
        title="Board again",
        status="todo",
        covers=["REQ-1.1", "REQ-1.2"],
    )
    session.add(again)
    session.commit()
    session.refresh(again)
    # The later task verifies them first; then the earlier task's run fails them.
    _deliver(nats, again, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    assert spec_planning.coverage(session, spec)["verified"] == 2
    _deliver(
        nats, board_task, sprint, {"src/__init__.py": "", "src/board.py": BROKEN_BOARD}
    )
    cov = spec_planning.coverage(session, spec)
    # The most recent verification wins, whatever the tasks' order by id.
    assert [(c["last_result"], c["verified"]) for c in cov["criteria"][:2]] == [
        ("fail", False),
        ("fail", False),
    ]


def test_the_daemon_hashes_files_as_they_are_on_disk(tmp_path):
    daemon = _load_daemon()
    root = tmp_path / "p"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_bytes(b"x = 1\n")
    reported = daemon.file_hash.hash_files(
        str(root),
        ["tests/test_a.py", "tests/none.py", "../out"],
        daemon._resolve_target_path,
    )
    assert [(f["path"], f["status"]) for f in reported] == [
        ("tests/test_a.py", "present"),
        ("tests/none.py", "missing"),
        ("../out", "rejected"),
    ]
    assert reported[0]["sha256"] == __import__("hashlib").sha256(b"x = 1\n").hexdigest()
    assert daemon.file_hash.hash_files(
        str(tmp_path / "nowhere"), ["a"], daemon._resolve_target_path
    ) == [{"path": "a", "status": "workspace_missing"}]


def test_a_test_task_whose_tests_do_not_run_is_refuted(factory):
    session, _spec, sprint, nats, tests_task, _board, _move = factory
    # A collection error lists no test: no criterion ran.
    _deliver(nats, tests_task, sprint, {"tests/test_req_1.py": "import nothing_here\n"})
    verification, checks = _checks(session, tests_task)
    assert checks["criteria_tests"]["outcome"] == "refuted"
    assert checks["criteria_tests"]["observation"].startswith(
        "no test ran for REQ-1.1, REQ-1.2 (name one test_req_1_1_…"
    )
    assert (
        checks["tests_run"]["outcome"] == "refuted"
    )  # nothing excused a collection error
    assert verification.verdict == "refuted"
    assert (
        worker_record._delivery_gate(sprint.company_id, tests_task.id)[0]
        == "tests failed"
    )


def test_without_a_specification_nothing_changes(factory, monkeypatch):
    session, _spec, sprint, nats, _tests, board_task, _move = factory
    sprint.specification_id = None
    session.add(sprint)
    session.commit()
    _deliver(nats, board_task, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    _verification, checks = _checks(session, board_task)
    assert nats.commands[-1].endswith("-q -p no:cacheprovider")
    assert "criteria_tests" not in checks and "acceptance_tests_untouched" not in checks
