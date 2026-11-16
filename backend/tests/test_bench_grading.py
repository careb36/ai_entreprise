"""The bench's grade (issue #316): pytest's JUnit report read per criterion.

A report with a document type is refused before anything is expanded (issue
#522); the fake producer's files pass, an empty project fails, a wrong answer
fails only its criteria; the produced project cannot stand in for the grader
or forge its report, and a report pytest did not finish is not read.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import scorecard  # scripts/bench/scorecard.py, by path
from bench_helpers import BENCH

JUNIT = """<testsuites><testsuite>
<testcase classname="t" name="test_req_1_1_a"/>
<testcase classname="t" name="test_req_1_1_b[x]"/>
<testcase classname="t" name="test_req_1_2_a"><failure message="no"/></testcase>
<testcase classname="t" name="test_req_2_1_a"><skipped/></testcase>
<testcase classname="t" name="test_req_2_2_a"><error message="boom"/></testcase>
<testcase classname="t" name="test_helper"/>
</testsuite></testsuites>"""


def test_the_junit_report_is_read_per_criterion():
    criteria = ["REQ-1.1", "REQ-1.2", "REQ-2.1", "REQ-2.2", "REQ-3.1"]
    assert scorecard.criteria_from_junit(JUNIT, criteria) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
        "REQ-2.1": "fail",  # a skipped test decides nothing in the criterion's favour
        "REQ-2.2": "fail",
        "REQ-3.1": "missing",
    }


@pytest.mark.parametrize("text", ["", "not xml <", "<testsuites/>"])
def test_an_unreadable_or_empty_report_leaves_every_criterion_missing(text):
    assert scorecard.criteria_from_junit(text, ["REQ-1.1"]) == {"REQ-1.1": "missing"}


# The report is written inside a project the agents produced, so a conftest
# there can rewrite it (issue #522, Semgrep's use-defused-xml): a document type
# is refused before any entity is expanded or any file is read.
_BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<testsuites><testsuite><testcase name="test_req_1_1_&lol3;"/></testsuite></testsuites>"""


def _external(path: Path) -> str:
    return f"""<?xml version="1.0"?>
<!DOCTYPE t [<!ENTITY secret SYSTEM "file://{path}">]>
<testsuites><testsuite><testcase name="test_req_1_1_a">&secret;</testcase>
</testsuite></testsuites>"""


def test_a_report_with_a_document_type_is_refused_before_expansion(tmp_path):
    import junit_xml

    secret = tmp_path / "secret.txt"
    secret.write_text("never read")
    for text in (
        _BOMB,
        _external(secret),
        '<!DOCTYPE t SYSTEM "http://example.invalid/t.dtd"><testsuites/>',
    ):
        with pytest.raises(junit_xml.JUnitError):
            junit_xml.testcases(text)
        assert scorecard.criteria_from_junit(text, ["REQ-1.1"]) == {
            "REQ-1.1": "missing"
        }
    # An entity no DTD declares is malformed XML, not an empty name.
    with pytest.raises(junit_xml.JUnitError):
        junit_xml.testcases('<testcase name="test_req_1_1_&x;"/>')


def test_the_junit_reader_looks_only_at_direct_children_of_a_case():
    import junit_xml

    text = """<testsuites><testsuite>
<testcase name="test_req_1_1_a"><system-out><failure/></system-out></testcase>
<testcase name="test_req_1_2_a"><failure/></testcase>
</testsuite></testsuites>"""
    assert junit_xml.testcases(text) == [
        ("test_req_1_1_a", True),
        ("test_req_1_2_a", False),
    ]


def test_grading_the_fake_producers_files_passes_every_criterion():
    spec_dir = BENCH / "specs" / "02-lib-roman"
    graded = scorecard.grade(spec_dir, spec_dir / "fake" / "files", sys.executable)
    assert (graded["passed"], graded["total"]) == (5, 5), graded["output_tail"]


def test_grading_an_empty_project_fails_every_criterion(tmp_path):
    graded = scorecard.grade(
        BENCH / "specs" / "01-cli-wordcount", tmp_path, sys.executable
    )
    assert graded["passed"] == 0
    assert set(graded["results"].values()) == {"fail"}


def test_grading_a_wrong_answer_fails_only_its_criteria(tmp_path):
    (tmp_path / "romannum").mkdir()
    source = (
        BENCH / "specs" / "02-lib-roman" / "fake" / "files" / "romannum" / "__init__.py"
    ).read_text()
    # A reader that accepts IIII: only the criterion that refuses it fails.
    broken = source.replace("or to_roman(total) != s", "")
    (tmp_path / "romannum" / "__init__.py").write_text(broken)
    graded = scorecard.grade(BENCH / "specs" / "02-lib-roman", tmp_path, sys.executable)
    assert [c for c, r in graded["results"].items() if r != "pass"] == ["REQ-2.2"]


FORGED_REPORT = """import sys

# A grader impostor: writes a JUnit report where every criterion passes.
for arg in sys.argv:
    if arg.startswith("--junitxml="):
        cases = "".join(
            f'<testcase classname="x" name="test_req_{r}_{c}_forged"/>' for r in (1, 2) for c in (1, 2)
        )
        open(arg.split("=", 1)[1], "w").write(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
sys.exit(0)
"""


def test_a_project_cannot_stand_in_for_the_grader(tmp_path):
    # The old grader (project on PYTHONPATH, then `-m pytest`) ran this
    # pytest.py instead of pytest and took its report: 4 of 4.
    (tmp_path / "pytest.py").write_text(FORGED_REPORT)
    (tmp_path / "sitecustomize.py").write_text(FORGED_REPORT)
    (tmp_path / "usercustomize.py").write_text("raise SystemExit(0)\n")
    (tmp_path / "conftest.py").write_text("raise SystemExit(0)\n")
    graded = scorecard.grade(
        BENCH / "specs" / "01-cli-wordcount", tmp_path, sys.executable
    )
    assert graded["exit_code"] == 1, graded["output_tail"]
    assert set(graded["results"].values()) == {"fail"}


def test_the_project_comes_after_the_standard_library(tmp_path):
    # A json.py in the project does not replace the one pytest's report needs.
    (tmp_path / "json.py").write_text("raise SystemExit(0)\n")
    (tmp_path / "romannum").mkdir()
    source = (
        BENCH / "specs" / "02-lib-roman" / "fake" / "files" / "romannum" / "__init__.py"
    )
    (tmp_path / "romannum" / "__init__.py").write_text(source.read_text())
    graded = scorecard.grade(BENCH / "specs" / "02-lib-roman", tmp_path, sys.executable)
    assert (graded["passed"], graded["total"]) == (5, 5), graded["output_tail"]


def test_a_report_pytest_did_not_finish_is_not_read(tmp_path):
    # A python that writes a passing report and exits 2 (interrupted).
    fake = tmp_path / "python"
    fake.write_text(
        f"#!{sys.executable}\n" + FORGED_REPORT.replace("sys.exit(0)", "sys.exit(2)")
    )
    fake.chmod(0o755)
    graded = scorecard.grade(BENCH / "specs" / "01-cli-wordcount", tmp_path, str(fake))
    assert graded["exit_code"] == 2
    assert set(graded["results"].values()) == {"missing"}
