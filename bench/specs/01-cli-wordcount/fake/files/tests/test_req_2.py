import subprocess
import sys


def _wordcount(*args):
    return subprocess.run([sys.executable, "-m", "wordcount", *args], capture_output=True, text=True)


def test_req_2_1_prints_the_counts(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text("one two\nthree\n", encoding="utf-8")
    result = _wordcount(str(target))
    assert result.returncode == 0
    assert result.stdout.strip() == f"2 3 14 {target}"


def test_req_2_2_missing_file(tmp_path):
    result = _wordcount(str(tmp_path / "none.txt"))
    assert result.returncode == 1
    assert result.stdout == ""
