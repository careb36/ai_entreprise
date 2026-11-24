"""The Verifier's ``tests_removed`` check (``services/evidence/tests_removed.py``).

Design: ``docs/design/move-and-delete-files.md`` (Residual risk). In sprint 6
(2026-11-24) a Developer delivered a 642-line test file with empty content
(``additions 0, deletions 642``) and rewrote two others from memory; nothing
counted the tests that went. The check counts test definitions on the removed
and added lines of the diffs the daemon observed, over the whole delivery: a
name lost holds the task for a person (``risk_tier`` high), a test moved to
another file of the delivery passes, a test file with no diff to count is
unverifiable. The diffs come from the daemon's own functions
(``vscode-daemon/file_diff.py``, ``workspace_move.deliver_delete``), run for
real against ``tmp_path``.
"""

from __future__ import annotations

import hashlib

import file_diff  # the daemon's modules, on sys.path via conftest
import workspace_move
from services.evidence import tests_removed
from verification_helpers import _ingest, _make_company, _payload, _verify

OLD = "".join(f"def test_{i}():\n    assert {i} == {i}\n\n\n" for i in range(4))


def _written(path: str, before: str | None, after: str) -> dict:
    """A ``written`` entry as the daemon's ``write_files`` reports it."""
    previous = None
    if before is not None:
        data = before.encode()
        previous = file_diff.Previous(hashlib.sha256(data).hexdigest(), data)
    return {
        "path": path,
        "status": "written",
        "sha256": hashlib.sha256(after.encode()).hexdigest(),
        **file_diff.describe_change(path, previous, after.encode()),
    }


def _deleted(tmp_path, path: str, content: str) -> dict:
    """A ``deleted`` entry from the daemon's own ``deliver_delete``."""
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    expected = hashlib.sha256(content.encode()).hexdigest()
    report = workspace_move.deliver_delete(
        tmp_path, target, {"delete": True, "sha256_expected": expected}, path
    )
    assert report["status"] == "deleted" and not target.exists()
    return report


def test_an_emptied_test_file_loses_its_tests_and_is_held_for_a_person():
    write = _written("backend/tests/test_image.py", OLD, "")
    assert write["additions"] == 0 and write["deletions"] > 0

    result = tests_removed.check({"workspace_writes": [write]})

    assert result["outcome"] == "pass" and result["held"] is True
    assert [t["name"] for t in result["removed"]] == [f"test_{i}" for i in range(4)]
    assert "test_0 (backend/tests/test_image.py)" in result["observation"]
    assert "test_0" in tests_removed.hold_reason(result)


def test_tests_rewritten_from_memory_lose_the_old_names():
    invented = "def test_something_else():\n    assert True\n"
    write = _written("tests/test_api.py", OLD, invented)

    result = tests_removed.check({"workspace_writes": [write]})

    assert sorted(t["name"] for t in result["removed"]) == [
        f"test_{i}" for i in range(4)
    ]


def test_tests_moved_to_another_file_of_the_delivery_pass(tmp_path):
    first, second = OLD[: len(OLD) // 2], OLD[len(OLD) // 2 :]
    writes = [
        _written("tests/test_a.py", None, first),
        _written("tests/test_b.py", "", second),
        _deleted(tmp_path, "tests/test_all.py", OLD),
    ]

    result = tests_removed.check({"workspace_writes": writes})

    assert result["outcome"] == "pass" and result["held"] is False
    assert result["removed"] == [] and tests_removed.hold_reason(result) is None
    assert "4 definition(s) removed, 4 added" in result["observation"]


def test_a_deleted_test_file_counts_its_tests(tmp_path):
    write = _deleted(tmp_path, "tests/test_all.py", OLD)
    assert write["change"] == "deleted" and write["previous_sha256"]

    result = tests_removed.check({"workspace_writes": [write]})

    assert result["held"] is True and len(result["removed"]) == 4


def test_an_emptied_file_that_is_not_a_test_file_is_not_counted():
    write = _written("backend/app/image.py", OLD, "")

    assert tests_removed.check({"workspace_writes": [write]}) is None


def test_a_test_file_without_a_diff_is_unverifiable():
    write = {**_written("tests/test_a.py", OLD, ""), "diff": None}
    write["diff_omitted"] = "reply_size"

    result = tests_removed.check({"workspace_writes": [write]})

    assert result["outcome"] == "unverifiable"
    assert result["uncounted"] == [
        {"path": "tests/test_a.py", "reason": "no diff (reply_size)"}
    ]


def test_an_empty_deleted_file_and_a_new_test_file_need_no_diff(tmp_path):
    new = {**_written("tests/test_new.py", None, OLD), "diff": None}
    new["diff_omitted"] = "reply_size"
    writes = [_deleted(tmp_path, "tests/test_empty.py", ""), new]

    result = tests_removed.check({"workspace_writes": writes})

    assert result["outcome"] == "pass" and result["held"] is False


def test_javascript_tests_and_a_changed_signature_line():
    before = "it('renders', () => {});\ntest.skip(\"loads\", () => {});\n"
    after = "it('renders', async () => {});\n"
    py_before = "class TestX:\n    def test_a(self):\n        pass\n"
    py_after = "class TestX:\n    async def test_a(self, client):\n        pass\n"
    writes = [
        _written("src/App.test.tsx", before, after),
        _written("tests/x_test.py", py_before, py_after),
    ]

    result = tests_removed.check({"workspace_writes": writes})

    assert [t["name"] for t in result["removed"]] == ["loads"]


def test_the_verifier_holds_a_delivery_that_lost_tests(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    run = _ingest(test_client, monkeypatch, tmp_path, _payload(company_id)).json()
    write = _written("backend/tests/test_image.py", OLD, "")

    body = _verify(
        test_client, run["id"], company_id, {"workspace_writes": [write]}
    ).json()

    checks = {c["name"]: c for c in body["checks"]}
    assert checks["tests_removed"]["held"] is True
    assert checks["risk_tier"]["tier"] == "high"
    assert any("tests: tests removed" in r for r in checks["risk_tier"]["reasons"])
