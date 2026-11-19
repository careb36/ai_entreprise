"""The /bench page's history of committed scorecards (issue #685, split from
#316): ``GET /v1/bench/scorecards`` and ``services/models/bench_scorecards.py``.

EARS criteria held here:

- WHEN the folder holds cards, the endpoint SHALL list every one, newest first
  by stamp, with the folder it came from (the fake references, ``real/``,
  ``ablation/``), and SHALL NOT read an ablation's own arms.
- WHEN a card matches the schema, its entry SHALL carry the card's totals and
  per-specification numbers as written, and the per-section prompt weight
  when the card has it.
- IF a card does not match the schema, THEN it SHALL be listed ``invalid`` with
  the schema's problems and no numbers; IF it cannot be read (broken JSON, a
  symbolic link, too large), THEN ``unreadable`` with the reason. Never dropped.
- WHERE the card says a metric was not measured (a grade the sandbox could not
  give, no turn that reported usage, a cost unknown, no prompt measured, a
  field older than the card's version), the entry SHALL say ``measured:
  false`` with the reason, never a 0.
- WHEN a v5 card holds a ``humaneval`` block, its entry SHALL carry pass@1,
  the counts, the dataset and each problem's outcome; WHERE a problem's grade
  could not run, its outcome and pass@1 SHALL be ``measured: false``, never 0;
  a card without the block SHALL carry ``humaneval: null``.
- WHEN the folder is missing, the endpoint SHALL answer ``available: false``.
- The endpoint SHALL refuse an anonymous caller when authentication is on.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from config import settings
from services.models import bench_scorecards

SHA = "0123456789abcdef0123456789abcdef01234567"


def _spec(
    name="01-cli", *, results=None, unmeasured=None, turns_reported=8, routing=None
):
    results = results if results is not None else {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    passed = sum(1 for r in results.values() if r == "pass")
    criteria = {"passed": passed, "total": len(results), "results": results}
    if unmeasured is not None:
        criteria.update({"isolation": "container", "unmeasured": unmeasured})
    return {
        "name": name,
        "status": "completed",
        "error": None,
        "criteria": criteria,
        "factory_criteria_verified": 2,
        "tasks": {"total": 4, "done": 4, "blocked": 0, "done_first_attempt": 3},
        "rework_requests": 1,
        "rework_per_task": 0.25,
        "blocked_share": 0.0,
        "first_attempt_rate": 0.75,
        "wall_clock_seconds": 70.0,
        "tokens": {
            "reported": 13538 if turns_reported else 0,
            "turns_reported": turns_reported,
            "turns_unreported": 2,
        },
        "models": {"Developer": ["fake-model"]},
        "routing": routing
        if routing is not None
        else [
            {
                "difficulty": 3,
                "model": "fake-model",
                "tasks": 4,
                "done": 4,
                "done_first_attempt": 3,
                "blocked": 0,
                "rework": 1,
                "rework_per_task": 0.25,
                "blocked_share": 0.0,
                "first_attempt_rate": 0.75,
                "tokens": {
                    "reported": 13538,
                    "turns_reported": 8,
                    "turns_unreported": 2,
                },
            }
        ],
        "prompt": _prompt(),
    }


def _prompt(prompts=4):
    return {
        "prompts": prompts,
        "prompt_sections_missing": 0 if prompts else 3,
        "tokens_estimated": 6854 if prompts else 0,
        "tokens_mean": 1713.5 if prompts else 0.0,
        "system_tokens_estimated": 102 if prompts else 0,
        "sections": {
            "repository": {"tokens": 762, "prompts": 4, "share": 0.1111},
            "standards_and_procedures": {"tokens": 3078, "prompts": 4, "share": 0.4487},
        }
        if prompts
        else {},
        "cut_prompts": 1 if prompts else 0,
        "cut": {"related_code": 1} if prompts else {},
        "related_code_prompts": 0,
    }


def _card(stamp="20261114T201804Z", specs=None, version=3):
    specs = specs if specs is not None else [_spec()]
    passed = sum(s["criteria"]["passed"] for s in specs)
    total = sum(s["criteria"]["total"] for s in specs)
    card = {
        "schema_version": version,
        "stamp": stamp,
        "commit": SHA,
        "llm": "fake",
        "policy": "default",
        "specs": specs,
        "totals": {
            "specs": len(specs),
            "specs_completed": len(specs),
            "criteria_passed": passed,
            "criteria_total": total,
            "criteria_rate": round(passed / total, 4) if total else 0.0,
            "tasks": 4 * len(specs),
            "done_first_attempt": 3 * len(specs),
            "first_attempt_rate": 0.75,
            "rework_per_task": 0.25,
            "blocked_share": 0.0,
            "wall_clock_seconds": 70.0 * len(specs),
            "tokens_reported": sum(s["tokens"]["reported"] for s in specs),
            "prompt": _prompt(),
        },
    }
    if version == 1:
        del card["policy"]
        del card["totals"]["prompt"]
        for spec in specs:
            spec.pop("routing", None)
            spec.pop("prompt", None)
    return card


def _write(folder: Path, name: str, card) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(
        card if isinstance(card, str) else json.dumps(card), encoding="utf-8"
    )
    return path


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BENCH_REPOSITORY_DIR", str(tmp_path))
    return tmp_path


def _cards_dir(root: Path) -> Path:
    return root / "bench" / "scorecards"


def _by_file(body):
    return {c["file"]: c for c in body["cards"]}


def test_lists_every_card_newest_first_from_each_folder(repo, test_client):
    cards = _cards_dir(repo)
    _write(cards, "20261108T140751Z.json", _card("20261108T140751Z", version=1))
    _write(cards, "20261114T201804Z.json", _card("20261114T201804Z"))
    _write(cards / "real", "20261116T020000Z.json", _card("20261116T020000Z"))
    _write(cards / "ablation", "20261115T000000Z.json", _card("20261115T000000Z"))
    _write(
        cards / "ablation" / "arms" / "r1" / "memory", "20261115T010000Z.json", _card()
    )
    (cards / "20261114T201804Z.md").write_text("# not a card", encoding="utf-8")

    response = test_client.get("/v1/bench/scorecards")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert [(c["folder"], c["stamp"]) for c in body["cards"]] == [
        ("real", "20261116T020000Z"),
        ("ablation", "20261115T000000Z"),
        ("", "20261114T201804Z"),
        ("", "20261108T140751Z"),
    ]
    assert all(c["status"] == "valid" for c in body["cards"])


def test_a_valid_card_carries_its_numbers_as_written(repo, test_client):
    _write(_cards_dir(repo), "20261114T201804Z.json", _card())

    card = test_client.get("/v1/bench/scorecards").json()["cards"][0]

    assert card["problems"] == []
    assert (card["schema_version"], card["commit"], card["llm"], card["policy"]) == (
        3,
        SHA,
        "fake",
        "default",
    )
    totals = card["totals"]
    assert totals["criteria"] == {
        "measured": True,
        "value": {"passed": 1, "total": 2, "rate": 0.5},
    }
    assert totals["tokens"] == {
        "measured": True,
        "value": 13538,
        "turns_reported": 8,
        "turns_unreported": 2,
    }
    assert (totals["rework_per_task"], totals["first_attempt_rate"]) == (0.25, 0.75)
    spec = card["specs"][0]
    assert spec["name"] == "01-cli"
    assert spec["results"] == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    assert spec["criteria"]["value"] == {"passed": 1, "total": 2, "rate": 0.5}
    assert spec["tasks"] == {
        "total": 4,
        "done": 4,
        "blocked": 0,
        "done_first_attempt": 3,
    }
    assert spec["prompt"]["value"]["tokens_mean"] == 1713.5
    assert card["prompt_sections"] == [
        {
            "section": "standards_and_procedures",
            "tokens": 3078,
            "prompts": 4,
            "share": 0.4487,
            "cut": 0,
        },
        {
            "section": "repository",
            "tokens": 762,
            "prompts": 4,
            "share": 0.1111,
            "cut": 0,
        },
        {"section": "related_code", "tokens": 0, "prompts": 0, "share": 0, "cut": 1},
    ]


def test_unmeasured_metrics_say_so_and_never_read_as_zero(repo, test_client):
    graded_out = _spec(
        "02-ungraded",
        results={"REQ-1.1": "missing"},
        unmeasured="the sandbox could not run",
        turns_reported=0,
    )
    graded_out["prompt"] = _prompt(prompts=0)
    card = _card(specs=[_spec(), graded_out])
    card["totals"]["prompt"] = _prompt(prompts=0)
    _write(_cards_dir(repo), "20261114T201804Z.json", card)

    entry = test_client.get("/v1/bench/scorecards").json()["cards"][0]

    assert entry["status"] == "valid"
    ungraded = entry["specs"][1]
    assert ungraded["criteria"] == {
        "measured": False,
        "reason": "grade_unmeasured",
        "detail": "the sandbox could not run",
    }
    assert ungraded["tokens"]["measured"] is False
    assert ungraded["tokens"]["reason"] == "no_reported_turns"
    assert "value" not in ungraded["tokens"]
    assert ungraded["prompt"] == {
        "measured": False,
        "reason": "no_prompt_measure",
        "detail": 3,
    }
    # The bench prices no turn: a routing row without cost_cents is unknown.
    assert entry["specs"][0]["cost_cents"] == {
        "measured": False,
        "reason": "cost_unknown",
        "detail": 1,
    }
    assert entry["totals"]["criteria"] == {
        "measured": False,
        "reason": "grade_unmeasured",
        "detail": ["02-ungraded"],
    }
    assert entry["totals"]["prompt"]["measured"] is False
    assert entry["prompt_sections"] is None


def test_a_cost_on_every_routing_row_is_measured(repo, test_client):
    spec = _spec()
    spec["routing"][0].update({"cost_cents": 12.5, "cost_unknown": 0})
    _write(_cards_dir(repo), "20261114T201804Z.json", _card(specs=[spec]))

    entry = test_client.get("/v1/bench/scorecards").json()["cards"][0]

    assert entry["specs"][0]["cost_cents"] == {"measured": True, "value": 12.5}


def test_fields_a_version_does_not_hold_are_not_in_the_card(repo, test_client):
    _write(
        _cards_dir(repo), "20261108T140751Z.json", _card("20261108T140751Z", version=1)
    )

    entry = test_client.get("/v1/bench/scorecards").json()["cards"][0]

    assert entry["status"] == "valid"
    assert entry["policy"] is None
    spec = entry["specs"][0]
    assert spec["cost_cents"] == {"measured": False, "reason": "not_in_card"}
    assert spec["prompt"] == {"measured": False, "reason": "not_in_card"}
    assert entry["totals"]["prompt"] == {"measured": False, "reason": "not_in_card"}
    assert entry["prompt_sections"] is None


def _he_row(task: int, outcome: str, reason=None, turns_reported=2) -> dict:
    return {
        "task_id": f"HumanEval/{task}",
        "spec": f"he-{task:03d}",
        "entry_point": "has_close_elements",
        "status": "completed",
        "outcome": outcome,
        "reason": reason,
        "isolation": "sandbox",
        "error": None,
        "tasks": {"total": 1, "done": 1, "blocked": 0, "done_first_attempt": 1},
        "rework_requests": 0,
        "wall_clock_seconds": 42.0,
        "tokens": {
            "reported": 900 if turns_reported else 0,
            "turns_reported": turns_reported,
            "turns_unreported": 1,
        },
        "models": {},
    }


def _he_card(rows, skipped=()) -> dict:
    graded = len(rows)
    count = {k: sum(r["outcome"] == k for r in rows) for k in ("pass", "fail")}
    unmeasured = graded - count["pass"] - count["fail"]
    card = _card(version=5)
    card["humaneval"] = {
        "dataset": {
            "name": "HumanEval",
            "source": "example/HumanEval.jsonl.gz",
            "sha256": "c" * 64,
            "license": "MIT",
            "problems": 164,
        },
        "subset": [r["task_id"] for r in rows],
        "problems": rows,
        "skipped": list(skipped),
        "graded": graded,
        "passed": count["pass"],
        "failed": count["fail"],
        "unmeasured": unmeasured,
        "pass_at_1": round(count["pass"] / graded, 4)
        if graded and not unmeasured
        else None,
        "tokens_reported": sum(r["tokens"]["reported"] for r in rows),
        "turns_unreported": sum(r["tokens"]["turns_unreported"] for r in rows),
    }
    return card


def test_a_humaneval_block_carries_pass_at_1_and_each_outcome(repo, test_client):
    card = _he_card([_he_row(0, "pass"), _he_row(1, "fail")], skipped=["HumanEval/2"])
    _write(_cards_dir(repo), "20261114T201804Z.json", card)

    entry = test_client.get("/v1/bench/scorecards").json()["cards"][0]

    assert entry["status"] == "valid", entry["problems"]
    he = entry["humaneval"]
    assert he["pass_at_1"] == {"measured": True, "value": 0.5}
    assert (he["graded"], he["passed"], he["failed"], he["unmeasured"]) == (2, 1, 1, 0)
    assert he["dataset"] == {
        "name": "HumanEval",
        "source": "example/HumanEval.jsonl.gz",
        "sha256": "c" * 64,
        "license": "MIT",
    }
    assert he["skipped"] == ["HumanEval/2"]
    assert he["tokens"] == {
        "measured": True,
        "value": 1800,
        "turns_reported": 4,
        "turns_unreported": 2,
    }
    assert [p["outcome"] for p in he["problems"]] == [
        {"measured": True, "value": "pass"},
        {"measured": True, "value": "fail"},
    ]
    assert he["problems"][0]["wall_clock_seconds"] == 42.0
    # The problems are not reference specifications.
    assert [s["name"] for s in entry["specs"]] == ["01-cli"]


def test_an_unmeasured_humaneval_problem_is_never_a_zero(repo, test_client):
    rows = [
        _he_row(0, "pass"),
        _he_row(3, "unmeasured", "the sandbox could not run", turns_reported=0),
    ]
    _write(_cards_dir(repo), "20261114T201804Z.json", _he_card(rows))

    he = test_client.get("/v1/bench/scorecards").json()["cards"][0]["humaneval"]

    assert he["pass_at_1"] == {
        "measured": False,
        "reason": "grade_unmeasured",
        "detail": ["HumanEval/3"],
    }
    unmeasured = he["problems"][1]
    assert unmeasured["outcome"] == {
        "measured": False,
        "reason": "grade_unmeasured",
        "detail": "the sandbox could not run",
    }
    assert unmeasured["tokens"]["measured"] is False
    assert "value" not in unmeasured["tokens"]


def test_humaneval_with_no_problem_graded_and_cards_without_it(repo, test_client):
    cards = _cards_dir(repo)
    _write(cards, "20261114T201804Z.json", _he_card([]))
    _write(cards, "20261108T140751Z.json", _card("20261108T140751Z"))

    entries = _by_file(test_client.get("/v1/bench/scorecards").json())

    he = entries["20261114T201804Z.json"]["humaneval"]
    assert he["pass_at_1"] == {"measured": False, "reason": "no_problems_graded"}
    assert he["tokens"]["measured"] is False
    assert entries["20261108T140751Z.json"]["humaneval"] is None


def test_a_humaneval_block_on_an_older_card_is_invalid(repo, test_client):
    card = _he_card([_he_row(0, "pass")])
    card["schema_version"] = 4
    _write(_cards_dir(repo), "20261114T201804Z.json", card)

    entry = test_client.get("/v1/bench/scorecards").json()["cards"][0]

    assert entry["status"] == "invalid"
    assert entry["problems"] == ["humaneval needs schema v5"]


def test_invalid_and_unreadable_cards_are_listed_with_their_problems(repo, test_client):
    cards = _cards_dir(repo)
    broken = _card("20261110T000000Z")
    broken["specs"][0]["criteria"]["passed"] = 2  # its results hold one pass
    del broken["totals"]["tokens_reported"]
    _write(cards, "20261110T000000Z.json", broken)
    _write(cards, "20261111T000000Z.json", "{ not json")
    _write(cards, "20261112T000000Z.json", json.dumps([1, 2]))
    target = _write(repo / "elsewhere", "card.json", _card("20261113T000000Z"))
    (cards / "20261113T000000Z.json").symlink_to(target)

    body = test_client.get("/v1/bench/scorecards").json()
    entries = _by_file(body)

    assert len(body["cards"]) == 4
    invalid = entries["20261110T000000Z.json"]
    assert invalid["status"] == "invalid"
    assert "specs[0].criteria.passed does not match its results" in invalid["problems"]
    assert "totals.tokens_reported is missing" in invalid["problems"]
    assert "totals" not in invalid and "specs" not in invalid
    assert entries["20261111T000000Z.json"]["status"] == "unreadable"
    assert entries["20261111T000000Z.json"]["problems"][0].startswith("not JSON")
    assert entries["20261112T000000Z.json"]["status"] == "invalid"
    assert entries["20261112T000000Z.json"]["problems"] == [
        "the scorecard is not an object"
    ]
    assert entries["20261113T000000Z.json"]["status"] == "unreadable"
    assert entries["20261113T000000Z.json"]["problems"] == [
        "a symbolic link is not read"
    ]


def test_a_card_over_the_size_cap_is_unreadable(repo, monkeypatch):
    monkeypatch.setattr(bench_scorecards, "MAX_CARD_BYTES", 10)
    _write(_cards_dir(repo), "20261114T201804Z.json", _card())

    entry = bench_scorecards.history(repo)["cards"][0]

    assert entry["status"] == "unreadable"
    assert entry["problems"] == ["larger than 10 bytes"]


def test_a_missing_folder_is_not_available(repo, test_client):
    body = test_client.get("/v1/bench/scorecards").json()

    assert body == {"available": False, "cards": []}


def test_the_committed_cards_are_all_valid():
    root = Path(__file__).resolve().parents[2]

    body = bench_scorecards.history(root)

    assert body["available"] is True
    assert body["cards"], "the repository holds reference cards"
    assert {c["status"] for c in body["cards"]} == {"valid"}, [
        (c["file"], c["problems"]) for c in body["cards"]
    ]


def test_an_anonymous_caller_is_refused_when_auth_is_on(repo, test_client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    _write(_cards_dir(repo), "20261114T201804Z.json", _card())

    assert test_client.get("/v1/bench/scorecards").status_code == 401


def test_the_entry_does_not_change_the_card():
    card = _card()
    before = copy.deepcopy(card)

    bench_scorecards.summarize(card)

    assert card == before
