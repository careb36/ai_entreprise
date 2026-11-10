"""An acceptance test per criterion (``services.evidence.acceptance``): the plan puts
QA's test-writing tasks first and the implementation tasks after them; the
runner's per-test lines are read back into criterion ids; through the real
daemon, QA's failing tests are expected before their implementation, the
implementation that makes them pass verifies the criteria on the
specification, a failing one is refuted, an implementation that edits an
acceptance test is refuted, and the independent history check refuses a
criterion verified without an observed test run.
"""

import asyncio
import importlib.util
import secrets
import sys
import textwrap
from pathlib import Path

import history_check
import pytest
import worker_stages
from config import settings
from models import BacklogItem, Company, ProjectDiscovery, Sprint
from models.agent_run import AgentRun, Verification
from models.specification import Specification
from services.evidence import acceptance, criteria_tests
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import record as worker_record
from services.planning import spec_check, spec_planning
from sqlmodel import select
from test_specifications import GOOD

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location(
        "fabrique_daemon_acceptance", _DAEMON_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- reading the runner's output ---------------------------------------------


def test_criterion_ids_become_test_name_tokens_and_are_found_as_whole_words():
    assert criteria_tests.token("REQ-1.2") == "req_1_2"
    assert criteria_tests.names_criterion(
        "tests/test_a.py::test_req_1_2_lists", "REQ-1.2"
    )
    assert criteria_tests.names_criterion("REQ-1.2 lists the tasks", "REQ-1.2")
    # ``req_1_2`` is not ``req_1_21``, nor ``req_11_2``.
    assert not criteria_tests.names_criterion("test_req_1_21_x", "REQ-1.2")
    assert not criteria_tests.names_criterion("test_req_11_2_x", "REQ-1.2")


PYTEST_OUTPUT = textwrap.dedent(
    """
    tests/test_req_1.py::test_req_1_1_name PASSED                      [ 33%]
    tests/test_req_1.py::test_req_1_2_empty FAILED                     [ 66%]
    tests/test_other.py::test_helper PASSED                            [100%]
    =================================== FAILURES ===================================
    FAILED tests/test_req_1.py::test_req_1_2_empty - AssertionError
    ========================= 1 failed, 2 passed in 0.02s ==========================
    """
)
JEST_OUTPUT = "  ✓ REQ-1.1 greets by name (3 ms)\n  ✕ REQ-1.2 greets the world (1 ms)\n"


def test_per_test_lines_of_pytest_jest_go_and_cargo_are_read():
    assert criteria_tests.results_from_output(
        PYTEST_OUTPUT, ["REQ-1.1", "REQ-1.2", "REQ-2.1"]
    ) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
        "REQ-2.1": "missing",
    }
    assert criteria_tests.failing_tests(PYTEST_OUTPUT) == [
        "tests/test_req_1.py::test_req_1_2_empty"
    ]
    assert criteria_tests.results_from_output(JEST_OUTPUT, ["REQ-1.1", "REQ-1.2"]) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
    }
    assert criteria_tests.results_from_output(
        "--- PASS: TestReq_1_1 (0.00s)\n--- FAIL: TestReq_1_2 (0.00s)\n",
        ["REQ-1.1", "REQ-1.2"],
    ) == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    assert criteria_tests.results_from_output(
        "test req_1_1_name ... ok\ntest req_1_2_empty ... FAILED\n",
        ["REQ-1.1", "REQ-1.2"],
    ) == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    # Two tests of one criterion: it passes only when both do.
    assert criteria_tests.results_from_output(
        "a.py::test_req_1_1_a PASSED\na.py::test_req_1_1_b FAILED\n", ["REQ-1.1"]
    ) == {"REQ-1.1": "fail"}
    # ANSI colour is ignored; a summary line names no test.
    assert criteria_tests.results_from_output(
        "\x1b[32ma.py::test_req_1_1_a PASSED\x1b[0m\n2 passed in 0.1s\n", ["REQ-1.1"]
    ) == {"REQ-1.1": "pass"}


def test_pytest_gets_per_test_output_and_other_runners_are_left_alone():
    assert (
        criteria_tests.verbose_command("python -m pytest -q") == "python -m pytest -v"
    )
    assert criteria_tests.verbose_command("pytest -v tests") == "pytest -v tests"
    assert criteria_tests.verbose_command(
        "python -m pytest -q -p no:cacheprovider"
    ) == ("python -m pytest -p no:cacheprovider -v")
    assert criteria_tests.verbose_command("npm test --silent") == "npm test --silent"


# --- the test-writing tasks in a plan ---------------------------------------


SPEC = spec_check.normalize(GOOD)


def test_the_plan_puts_a_test_task_per_requirement_first_and_repoints_dependencies():
    tasks = [
        {"title": "Store", "covers": ["REQ-1.1"], "depends_on": []},
        {"title": "Endpoint", "covers": ["REQ-1.2"], "depends_on": [0]},
        {"title": "Board", "covers": ["REQ-2.1", "REQ-1.1"], "depends_on": [1]},
    ]
    planned = acceptance.with_test_tasks(tasks, SPEC, item_limit=8)
    assert [t["title"] for t in planned] == [
        "Acceptance tests for REQ-1: List the tasks",
        "Acceptance tests for REQ-2: Move a task",
        "Store",
        "Endpoint",
        "Board",
    ]
    assert [t["kind"] for t in planned] == [
        "acceptance_tests",
        "acceptance_tests",
        None,
        None,
        None,
    ]
    assert planned[0]["assignee"] == "QA" and planned[0]["covers"] == [
        "REQ-1.1",
        "REQ-1.2",
    ]
    assert "test_req_1_1_<what it checks>" in planned[0]["description"]
    assert [t["depends_on"] for t in planned] == [[], [], [0], [0, 2], [1, 0, 3]]
    # Already planned with its tests: left as it is.
    assert acceptance.with_test_tasks(planned, SPEC, 8) == planned


def test_the_plan_stays_within_the_scope_dropping_tasks_from_the_end():
    tasks = [
        {"title": "Store", "covers": ["REQ-1.1"], "depends_on": []},
        {"title": "Board", "covers": ["REQ-2.1"], "depends_on": [0]},
    ]
    planned = acceptance.with_test_tasks(tasks, SPEC, item_limit=3)
    assert [t["title"] for t in planned] == [
        "Acceptance tests for REQ-1: List the tasks",
        "Store",
    ]
    # A task without criteria needs no test task and no budget for one.
    assert [
        t["title"] for t in acceptance.with_test_tasks([{"title": "Docs"}], SPEC, 1)
    ] == ["Docs"]


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


def _answer(files: dict[str, str]) -> str:
    blocks = "".join(
        f"```python path={path}\n{body}```\n\n" for path, body in files.items()
    )
    return f"Delivered.\n\n{blocks}"


ACCEPTANCE_TESTS = textwrap.dedent(
    """\
    def test_req_1_1_lists_the_tasks():
        from src.board import tasks_of

        assert tasks_of("acme") == ["a", "b"]


    def test_req_1_2_one_row_per_task():
        from src.board import tasks_of

        assert len(tasks_of("acme")) == 2
    """
)
BOARD = 'def tasks_of(company):\n    return ["a", "b"]\n'
BROKEN_BOARD = 'def tasks_of(company):\n    return ["a"]\n'
MOVE_TESTS = (
    "def test_req_2_1_drop_on_done():\n    from src.move import move\n\n"
    '    assert move("t", "done") == "done"\n'
)


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


def _checks(session, item):
    session.expire_all()
    run = session.exec(
        select(AgentRun)
        .where(AgentRun.external_ref == f"task-{item.id}")
        .order_by(AgentRun.version.desc())
    ).first()
    verification = session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run.id)
        .order_by(Verification.id.desc())
    ).first()
    return verification, {c["name"]: c for c in verification.checks}


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


# --- the independent check ---------------------------------------------------


def test_a_criterion_verified_without_an_observed_run_is_illegal():
    task = {"id": 1, "status": "done", "comments": []}
    legal = [
        {
            "version": 1,
            "status": "verified",
            "verified_criteria": ["REQ-1.1"],
            "observed_test_run": True,
        }
    ]
    illegal = [
        {
            "version": 1,
            "status": "verified",
            "verified_criteria": ["REQ-1.1"],
            "observed_test_run": False,
        }
    ]
    assert not [
        v
        for v in history_check.check_task(task, legal, [])
        if v.startswith("criteria_verified")
    ]
    assert history_check.check_task(task, illegal, []) == [
        "done_without_approval",
        "criteria_verified_without_run: run v1 marks REQ-1.1 as passed with no test run observed",
    ]
    facts = history_check._run_facts(
        Verification(
            company_id=1,
            agent_run_id=1,
            verdict="verified",
            checks=[
                {
                    "name": "criteria_tests",
                    "results": {"REQ-1.1": "pass", "REQ-1.2": "fail"},
                }
            ],
            evidence={"test_run": {"exit_code": 1}},
        )
    )
    assert facts == {
        "verified_criteria": ["REQ-1.1"],
        "observed_test_run": True,
        "reads_without_hash": [],
    }
    assert history_check._run_facts(None) == {
        "verified_criteria": [],
        "observed_test_run": False,
        "reads_without_hash": [],
    }
    # An exit status that travelled as text still counts; a key that is not
    # text is read as text.
    odd = history_check._run_facts(
        Verification(
            company_id=1,
            agent_run_id=1,
            verdict="verified",
            checks=[{"name": "criteria_tests", "results": {7: "pass"}}],
            evidence={"test_run": {"exit_code": "0"}},
        )
    )
    assert odd == {
        "verified_criteria": ["7"],
        "observed_test_run": True,
        "reads_without_hash": [],
    }
    assert (
        history_check._run_facts(
            Verification(
                company_id=1,
                agent_run_id=1,
                verdict="verified",
                checks=[],
                evidence={"test_run": {"exit_code": "x"}},
            )
        )["observed_test_run"]
        is False
    )
