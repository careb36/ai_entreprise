"""QA sees every file of a delivery, and the Developer prompt is cache-friendly."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from services.loop import review_digest
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import execute as worker_execute


def _file(path: str, lines: int) -> dict[str, str]:
    return {
        "path": path,
        "content": "\n".join(f"line {i} of {path}" for i in range(lines)),
    }


def test_every_file_is_listed_and_headed_within_the_budget():
    targets = [
        _file("src/a.py", 400),
        _file("src/b.py", 300),
        _file("tests/test_a.py", 200),
    ]
    digest = review_digest.build(targets, "Implemented both modules.")
    assert review_digest.is_digest(digest)
    assert len(digest) <= review_digest.DIGEST_CHARS + 200
    assert re.findall(r"# Artifact: (\S+)", digest) == [
        "src/a.py",
        "src/b.py",
        "tests/test_a.py",
    ]
    assert (
        "- src/a.py (400 lines," in digest and "- tests/test_a.py (200 lines," in digest
    )
    assert "Developer's note: Implemented both modules." in digest
    # Each file shows its start and says how much was left out.
    for path in ("src/a.py", "src/b.py", "tests/test_a.py"):
        assert f"line 0 of {path}" in digest
    assert digest.count("more line(s) not shown here") == 3


def test_a_small_delivery_is_shown_whole():
    targets = [_file("src/a.py", 5)]
    digest = review_digest.build(targets)
    assert "line 4 of src/a.py" in digest and "not shown here" not in digest
    assert "Developer's note" not in digest


def test_the_old_cut_left_files_out_and_the_digest_does_not():
    """The board keeps 4000 characters and QA used to read 3000 of them."""
    targets = [_file("src/a.py", 200), _file("src/b.py", 200), _file("src/c.py", 200)]
    old = "\n\n".join(f"# Artifact: {t['path']}\n\n{t['content']}" for t in targets)[
        :4000
    ][:3000]
    assert re.findall(r"# Artifact: (\S+)", old) == ["src/a.py"]
    new = worker_deliver._build_review_payload("prose", targets)[
        : review_digest.DIGEST_CHARS
    ]
    assert re.findall(r"# Artifact: (\S+)", new) == ["src/a.py", "src/b.py", "src/c.py"]


def test_text_without_files_goes_to_review_as_it_was():
    assert (
        worker_deliver._build_review_payload("  plain answer  ", []) == "plain answer"
    )
    assert not review_digest.is_digest("plain answer")


def test_the_developer_prompt_runs_from_stable_to_variable(monkeypatch):
    seen = {}

    class _Client:
        def __init__(self, base_url=None):
            pass

        def chat(self, messages, model, role, bu_id=None, tools=None):
            seen["prompt"] = messages[-1]["content"]
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(worker_execute, "LLMProxyClient", _Client)
    item = {
        "id": 1,
        "title": "Login",
        "description": "Build it",
        "comments": [
            {"author": "Dev", "text": "# Artifact: a.py\nprint(1)"},
            {"author": "QA", "text": "NEEDS WORK: add a test"},
        ],
    }
    worker_execute._execute_task(
        item,
        {"role": "Developer", "model": "m"},
        "S1",
        memory_context="memory text",
        completed_tasks_context="done text",
        architect_advice="brief text",
        lessons_context="lessons text",
        procedures_context="\n\n## Company Procedures\nprocedure text",
    )
    prompt = seen["prompt"]
    order = [
        "## Output format for files",
        "## Company Procedures",
        "## Lessons from Recent Sprints",
        "## Task: Login",
        "## Memory: Relevant Past Decisions",
        "## Sprint Context: Already Completed Tasks",
        "## Architect Guidance",
        "## Review feedback to address",
    ]
    positions = [prompt.index(section) for section in order]
    assert positions == sorted(positions)
    assert prompt.startswith("## Output format for files")


def _artifact(targets, prose="Done."):
    blocks = "\n\n".join(
        f"```python path={t['path']}\n{t['content']}\n```" for t in targets
    )
    return f"{prose}\n\n{blocks}"


def test_the_digest_never_exceeds_its_limit_so_no_later_cut_hides_a_file():
    """Five files used to overflow 3800 and QA's second cut fell mid-file."""
    targets = [_file(f"src/m{i}.py", 400) for i in range(5)]
    digest = review_digest.build(targets, "x" * 1000)
    assert len(digest) <= review_digest.DIGEST_CHARS
    assert re.findall(r"# Artifact: (\S+)", digest) == [t["path"] for t in targets]
    many = [_file(f"src/f{i}.py", 50) for i in range(60)]
    assert len(review_digest.build(many)) <= review_digest.DIGEST_CHARS


def test_qa_reads_the_run_s_whole_files_when_the_artifact_holds_them_all():
    targets = [_file("src/a.py", 150), _file("tests/test_a.py", 120)]
    board = review_digest.build(targets, "prose")
    view = review_digest.for_review(board, _artifact(targets))
    assert "line 149 of src/a.py" in view and "line 119 of tests/test_a.py" in view
    assert "not shown here" not in view
    assert len(view) <= review_digest.REVIEW_CHARS


def test_qa_keeps_the_board_digest_when_the_artifact_misses_a_listed_file():
    """A file written with the edit tool is not in the answer's artifact."""
    targets = [_file("src/a.py", 150), _file("src/edited.py", 40)]
    board = review_digest.build(targets)
    assert review_digest.for_review(board, _artifact(targets[:1])) == board
    assert review_digest.for_review(board, None) == board
    assert (
        review_digest.for_review("plain answer", _artifact(targets)) == "plain answer"
    )
