"""The Developer's move and delete through the toolbox, the worker and the Verifier.

Design: ``docs/design/move-and-delete-files.md``. The tools are offered only
where edits may go and when every daemon announces both operations; a move and
a delete are observations with the daemon's hashes; at close the created file
comes back to deliver and the deleted one as a deletion whose expected hash is
the daemon's; the worker delivers both, the daemon removes the emptied file,
and the evidence records the deletion, which counts as a change of its path.
The daemon's functions run for real against ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import hashlib

import agent_tools_helpers
import worker_stages
from agent_tools_helpers import _call, _DaemonOverNATS, _Requester
from config import settings
from models.agent_run import AgentRun, Verification
from models.company import Company, ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.evidence import protected_check
from services.evidence.verification import evaluate_workspace_writes
from services.loop.worker import deliver as worker_deliver
from services.tools import developer_tools, workspace_edits
from services.workspace import daemon_liveness
from sqlmodel import select

daemon = agent_tools_helpers.daemon
project = agent_tools_helpers.project

TESTS = "".join(f"def test_{i}():\n    assert {i} == {i}\n\n\n" for i in range(4))


def _box(daemon, project, **kwargs):
    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), can_edit=True, edit_suffixes={".py"}, **kwargs
    )
    box.can_move = True
    return box


def _split(daemon, project):
    """Every line of tests/test_all.py moved to two new files, the original deleted."""
    (project / "tests").mkdir()
    (project / "tests" / "test_all.py").write_text(TESTS)
    box = _box(daemon, project)
    first, _ = box.call(
        _call(
            "workspace__move",
            path="tests/test_all.py",
            start_line=1,
            end_line=8,
            to_path="tests/test_a.py",
        ),
        1,
    )
    second, _ = box.call(
        _call(
            "workspace__move",
            path="tests/test_all.py",
            start_line=1,
            end_line=8,
            to_path="tests/test_b.py",
        ),
        2,
    )
    gone, text = box.call(_call("workspace__delete", path="tests/test_all.py"), 3)
    box.close()
    return box, [first, second, gone], text


def test_move_and_delete_are_offered_only_where_edits_go_and_daemons_announce_them(
    project, daemon, monkeypatch
):
    box = developer_tools.WorkspaceToolbox(
        _Requester(daemon), str(project), can_edit=True
    )
    names = [s["function"]["name"] for s in box.schemas()]
    assert "workspace__move" not in names and "workspace__delete" not in names
    box.can_move = True
    assert {"workspace__move", "workspace__delete"} <= set(box.tools)
    assert box.tools["workspace__move"]["read_only"] is False
    assert box.tools["workspace__delete"]["read_only"] is False

    announced = {
        ("workspace.edit", "move"),
        ("workspace.edit", "delete"),
        ("workspace.generate", "delete"),
    }
    monkeypatch.setattr(
        daemon_liveness, "announces", lambda s, f=None: (s, f) in announced
    )
    assert workspace_edits.can_move(True) is True
    assert workspace_edits.can_move(False) is False
    announced.discard(("workspace.generate", "delete"))  # An older daemon among them.
    assert workspace_edits.can_move(True) is False


def test_a_move_and_a_delete_are_observations_with_the_daemon_s_hashes(project, daemon):
    _box, (first, _second, gone), text = _split(daemon, project)

    moved = "".join(TESTS.splitlines(keepends=True)[:8])
    assert first["tool"] == "move" and first["is_error"] is False
    assert first["moved_lines"] == 8
    assert first["moved_sha256"] == hashlib.sha256(moved.encode()).hexdigest()
    assert (
        first["target"]["created"] is True and first["target"]["sha256_before"] is None
    )
    assert gone["tool"] == "delete" and gone["deleted"] is True
    assert gone["sha256_before"] == hashlib.sha256(TESTS.encode()).hexdigest()
    assert text.startswith("Deleted tests/test_all.py")
    # Back on disk as before the loop: the original, no created file.
    assert (project / "tests" / "test_all.py").read_text() == TESTS
    assert not (project / "tests" / "test_a.py").exists()


def test_close_brings_back_the_created_files_and_the_deletion_with_its_expected_hash(
    project, daemon
):
    box, _calls, _ = _split(daemon, project)

    targets = workspace_edits.with_tool_edits([], box.edited_files)
    assert sorted(t["path"] for t in targets) == ["tests/test_a.py", "tests/test_b.py"]
    assert (
        "".join(t["content"] for t in sorted(targets, key=lambda t: t["path"])) == TESTS
    )
    assert workspace_edits.deletions(box.edited_files) == [
        {
            "path": "tests/test_all.py",
            "delete": True,
            "sha256_expected": hashlib.sha256(TESTS.encode()).hexdigest(),
        }
    ]
    evidence = workspace_edits.observe_tools({}, {"tool_edits": box.edited_files})
    assert evidence["tool_edit_deletions"] == [
        {
            "path": "tests/test_all.py",
            "sha256_original": hashlib.sha256(TESTS.encode()).hexdigest(),
        }
    ]


def test_a_file_with_content_is_refused_and_a_wrong_file_type_too(project, daemon):
    (project / "tests").mkdir()
    (project / "tests" / "test_all.py").write_text(TESTS)
    box = _box(daemon, project)

    full, text = box.call(_call("workspace__delete", path="tests/test_all.py"), 1)
    assert full["is_error"] is True and "still has 16 lines" in text
    wrong, text = box.call(
        _call(
            "workspace__move",
            path="tests/test_all.py",
            start_line=1,
            end_line=4,
            to_path="notes.txt",
        ),
        2,
    )
    assert wrong["is_error"] is True and "does not write .txt" in text
    box.close()
    assert workspace_edits.deletions(box.edited_files) == []


def test_a_deleted_write_passes_and_counts_as_a_change_of_its_path():
    evidence = {
        "expected": {"tests/test_a.py": "a" * 64},
        "workspace_writes": [
            {"path": "tests/test_a.py", "status": "written", "sha256": "a" * 64},
            {"path": "tests/test_all.py", "status": "deleted"},
        ],
    }
    outcome, _ = evaluate_workspace_writes(
        ["tests/test_a.py", "tests/test_all.py"], evidence
    )
    assert outcome == "pass"
    assert protected_check.observed_paths(evidence) == [
        "tests/test_a.py",
        "tests/test_all.py",
    ]
    refused = {
        **evidence,
        "workspace_writes": [
            evidence["workspace_writes"][0],
            {
                "path": "tests/test_all.py",
                "status": "rejected_delete",
                "error": "the file changed",
            },
        ],
    }
    outcome, observation = evaluate_workspace_writes(
        ["tests/test_a.py", "tests/test_all.py"], refused
    )
    assert outcome == "refuted" and "rejected_delete" in observation


def test_the_worker_delivers_a_split_and_the_daemon_removes_the_emptied_file(
    project, daemon, monkeypatch, sqlite_session, tmp_path
):
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    worker_stages.use_engine(monkeypatch, bind)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", False)
    company = Company(company_name=f"Split Co {tmp_path.name}")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(
        ProjectDiscovery(company_id=company.id, project_path=str(project))
    )
    sprint = Sprint(company_id=company.id, sprint_name="T", scope_size="S")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(
        sprint_id=sprint.id, title="Split the tests", status="in_progress"
    )
    sqlite_session.add(item)
    sqlite_session.commit()
    box, calls, _ = _split(daemon, project)
    agent = {
        "role": "Developer",
        "served_model": "m",
        "tool_calls": calls,
        "tool_edits": box.edited_files,
    }

    review = asyncio.run(
        worker_deliver._deliver_artifacts(
            _DaemonOverNATS(daemon),
            {"id": item.id, "title": item.title},
            agent,
            {"id": sprint.id, "company_id": company.id},
            "Split the tests with the move tool.",
        )
    )

    assert not (project / "tests" / "test_all.py").exists()
    assert (project / "tests" / "test_a.py").read_text() + (
        project / "tests" / "test_b.py"
    ).read_text() == TESTS
    assert "Deleted: tests/test_all.py" in review
    sqlite_session.expire_all()
    run = sqlite_session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).first()
    verification = sqlite_session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).first()
    writes = {w["path"]: w["status"] for w in verification.evidence["workspace_writes"]}
    assert writes == {
        "tests/test_a.py": "written",
        "tests/test_b.py": "written",
        "tests/test_all.py": "deleted",
    }
    assert (
        verification.evidence["tool_edit_deletions"][0]["path"] == "tests/test_all.py"
    )
    moves = [c for c in verification.evidence["tool_calls"] if c["tool"] == "move"]
    assert [m["moved_lines"] for m in moves] == [8, 8]
