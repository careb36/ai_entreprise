"""Acceptance tests of the word counter, written by a person.

They grade what the factory produced; the model under test never sees or
edits them. Each test is named after the criterion it decides
(``test_req_1_1_...`` decides REQ-1.1); a criterion passes when every test
named after it passes. The project under test is on ``PYTHONPATH``.
"""

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wordcount", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_req_1_1_counts_lines_words_and_chars():
    from wordcount import count

    assert count("one two\nthree\n") == {"lines": 2, "words": 3, "chars": 14}


def test_req_1_1_counts_a_last_line_without_newline():
    from wordcount import count

    assert count("a b\nc") == {"lines": 1, "words": 3, "chars": 5}


def test_req_1_2_empty_text_is_all_zero():
    from wordcount import count

    assert count("") == {"lines": 0, "words": 0, "chars": 0}


def test_req_2_1_prints_counts_and_name(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("one two\nthree\n", encoding="utf-8")
    result = _run(str(target))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"2 3 14 {target}"


def test_req_2_2_missing_file_fails_on_stderr(tmp_path):
    missing = tmp_path / "nowhere.txt"
    result = _run(str(missing))
    assert result.returncode == 1
    assert result.stdout == ""
    assert str(missing) in result.stderr
