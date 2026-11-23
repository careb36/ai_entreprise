"""The documentation pack grader (``scripts/bench/docs_pack_grade.py``, issue
#698): a pack compiled from the shared snapshot (``project_docs_helpers``) is
graded against expectations written here for that snapshot, as a person
writes them for a reference specification. Documents, headings, texts and
completeness come from the documents; traceability and checks from the data
the matrix and the checks carry, so a pack compiled before that data is
unmeasured, never failed.
"""

import copy
import json
import sys
from pathlib import Path

import pytest
from project_docs_helpers import _docs, _snapshot

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import docs_pack_grade as grader  # scripts/bench/docs_pack_grade.py, by path


def _pack():
    docs, _ = _docs(_snapshot())
    return {"id": 9, "documents": list(docs.values())}


def _expect(**overrides):
    base = {
        "format": grader.EXPECTATIONS_FORMAT,
        "spec": "fixture",
        "written_by": "the test",
        "documents": {
            "traceability": {"contains": ["REQ-1.1"]},
            "charter": {},
        },
        # The snapshot's specification has REQ-1 (task 41, verified and
        # integrated) and REQ-2 (no task yet).
        "traceability": {"requirements": ["REQ-1", "REQ-2"]},
    }
    base.update(overrides)
    return base


def _outcomes(card):
    return {item["what"]: item["outcome"] for item in card["items"]}


def test_a_pack_that_holds_what_the_person_expected_passes():
    pack = _pack()
    checks_doc = next(d for d in pack["documents"] if d["key"] == "checks")
    results = {c["id"]: c["result"] for c in checks_doc["data"]["checks"]}
    some = dict(list(results.items())[:2])

    card = grader.grade(pack, _expect(checks=some))

    assert card["verdict"] == "pass", card["items"]
    assert card["totals"]["fail"] == 0 and card["score"] == 1.0
    assert card["format"] == grader.CARD_FORMAT and card["pack_id"] == 9


def test_a_requirement_without_a_task_and_a_missing_document_fail():
    card = grader.grade(
        _pack(),
        _expect(
            documents={"risk_register_v9": {}},
            traceability={
                "requirements": ["REQ-1", "REQ-2"],
                "every_requirement_has_a_task": True,
            },
        ),
    )
    outcomes = _outcomes(card)

    assert outcomes["document risk_register_v9"] == "fail"
    assert outcomes["traceability: REQ-1 has a task"] == "pass"
    assert outcomes["traceability: REQ-2 has a task"] == "fail"
    assert card["verdict"] == "fail" and card["score"] < 1


def test_a_requirement_the_person_did_not_list_fails_the_matrix():
    card = grader.grade(
        _pack(),
        _expect(
            traceability={
                "requirements": ["REQ-1"],
                "verified": True,
                "integrated": True,
            }
        ),
    )
    outcomes = _outcomes(card)
    assert outcomes["traceability: REQ-1 verified"] == "pass"
    assert outcomes["traceability: REQ-1 integrated"] == "pass"

    item = next(i for i in card["items"] if "beyond the expected" in i["what"])
    assert item["outcome"] == "fail" and "REQ-2" in item["detail"]


def test_a_heading_a_text_and_completeness_are_read_from_the_document():
    pack = _pack()
    trace = next(d for d in pack["documents"] if d["key"] == "traceability")
    first_heading = next(
        line for line in trace["markdown"].splitlines() if line.startswith("# ")
    )
    card = grader.grade(
        pack,
        _expect(
            documents={
                "traceability": {
                    "headings": [first_heading, "## Not there"],
                    "contains": ["T-41", "T-999"],
                    "complete": True,
                }
            },
            traceability={},
        ),
    )
    outcomes = _outcomes(card)

    assert outcomes[f"traceability: heading {first_heading!r}"] == "pass"
    assert outcomes["traceability: heading '## Not there'"] == "fail"
    assert outcomes["traceability: contains 'T-41'"] == "pass"
    assert outcomes["traceability: contains 'T-999'"] == "fail"
    # The matrix lists REQ-2 with no task: it is not complete.
    complete = next(i for i in card["items"] if i["what"] == "traceability: complete")
    assert complete["outcome"] == "fail" and "REQ-2" in complete["detail"]


def test_a_check_computed_otherwise_fails_with_both_results():
    pack = _pack()
    checks_doc = next(d for d in pack["documents"] if d["key"] == "checks")
    check = checks_doc["data"]["checks"][0]
    other = next(r for r in grader.RESULTS if r != check["result"])

    card = grader.grade(pack, _expect(traceability={}, checks={check["id"]: other}))

    item = next(i for i in card["items"] if i["what"] == f"check {check['id']}")
    assert item["outcome"] == "fail"
    assert repr(other) in item["detail"] and repr(check["result"]) in item["detail"]


def test_a_pack_compiled_before_the_data_is_unmeasured_not_failed():
    pack = copy.deepcopy(_pack())
    for doc in pack["documents"]:
        doc.pop("data", None)
    checks_doc = next(d for d in _pack()["documents"] if d["key"] == "checks")
    some = {checks_doc["data"]["checks"][0]["id"]: "met"}

    card = grader.grade(pack, _expect(documents={}, checks=some))

    assert {i["outcome"] for i in card["items"]} == {"unmeasured"}
    assert card["verdict"] == "unmeasured" and card["score"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"format": "something/else"},
        {"format": grader.EXPECTATIONS_FORMAT, "documents": []},
        {
            "format": grader.EXPECTATIONS_FORMAT,
            "documents": {"x": {"headings": "## A"}},
        },
        {"format": grader.EXPECTATIONS_FORMAT, "checks": {"x": "great"}},
    ],
)
def test_malformed_expectations_are_refused(bad):
    with pytest.raises(grader.InputError):
        grader.grade(_pack(), bad)


def test_the_command_writes_the_card_with_its_own_digests(tmp_path, capsys):
    pack_path, expect_path = tmp_path / "pack.json", tmp_path / "expect.json"
    pack_path.write_text(json.dumps(_pack()))
    expect_path.write_text(json.dumps(_expect()))
    out = tmp_path / "card.json"

    code = grader.main([str(pack_path), str(expect_path), "--out", str(out)])

    card = json.loads(out.read_text())
    assert code == grader.EXIT_PASS and card["verdict"] == "pass"
    assert len(card["pack_sha256"]) == 64 and len(card["expectations_sha256"]) == 64
    assert (
        out.with_suffix(".md")
        .read_text()
        .startswith("# Documentation pack grade: fixture")
    )
    assert "Verdict **pass**" in capsys.readouterr().out

    expect_path.write_text("{not json")
    assert grader.main([str(pack_path), str(expect_path)]) == grader.EXIT_INPUT
