"""The edit session's move and delete (``vscode-daemon/workspace_move.py``).

Design: ``docs/design/move-and-delete-files.md``. In sprint 6 (2026-11-23) a
Developer could split a 642-line test file only by retyping it, wrote tests
that called functions that do not exist, and could not remove the original.

A move carries the same bytes from one file to another (created when missing)
or within a file, both files or neither, both still parsing, with the moved
bytes' sha256 computed by the daemon. A delete removes only an emptied file.
At finish a created file comes back ``created`` and a deleted one ``deleted``,
and every original is back on disk. The daemon's functions run for real
against ``tmp_path``.
"""

from __future__ import annotations

import hashlib

import agent_tools_helpers
import pytest
import workspace_edit  # the daemon's modules, on sys.path via conftest
from agent_tools_helpers import SESSION, _edit, _sha
from workspace_read import ReadError

daemon = agent_tools_helpers.daemon
project = agent_tools_helpers.project

TESTS = "".join(f"def test_{i}():\n    assert {i} == {i}\n\n\n" for i in range(6))


def _finish(daemon, root):
    return workspace_edit.finish(str(root), SESSION, daemon._resolve_target_path)


def _move(daemon, root, **data):
    return _edit(daemon, root, op="move", **data)


def _delete(daemon, root, path):
    return _edit(daemon, root, op="delete", path=path)


@pytest.fixture()
def suite(project):
    (project / "tests").mkdir()
    (project / "tests" / "test_all.py").write_text(TESTS)
    return project


def test_a_move_into_a_new_file_carries_the_same_bytes_with_their_hash(suite, daemon):
    source = suite / "tests" / "test_all.py"
    lines = TESTS.splitlines(keepends=True)
    moved = "".join(lines[12:24])  # test_3 .. test_5, four lines each

    reply = _move(
        daemon,
        suite,
        path="tests/test_all.py",
        start_line=13,
        end_line=24,
        to_path="tests/split/test_rest.py",
    )

    target = suite / "tests" / "split" / "test_rest.py"
    assert target.read_text() == moved
    assert source.read_text() == "".join(lines[:12])
    assert reply["moved_lines"] == 12
    assert reply["moved_sha256"] == hashlib.sha256(moved.encode()).hexdigest()
    assert reply["source"]["sha256_after"] == _sha(source)
    assert reply["target"] == {
        "path": "tests/split/test_rest.py",
        "sha256_before": None,
        "sha256_after": _sha(target),
        "lines": 12,
        "created": True,
    }


def test_a_move_into_an_existing_file_inserts_before_to_line(suite, daemon):
    other = suite / "tests" / "test_other.py"
    other.write_text("import os\n\n\ndef test_z():\n    assert os\n")

    _move(
        daemon,
        suite,
        path="tests/test_all.py",
        start_line=1,
        end_line=4,
        to_path="tests/test_other.py",
        to_line=4,
    )

    assert other.read_text() == (
        "import os\n\n\ndef test_0():\n    assert 0 == 0\n\n\ndef test_z():\n    assert os\n"
    )
    assert "def test_0" not in (suite / "tests" / "test_all.py").read_text()


def test_a_move_within_one_file_keeps_every_line(suite, daemon):
    source = suite / "tests" / "test_all.py"

    _move(
        daemon,
        suite,
        path="tests/test_all.py",
        start_line=21,
        end_line=24,
        to_path="tests/test_all.py",
        to_line=1,
    )

    text = source.read_text()
    assert text.startswith("def test_5():\n") and sorted(text.splitlines()) == sorted(
        TESTS.splitlines()
    )
    with pytest.raises(ReadError) as inside:
        _move(
            daemon,
            suite,
            path="tests/test_all.py",
            start_line=5,
            end_line=12,
            to_path="tests/test_all.py",
            to_line=8,
        )
    assert inside.value.status == 400


def test_a_move_that_would_break_either_file_changes_neither(suite, daemon):
    source = suite / "tests" / "test_all.py"
    before = source.read_bytes()

    with pytest.raises(
        ReadError
    ) as broken:  # Half a function: the source would not parse.
        _move(
            daemon,
            suite,
            path="tests/test_all.py",
            start_line=1,
            end_line=1,
            to_path="tests/test_half.py",
        )

    assert broken.value.status == 422 and "would not parse" in broken.value.message
    assert (
        source.read_bytes() == before
        and not (suite / "tests" / "test_half.py").exists()
    )


def test_a_move_never_reaches_outside_the_project_or_a_secret(suite, daemon):
    for to_path, status in (
        ("../outside.py", 400),
        (".env", 403),
        (".git/hooks/x.py", 400),
    ):
        with pytest.raises(ReadError) as refused:
            _move(
                daemon,
                suite,
                path="tests/test_all.py",
                start_line=1,
                end_line=4,
                to_path=to_path,
            )
        assert refused.value.status == status, to_path
    with pytest.raises(ReadError) as secret:
        _move(
            daemon, suite, path=".env", start_line=1, end_line=1, to_path="src/leak.py"
        )
    assert secret.value.status == 403
    assert (
        not (suite.parent / "outside.py").exists()
        and (suite / ".env").read_text() == "TOKEN=x\n"
    )


def test_only_an_emptied_file_is_deleted(suite, daemon):
    with pytest.raises(ReadError) as full:
        _delete(daemon, suite, "tests/test_all.py")
    assert full.value.status == 409 and "still has 24 lines" in full.value.message
    assert (suite / "tests" / "test_all.py").exists()

    _move(
        daemon,
        suite,
        path="tests/test_all.py",
        start_line=1,
        end_line=24,
        to_path="tests/test_moved.py",
    )
    reply = _delete(daemon, suite, "tests/test_all.py")

    assert (
        reply["deleted"] is True
        and reply["sha256_before"] == hashlib.sha256(TESTS.encode()).hexdigest()
    )
    assert not (suite / "tests" / "test_all.py").exists()
    assert (suite / "tests" / "test_moved.py").read_text() == TESTS


def test_finish_reports_created_and_deleted_files_and_puts_the_originals_back(
    suite, daemon
):
    original = (suite / "tests" / "test_all.py").read_bytes()
    _move(
        daemon,
        suite,
        path="tests/test_all.py",
        start_line=1,
        end_line=24,
        to_path="tests/split/test_moved.py",
    )
    _delete(daemon, suite, "tests/test_all.py")

    files = {f["path"]: f for f in _finish(daemon, suite)["files"]}

    assert files["tests/split/test_moved.py"]["created"] is True
    assert files["tests/split/test_moved.py"]["content"] == TESTS
    deleted = files["tests/test_all.py"]
    assert (
        deleted["deleted"] is True
        and deleted["content"] is None
        and deleted["changed"] is True
    )
    assert deleted["sha256_original"] == hashlib.sha256(original).hexdigest()
    assert all(f["restored"] for f in files.values())
    # Back as before the session: the original, and no created file or folder.
    assert (suite / "tests" / "test_all.py").read_bytes() == original
    assert not (suite / "tests" / "split").exists()


def test_a_file_created_then_deleted_in_one_session_is_nothing_to_deliver(
    suite, daemon
):
    _move(
        daemon,
        suite,
        path="tests/test_all.py",
        start_line=1,
        end_line=4,
        to_path="tests/tmp.py",
    )
    _move(
        daemon,
        suite,
        path="tests/tmp.py",
        start_line=1,
        end_line=4,
        to_path="tests/test_all.py",
        to_line=1,
    )
    _delete(daemon, suite, "tests/tmp.py")

    files = {f["path"]: f for f in _finish(daemon, suite)["files"]}

    assert files["tests/tmp.py"]["changed"] is False and not files["tests/tmp.py"].get(
        "deleted"
    )
    assert not (suite / "tests" / "tmp.py").exists()


def test_a_file_recreated_after_its_deletion_is_left_as_a_conflict(suite, daemon):
    _move(
        daemon,
        suite,
        path="tests/test_all.py",
        start_line=1,
        end_line=24,
        to_path="tests/test_moved.py",
    )
    _delete(daemon, suite, "tests/test_all.py")
    (suite / "tests" / "test_all.py").write_text("x = 'a person saved this'\n")

    files = {f["path"]: f for f in _finish(daemon, suite)["files"]}

    assert files["tests/test_all.py"]["conflict"] is True
    assert (
        suite / "tests" / "test_all.py"
    ).read_text() == "x = 'a person saved this'\n"


def test_an_unknown_operation_is_refused(suite, daemon):
    with pytest.raises(ReadError) as unknown:
        _edit(daemon, suite, op="rename", path="tests/test_all.py")
    assert unknown.value.status == 400
