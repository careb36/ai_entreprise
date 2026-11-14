import json
import subprocess
import sys


def _salesjob(*args):
    return subprocess.run(
        [sys.executable, "-m", "salesjob", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(tmp_path, text):
    path = tmp_path / "sales.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_req_2_1_json_summary(tmp_path):
    result = _salesjob(
        _write(tmp_path, "region,product,amount\nnorth,tea,2.50\nsouth,tea,1\n")
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["bad_rows"] == 0


def test_req_2_2_bad_rows(tmp_path):
    result = _salesjob(
        _write(tmp_path, "region,product,amount\nnorth,tea,x\nnorth,tea,1\n,tea,2\n")
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["bad_rows"] == 2
    assert "line 2:" in result.stderr and "line 4:" in result.stderr


def test_req_2_3_unreadable_input(tmp_path):
    result = _salesjob(str(tmp_path / "missing.csv"))
    assert result.returncode == 1
    assert result.stdout == ""


def test_req_2_4_csv(tmp_path):
    result = _salesjob(
        _write(tmp_path, "region,product,amount\nsouth,tea,1\nnorth,tea,2\n"),
        "--format",
        "csv",
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "region,count,total",
        "north,1,2.00",
        "south,1,1.00",
    ]
