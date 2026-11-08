"""SEARCH/REPLACE edits in a deliverable and the syntax check before writing
(issue #359; ideas from Aider and SWE-agent): the manifest parser reads a
block marked ``edit`` as edits; the backend expects no hash for it (only the
daemon knows the result); the daemon applies each edit only when its search
text appears exactly once, refuses a missing or ambiguous one, and writes a
``.py`` or ``.json`` file only when it still parses (whole files too); the
Verifier names the daemon's reason when a file was not written."""

from __future__ import annotations

import hashlib

import file_edits
import pytest
from services import verification
from services.task_worker import parse_file_manifest
from test_task_delivery import _load_daemon

EDIT_DELIVERABLE = """Changed the greeting.

```python path=src/app.py edit
<<<<<<< SEARCH
def greet():
    return "hi"
=======
def greet(name):
    return f"hi {name}"
>>>>>>> REPLACE
```

```python path=src/new.py
X = 1
```
"""


def test_the_manifest_reads_an_edit_block_as_edits():
    files = {f["path"]: f for f in parse_file_manifest(EDIT_DELIVERABLE)}
    assert files["src/app.py"]["edits"] == [
        {
            "search": 'def greet():\n    return "hi"\n',
            "replace": 'def greet(name):\n    return f"hi {name}"\n',
        }
    ]
    assert (
        "<<<<<<< SEARCH" in files["src/app.py"]["content"]
    )  # scanners and QA see the edit
    assert "edits" not in files["src/new.py"]
    # "edit" inside the path is not the flag; an edit block with no edit stays visible.
    weird = parse_file_manifest(
        "```python path=src/edit.py\nX = 1\n```\n```python path=a.py edit\nno blocks\n```\n"
    )
    by_path = {f["path"]: f for f in weird}
    assert "edits" not in by_path["src/edit.py"] and by_path["a.py"]["edits"] == []


def test_apply_edits_needs_one_exact_match():
    assert (
        file_edits.apply_edits("a\nb\nc\n", [{"search": "b\n", "replace": "B\n"}])
        == "a\nB\nc\n"
    )
    with pytest.raises(file_edits.EditError, match="not in the file"):
        file_edits.apply_edits("a\n", [{"search": "z", "replace": "y"}])
    with pytest.raises(file_edits.EditError, match="appears 2 times"):
        file_edits.apply_edits("x\nx\n", [{"search": "x", "replace": "y"}])
    with pytest.raises(file_edits.EditError, match="no edits"):
        file_edits.apply_edits("a", [])
    with pytest.raises(file_edits.EditError, match="empty search"):
        file_edits.apply_edits("a", [{"search": "", "replace": "b"}])


def test_the_syntax_check_parses_without_running():
    assert file_edits.syntax_error("a.py", "def f():\n    return 1\n") is None
    assert "SyntaxError at line 1" in file_edits.syntax_error("a.py", "def f(:\n")
    # Parsing never executes: a file that would raise at import still passes.
    assert file_edits.syntax_error("a.py", "raise SystemExit('never run')\n") is None
    assert "invalid JSON" in file_edits.syntax_error("a.json", "{bad")
    assert file_edits.syntax_error("a.ts", "this is not checked {") is None


def test_the_daemon_applies_edits_and_refuses_what_would_break(tmp_path):
    daemon = _load_daemon()
    (tmp_path / "src").mkdir()
    app = tmp_path / "src" / "app.py"
    app.write_text('def greet():\n    return "hi"\n', encoding="utf-8")
    files = parse_file_manifest(EDIT_DELIVERABLE)
    report = {r["path"]: r for r in daemon.write_files(str(tmp_path), files)}
    assert (
        report["src/app.py"]["status"] == "written"
        and report["src/app.py"]["edits_applied"] == 1
    )
    assert app.read_text() == 'def greet(name):\n    return f"hi {name}"\n'
    assert (
        report["src/app.py"]["sha256"] == hashlib.sha256(app.read_bytes()).hexdigest()
    )
    assert report["src/new.py"]["status"] == "written"
    # The same edit again no longer matches: refused, the file untouched.
    again = daemon.write_files(str(tmp_path), [files[0]])[0]
    assert again["status"] == "rejected_edit" and "not in the file" in again["error"]
    assert app.read_text().startswith("def greet(name)")
    # An edit on a missing file, and an edit or a whole file that would not parse.
    missing = daemon.write_files(
        str(tmp_path), [{"path": "nope.py", "edits": [{"search": "a", "replace": "b"}]}]
    )[0]
    assert missing["status"] == "rejected_edit" and "existing file" in missing["error"]
    broken = daemon.write_files(
        str(tmp_path),
        [
            {
                "path": "src/app.py",
                "edits": [{"search": "def greet(name):", "replace": "def greet(name:"}],
            }
        ],
    )[0]
    assert broken["status"] == "rejected_syntax" and app.read_text().startswith(
        "def greet(name)"
    )
    whole = daemon.write_files(
        str(tmp_path), [{"path": "bad.json", "content": "{nope"}]
    )[0]
    assert whole["status"] == "rejected_syntax" and not (tmp_path / "bad.json").exists()


def test_the_verifier_names_the_daemons_reason():
    outcome, observation = verification.evaluate_workspace_writes(
        ["src/app.py"],
        {
            "workspace_writes": [
                {
                    "path": "src/app.py",
                    "status": "rejected_edit",
                    "error": "edit 1: the search text is not in the file",
                }
            ]
        },
    )
    assert outcome == verification.VERDICT_REFUTED
    assert (
        observation
        == "src/app.py rejected_edit: edit 1: the search text is not in the file"
    )
