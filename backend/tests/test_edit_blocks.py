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
from services.contracts import edit_blocks
from services.evidence import standards, verification
from services.loop.task_worker import parse_file_manifest
from services.tools import research_tools
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


def test_an_edit_block_with_text_outside_its_edits_is_refused_whole():
    """A second edit left unclosed must not let the first through alone."""
    body = (
        "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\ny = 1\n=======\ny = 2\n"
    )
    assert edit_blocks.parse(body) == []
    assert edit_blocks.parse(
        "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n\n"
    ) == [{"search": "x = 1\n", "replace": "x = 2\n"}]


def test_checks_read_what_an_edit_adds_not_what_it_removes():
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    removal = (
        "```python path=src/conf.py edit\n<<<<<<< SEARCH\n"
        f'KEY = "{secret}"\n=======\nKEY = os.environ["KEY"]\n>>>>>>> REPLACE\n```\n'
    )
    assert not standards.check_delivery(removal)["findings"] or all(
        f["standard"] != "STD-SEC-1"
        for f in standards.check_delivery(removal)["findings"]
    )
    addition = removal.replace(secret, "x").replace('os.environ["KEY"]', f'"{secret}"')
    assert any(
        f["standard"] == "STD-SEC-1"
        for f in standards.check_delivery(addition)["findings"]
    )
    # A package.json edit is a fragment: its dependencies are still read.
    manifest = parse_file_manifest(
        '```json path=package.json edit\n<<<<<<< SEARCH\n    "react": "18.2.0"\n=======\n'
        '    "react": "18.2.0",\n    "left-pad": "*",\n    "test": "vitest"\n'
        ">>>>>>> REPLACE\n```\n"
    )
    assert standards.unpinned_in(
        "package.json", edit_blocks.scanned_text(manifest[0])
    ) == ["left-pad@*"]
    assert research_tools.packages_in(manifest)["npm"] == {"react", "left-pad"}


def test_a_missing_search_text_names_the_closest_line_and_is_not_applied():
    original = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    with pytest.raises(file_edits.EditError) as excinfo:
        file_edits.apply_edits(
            original, [{"search": "def bb():\n    return 2\n", "replace": "x"}]
        )
    assert str(excinfo.value) == (
        "edit 1: the search text is not in the file; the closest line is 5: 'def b():'"
    )
    with pytest.raises(file_edits.EditError) as excinfo:
        file_edits.apply_edits(original, [{"search": "zzz qqq", "replace": "x"}])
    assert str(excinfo.value) == "edit 1: the search text is not in the file"


def test_apply_edits_needs_one_exact_match():
    assert (
        file_edits.apply_edits("a\nb\nc\n", [{"search": "b\n", "replace": "B\n"}])
        == "a\nB\nc\n"
    )
    with pytest.raises(file_edits.EditError, match="not in the file"):
        file_edits.apply_edits("a\n", [{"search": "z", "replace": "y"}])
    with pytest.raises(file_edits.EditError, match="appears 2 times"):
        file_edits.apply_edits("x\nx\n", [{"search": "x", "replace": "y"}])
    # Overlapping matches count: "pass\npass\n" starts at two places here.
    with pytest.raises(file_edits.EditError, match="appears 2 times"):
        file_edits.apply_edits(
            "pass\npass\npass\n", [{"search": "pass\npass\n", "replace": "x\n"}]
        )
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


def test_a_block_made_only_of_edits_is_read_as_edits_without_the_flag():
    """Run 9, task 56: the agent left out ``edit`` after the path; the block
    went to the daemon as the whole of contexts.json and failed to parse."""
    body = (
        '<<<<<<< SEARCH\n      "test_required_checks.py": [\n=======\n'
        '      "test_req_1.py": [\n        "loop"\n      ],\n'
        '      "test_required_checks.py": [\n>>>>>>> REPLACE\n'
    )
    files = parse_file_manifest(
        f"```json path=docs/architecture/contexts.json\n{body}```\n"
    )
    assert files[0]["edits"] == [
        {
            "search": '      "test_required_checks.py": [\n',
            "replace": '      "test_req_1.py": [\n        "loop"\n      ],\n'
            '      "test_required_checks.py": [\n',
        }
    ]
    # Text beside the markers keeps the block a whole file, as before.
    mixed = parse_file_manifest(f"```json path=a.json\n{{}}\n{body}```\n")
    assert "edits" not in mixed[0]


def test_the_flag_alone_on_the_first_line_is_the_flag():
    """Run 10, task 62: ``edit`` on the line after the opening fence."""
    body = 'edit\n<<<<<<< SEARCH\n  "b": 1\n=======\n  "a": 0,\n  "b": 1\n>>>>>>> REPLACE\n'
    files = parse_file_manifest(f"```json path=x.json\n{body}```\n")
    assert files[0]["edits"] == [
        {"search": '  "b": 1\n', "replace": '  "a": 0,\n  "b": 1\n'}
    ]
    # A malformed body after the flag line is still refused whole, not written.
    bad = parse_file_manifest("```json path=x.json\nedit\nnot an edit\n```\n")
    assert bad[0]["edits"] == []
    assert edit_blocks.split_flag_line("editor = 1\n") == (False, "editor = 1\n")


def test_an_edit_marker_body_is_never_written_as_the_whole_file():
    deliverable = (
        '```json path=src/en.json\n{\n  "edit": true\n}\n```\n'
        '```\n<<<<<<< SEARCH\n  "a": 1\n=======\n  "a": 2\n>>>>>>> REPLACE\n```\n'
    )
    [entry] = parse_file_manifest(deliverable)
    assert entry["edits"] == [{"search": '  "a": 1\n', "replace": '  "a": 2\n'}]
    # A marker with no edits after it is refused by the daemon, not written.
    [alone] = parse_file_manifest("```json path=src/es.json\nedit: true\n```\n")
    assert alone["edits"] == []
    # An ordinary small JSON file stays a whole file.
    [whole] = parse_file_manifest(
        '```json path=src/flags.json\n{"editor": true}\n```\n'
    )
    assert "edits" not in whole
