"""Acceptance tests under the Git workflow, and the verification a ``verified`` event names.

Two findings of La Fabrique running on itself (#329 retrospective):

- With the Git workflow on, a task's branch holds only the acceptance tests
  of its base and of the tasks it depends on; QA's tests for a sibling
  requirement live on the sibling's branch. The Verifier treated those as
  edited, copied them in and ran them, so a task failed on a sibling's test.
  A task now guards and runs only the tests its branch can hold, and the
  sprint's integration runs them all.
- ``verified`` task events recorded no ``verification_id``, so the
  documentation pack showed "Latest verification: none recorded".

These tests run the real daemon, real git and pytest in ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import worker_stages
from config import settings
from models import BacklogItem, Company, ProjectDiscovery, Sprint, TaskEvent
from models.agent_run import AgentRun, Verification
from models.specification import Specification
from services.loop.worker import deliver as worker_deliver
from services.planning.project_docs import collect, log_docs
from services.release import integration
from sqlmodel import select
from test_acceptance import (
    ACCEPTANCE_TESTS,
    BOARD,
    MOVE_TESTS,
    SPEC,
    _answer,
    _checks,
    _load_daemon,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace
import sprint_integration
from test_git_workflow import git

MOVE = "def move(task, column):\n    return column\n"


class _GitDaemon:
    """The real daemon's handlers behind ``request``, deliveries on task branches."""

    jetstream_enabled = False

    def __init__(self):
        self.daemon = _load_daemon()
        self.requests: list[tuple[str, dict]] = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if subject == "workspace.generate":
            try:
                return self.daemon.deliver_files(
                    payload["workspace_root"], payload["files"], payload.get("git")
                )
            except git_workspace.GitError as e:
                return {"error": str(e), "files": []}
        if subject == "workspace.execute":
            return self.daemon.execute_shell_command(
                payload["command"], payload["workspace_root"]
            )
        if subject == "workspace.hash":
            return {
                "files": self.daemon.file_hash.hash_files(
                    payload["workspace_root"],
                    payload["paths"],
                    self.daemon._resolve_target_path,
                )
            }
        if subject == "workspace.git.integrate":
            return sprint_integration.integrate(payload["workspace_root"], payload)
        if subject == "workspace.inspect":
            return self.daemon.inspect_project(payload["path"], payload["snapshot"])
        raise AssertionError(subject)

    async def publish(self, subject, payload, msg_id=None):
        pass

    async def close(self):
        return None

    async def drain(self):
        return None


@pytest.fixture()
def sprint_on_branches(monkeypatch, sqlite_session, tmp_path):
    """A repository, a company with the Git workflow on, two requirements:
    a QA task and an implementation task (depending on it) for each."""
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
    git(root, "init", "-q", "-b", "main")
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    git(root, "add", "pytest.ini")
    git(root, "commit", "-q", "-m", "first")
    company = Company(
        company_name=f"Branches {secrets.token_hex(3)}",
        autonomy_level=2,
        git_workflow=True,
    )
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path=str(root)))
    spec = Specification(company_id=company.id, status="approved", **SPEC)
    sqlite_session.add(spec)
    sqlite_session.commit()
    sprint = Sprint(
        company_id=company.id, sprint_name="B", scope_size="S", specification_id=spec.id
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()

    def task(title, covers, kind=None, depends_on=()):
        item = BacklogItem(
            sprint_id=sprint.id,
            title=title,
            status="todo",
            assignee="QA" if kind else "Developer",
            covers=covers,
            kind=kind,
            depends_on=list(depends_on),
        )
        sqlite_session.add(item)
        sqlite_session.commit()
        sqlite_session.refresh(item)
        return item

    qa1 = task("Acceptance tests for REQ-1", ["REQ-1.1", "REQ-1.2"], "acceptance_tests")
    qa2 = task("Acceptance tests for REQ-2", ["REQ-2.1"], "acceptance_tests")
    board = task("Board", ["REQ-1.1", "REQ-1.2"], depends_on=[qa1.id])
    move = task("Move", ["REQ-2.1"], depends_on=[qa2.id])
    return SimpleNamespace(
        session=sqlite_session,
        company=company,
        sprint=sprint,
        root=root,
        qa1=qa1,
        qa2=qa2,
        board=board,
        move=move,
    )


def _deliver(nats, item, sprint, files):
    asyncio.run(
        worker_deliver._deliver_artifacts(
            nats,
            {"id": item.id, "title": item.title},
            {"role": item.assignee, "served_model": "m"},
            {"id": sprint.id, "company_id": sprint.company_id},
            _answer(files),
        )
    )


def _done(session, *items):
    for item in items:
        session.refresh(item)
        item.status = "done"
        session.add(item)
    session.commit()


def test_sibling_tasks_run_only_the_acceptance_tests_their_branch_holds(
    sprint_on_branches,
):
    s = sprint_on_branches
    session, sprint, qa1, qa2, board, move = (
        s.session,
        s.sprint,
        s.qa1,
        s.qa2,
        s.board,
        s.move,
    )
    nats = _GitDaemon()
    _deliver(nats, qa1, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    _deliver(nats, qa2, sprint, {"tests/test_req_2.py": MOVE_TESTS})
    _done(session, qa1, qa2)

    # The board's branch merges QA's REQ-1 tests; REQ-2's are on a sibling branch.
    _deliver(nats, board, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    verification, checks = _checks(session, board)
    worktree = Path(verification.evidence["git"]["worktree"])
    acceptance = verification.evidence["acceptance"]
    assert acceptance["test_files"] == ["tests/test_req_1.py"]
    assert acceptance["not_on_branch"] == ["tests/test_req_2.py"]
    assert "tampered" not in acceptance
    # Never copied in, so never run for this task.
    assert not (worktree / "tests" / "test_req_2.py").exists()
    assert verification.evidence["test_run"]["criteria"]["REQ-2.1"] == "missing"
    assert checks["acceptance_tests_untouched"]["outcome"] == "pass"
    assert checks["criteria_tests"]["results"] == {"REQ-1.1": "pass", "REQ-1.2": "pass"}
    assert checks["tests_run"]["outcome"] == "pass"
    assert verification.verdict == "verified"
    _done(session, board)

    # The sibling, with the board done on another branch: REQ-1's tests are not
    # on its branch (they would fail there), so it is judged on REQ-2's alone.
    _deliver(nats, move, sprint, {"src/__init__.py": "", "src/move.py": MOVE})
    verification, checks = _checks(session, move)
    worktree = Path(verification.evidence["git"]["worktree"])
    acceptance = verification.evidence["acceptance"]
    assert acceptance["test_files"] == ["tests/test_req_2.py"]
    assert acceptance["not_on_branch"] == ["tests/test_req_1.py"]
    assert not (worktree / "tests" / "test_req_1.py").exists()
    assert checks["acceptance_tests_untouched"]["outcome"] == "pass"
    assert checks["criteria_tests"]["results"] == {"REQ-2.1": "pass"}
    assert verification.verdict == "verified"
    _done(session, move)

    # The sprint's integration merges every branch and runs the whole suite.
    row = asyncio.run(
        integration.integrate(
            nats,
            session.get_bind(),
            sprint.id,
            trigger="manual",
            requested_by="human:A",
        )
    )
    assert row.status == "tests_passed", row.test_run
    tested_in = Path(row.test_run["workspace_root"])
    assert (tested_in / "tests" / "test_req_1.py").read_text() == ACCEPTANCE_TESTS
    assert (tested_in / "tests" / "test_req_2.py").read_text() == MOVE_TESTS
    assert git(tested_in, "rev-parse", "HEAD") == row.commit


def test_a_sibling_test_the_base_holds_is_still_guarded(sprint_on_branches):
    """A sibling's test already on the base (merged by a person) is on the
    task's branch: an edit to it is refuted and it is put back."""
    s = sprint_on_branches
    session, sprint, root, qa1, qa2, board = (
        s.session,
        s.sprint,
        s.root,
        s.qa1,
        s.qa2,
        s.board,
    )
    nats = _GitDaemon()
    _deliver(nats, qa1, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    _deliver(nats, qa2, sprint, {"tests/test_req_2.py": MOVE_TESTS})
    _done(session, qa1, qa2)
    git(root, "merge", "-q", "--no-ff", "-m", "QA REQ-2", f"fabrique/task-{qa2.id}")

    weakened = MOVE_TESTS.replace('== "done"', "is not None")
    _deliver(
        nats,
        board,
        sprint,
        {"src/__init__.py": "", "src/board.py": BOARD, "tests/test_req_2.py": weakened},
    )
    verification, checks = _checks(session, board)
    worktree = Path(verification.evidence["git"]["worktree"])
    acceptance = verification.evidence["acceptance"]
    assert acceptance["not_on_branch"] == []
    assert acceptance["tampered"] == ["tests/test_req_2.py"]
    assert acceptance["restored"] == ["tests/test_req_2.py"]
    assert (worktree / "tests" / "test_req_2.py").read_text() == MOVE_TESTS
    assert checks["acceptance_tests_untouched"]["outcome"] == "refuted"
    assert verification.verdict == "refuted"


def test_a_sibling_test_the_base_holds_in_an_earlier_version_is_not_an_edit(
    sprint_on_branches,
):
    """Run 2 of the self-hosting owner action (2026-11-24): QA changed a test
    file the base already had, and an implementation task that never wrote it
    was refuted for "editing" it, the base's version on its branch."""
    s = sprint_on_branches
    session, sprint, root, qa1, qa2, board = (
        s.session,
        s.sprint,
        s.root,
        s.qa1,
        s.qa2,
        s.board,
    )
    earlier = "def test_req_2_0_earlier():\n    assert True\n"
    (root / "tests").mkdir()
    (root / "tests" / "test_req_2.py").write_text(earlier)
    git(root, "add", "tests/test_req_2.py")
    git(root, "commit", "-q", "-m", "an earlier REQ-2 test")
    nats = _GitDaemon()
    _deliver(nats, qa1, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    _deliver(nats, qa2, sprint, {"tests/test_req_2.py": MOVE_TESTS})
    _done(session, qa1, qa2)

    _deliver(nats, board, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    verification, checks = _checks(session, board)
    worktree = Path(verification.evidence["git"]["worktree"])
    acceptance = verification.evidence["acceptance"]
    assert checks["acceptance_tests_untouched"]["outcome"] == "pass"
    assert "tampered" not in acceptance
    assert (worktree / "tests" / "test_req_2.py").read_text() == earlier
    assert acceptance["base_version"] == ["tests/test_req_2.py"]
    assert verification.verdict == "verified"


def test_a_verified_event_names_its_verification_and_the_pack_shows_it(
    sprint_on_branches,
):
    s = sprint_on_branches
    session, company, sprint, qa1, board = (
        s.session,
        s.company,
        s.sprint,
        s.qa1,
        s.board,
    )
    nats = _GitDaemon()
    _deliver(nats, qa1, sprint, {"tests/test_req_1.py": ACCEPTANCE_TESTS})
    _done(session, qa1)
    _deliver(nats, board, sprint, {"src/__init__.py": "", "src/board.py": BOARD})
    verification, _ = _checks(session, board)

    event = session.exec(
        select(TaskEvent)
        .where(TaskEvent.task_id == board.id, TaskEvent.event_type == "verified")
        .order_by(TaskEvent.id.desc())
    ).first()
    assert event.verification_id == verification.id
    assert event.payload["verification"] == {
        "id": verification.id,
        "verdict": verification.verdict,
    }

    snapshot = collect.snapshot(session, session.get(Company, company.id))
    by_task = log_docs.latest_verification(snapshot)
    assert by_task[board.id]["verification_id"] == verification.id
    markdown = log_docs.traceability(snapshot)["markdown"]
    rows = [r for r in markdown.splitlines() if f"| T-{board.id} " in r]
    # The Latest verification column: the verdict, cited, not "none recorded".
    assert [r.split("|")[5].strip() for r in rows] == ["verified"], rows


def test_the_pack_reads_an_older_verified_event_by_its_run(sprint_on_branches):
    """Events recorded before the fix name only their run: the pack still
    finds that run's verification."""
    s = sprint_on_branches
    session, company, board = s.session, s.company, s.board
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{board.id}",
        producer="le-fabrique-task-worker",
        claims={"files": []},
        artifact_digest="d",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    verification = Verification(
        company_id=company.id, agent_run_id=run.id, verdict="verified", checks=[]
    )
    session.add(verification)
    session.commit()
    from services.tasks import task_events

    for _ in range(2):  # A queued delivery's second reading: one entry, not two.
        task_events.record(session, board, "verified", "system", run_id=run.id)
    session.commit()

    snapshot = collect.snapshot(session, session.get(Company, company.id))
    mine = [v for v in snapshot["verifications"] if v["task_id"] == board.id]
    assert [v["verification_id"] for v in mine] == [verification.id]
