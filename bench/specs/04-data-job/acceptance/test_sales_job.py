"""Acceptance tests of the sales summary job, written by a person.

They grade what the factory produced; the model under test never sees or
edits them. Each test is named after the criterion it decides
(``test_req_1_1_...`` decides REQ-1.1); a criterion passes when every test
named after it passes. The project under test is on ``PYTHONPATH``.
"""

import csv
import io
import json
import subprocess
import sys

import pytest

GOOD = "region,product,amount\nnorth,tea,2.50\nnorth,coffee,3\nsouth,tea,1.25\n"
# Lines 3 and 5 are bad: an amount that is not a number, an empty region.
TWO_BAD = "region,product,amount\nnorth,tea,2.50\nnorth,coffee,abc\nsouth,tea,1.25\n,tea,4.00\n"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "salesjob", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _file(tmp_path, text, name="sales.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_req_1_1_sums_per_region():
    from salesjob.summary import summarize

    assert summarize(GOOD) == {
        "regions": {
            "north": {"count": 2, "total": "5.50"},
            "south": {"count": 1, "total": "1.25"},
        },
        "total": "6.75",
        "rows": 3,
        "bad": [],
    }


def test_req_1_1_columns_in_any_order_and_exact_decimals():
    from salesjob.summary import summarize

    text = "amount,region,product\n0.10,east,a\n0.20,east,b\n0.70,east,c\n"
    result = summarize(text)
    assert result["regions"] == {"east": {"count": 3, "total": "1.00"}}
    assert result["total"] == "1.00"


def test_req_1_2_bad_rows_are_listed_not_summed():
    from salesjob.summary import summarize

    text = (
        "region,product,amount\n"
        "north,tea,2.50\n"  # 2: good
        "north,coffee,abc\n"  # 3: not a number
        "south,tea,-1.00\n"  # 4: negative
        ",tea,4.00\n"  # 5: empty region
        "south,tea\n"  # 6: missing field
        "south,tea,1.25\n"  # 7: good
    )
    result = summarize(text)
    assert result["regions"] == {
        "north": {"count": 1, "total": "2.50"},
        "south": {"count": 1, "total": "1.25"},
    }
    assert result["total"] == "3.75"
    assert result["rows"] == 2
    assert [b["line"] for b in result["bad"]] == [3, 4, 5, 6]
    assert all(isinstance(b["reason"], str) and b["reason"] for b in result["bad"])


@pytest.mark.parametrize("text", ["region,product,price\nnorth,tea,1\n", ""])
def test_req_1_3_no_amount_column_is_refused(text):
    from salesjob.summary import summarize

    with pytest.raises(ValueError):
        summarize(text)


def test_req_2_1_prints_the_json_summary(tmp_path):
    result = _run(str(_file(tmp_path, GOOD)))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "regions": {
            "north": {"count": 2, "total": "5.50"},
            "south": {"count": 1, "total": "1.25"},
        },
        "total": "6.75",
        "rows": 3,
        "bad_rows": 0,
    }


def test_req_2_2_bad_rows_on_stderr_and_exit_2(tmp_path):
    result = _run(str(_file(tmp_path, TWO_BAD)))
    assert result.returncode == 2
    summary = json.loads(result.stdout)
    assert summary["bad_rows"] == 2
    assert summary["rows"] == 2
    assert summary["total"] == "3.75"
    reported = [line for line in result.stderr.splitlines() if line.startswith("line ")]
    assert [line.split(":")[0] for line in reported] == ["line 3", "line 5"]


@pytest.mark.parametrize("case", ["missing", "no-amount"])
def test_req_2_3_unreadable_input_exits_1(tmp_path, case):
    if case == "missing":
        target = tmp_path / "nowhere.csv"
    else:
        target = _file(tmp_path, "region,product,price\nnorth,tea,1\n")
    result = _run(str(target))
    assert result.returncode == 1
    assert result.stdout == ""
    # Names the path, and for a file that was read, the column it lacks.
    assert str(target) in result.stderr
    if case == "no-amount":
        assert "amount" in result.stderr


def test_req_2_4_csv_format(tmp_path):
    text = "region,product,amount\nsouth,tea,1.25\nnorth,tea,2.50\nnorth,coffee,3\n"
    result = _run(str(_file(tmp_path, text)), "--format", "csv")
    assert result.returncode == 0, result.stderr
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows == [
        ["region", "count", "total"],
        ["north", "2", "5.50"],
        ["south", "1", "1.25"],
    ]
