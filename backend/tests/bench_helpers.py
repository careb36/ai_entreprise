"""What the tests of the bench share (issue #316).

The reference specifications under ``bench/specs`` and a reader for them, and
a scorecard entry and card built from a board's comments. The tests are
``test_bench_specs.py``, ``test_bench_grading.py``, ``test_bench_scorecard.py``,
``test_bench_compare.py``, ``test_bench_routing.py``, ``test_bench_broker.py``,
``test_bench_graders.py`` and ``test_bench_repeated.py``.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import scorecard  # scripts/bench/scorecard.py, by path

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "bench"


SPEC_DIRS = sorted(
    p for p in (BENCH / "specs").iterdir() if (p / "spec.json").is_file()
)
TEST_DEF = re.compile(r"^def (test_\w+)\(", re.MULTILINE)


def _spec(spec_dir: Path) -> dict:
    return json.loads((spec_dir / "spec.json").read_text(encoding="utf-8"))


def _comment(author, text):
    return {"author": author, "text": text, "created_at": "2026-11-08T10:00:00"}


def _entry(
    name="01-a", status="completed", results=None, runs=None, rework=0, tokens=100
):
    results = results or {"REQ-1.1": "pass", "REQ-1.2": "pass"}
    asked = [_comment("QA", "NEEDS WORK: add a test.")] * rework
    items = [
        {"id": 1, "status": "done", "comments": asked},
        {"id": 2, "status": "done", "comments": []},
    ]
    return scorecard.summarize_spec(
        name=name,
        status=status,
        items=items,
        runs_per_task=runs or {1: 1, 2: 1},
        graded={
            "passed": sum(r == "pass" for r in results.values()),
            "total": len(results),
            "results": results,
        },
        factory_verified=2,
        wall_clock_seconds=12.34,
        tokens={"reported": tokens, "turns_reported": 4, "turns_unreported": 1},
        models={"Developer": ["fake-model", "fake-model"]},
    )


def _card(*entries, stamp="20260101T000000Z"):
    return scorecard.build_scorecard(
        list(entries) or [_entry()], stamp=stamp, commit="abc123", llm="fake"
    )
