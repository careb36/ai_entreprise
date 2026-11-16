"""What the tests of ``scripts/rsi/decide.py`` share (rsi-loop §8, PR 7).

``cycle_files(folder)`` writes every file of a cycle the script keeps: three
real-model scorecards per side (the committed fake reference relabelled with
a real ``llm`` and the side's commit), the check-runs, the test counts and
the Verifier bench per side, the fake card on C, the graders, the protected
match and the target report. The tests are ``test_rsi_decide.py`` and
``test_rsi_decide_inputs.py``.
"""

import copy
import json
import sys
from pathlib import Path

from fitness_helpers import B_SHA, C_SHA

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "rsi" / "decide.py"
sys.path.insert(0, str(SCRIPT.parent))
import decide  # scripts/rsi/decide.py, by path

REFERENCE = json.loads(
    (REPO / "bench" / "scorecards" / "20261114T201804Z.json").read_text("utf-8")
)
REAL_LLM = "http://llm-proxy:5100"


def card(sha, index, *, llm=REAL_LLM, **totals):
    found = copy.deepcopy(REFERENCE)
    found["commit"] = sha
    found["llm"] = llm
    found["stamp"] = f"20261116T0{index}0000Z"
    found["totals"].update(totals)
    return found


def listing(sha, conclusion="success", total=None):
    runs = [
        {
            "id": i,
            "name": name,
            "head_sha": sha,
            "status": "completed",
            "conclusion": conclusion if name == "backend-tests" else "success",
            "app": {"slug": "github-actions"},
        }
        for i, name in enumerate(("backend-tests", "frontend-build"), start=1)
    ]
    return {"total_count": len(runs) if total is None else total, "check_runs": runs}


def write(folder: Path, name: str, obj) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def cycle_files(folder: Path) -> dict:
    """Every file of a cycle decide.py keeps, by option name."""
    paths = {}
    for side, sha in (("baseline", B_SHA), ("candidate", C_SHA)):
        for i in range(3):
            write(folder / side, f"card-{i}.json", card(sha, i))
        paths[f"{side}_cards"] = folder / side
        paths[f"{side}_checks"] = write(folder, f"{side}-ci.json", listing(sha))
        paths[f"{side}_tests"] = write(
            folder, f"{side}-tests.json", {"sha": sha, "suites": {"backend": 4000}}
        )
        paths[f"{side}_verifier"] = write(
            folder, f"{side}-verifier.json", {"sha": sha, "cases": {"cors": "pass"}}
        )
    paths["fake_card"] = write(folder, "fake.json", card(C_SHA, 9, llm="fake"))
    paths["graders"] = write(
        folder, "graders.json", {"baseline_tree": "t1", "candidate_tree": "t1"}
    )
    paths["protected"] = write(
        folder, "protected.json", {"sha": C_SHA, "touched": [], "approval": None}
    )
    paths["target"] = write(
        folder,
        "target.json",
        {
            "measure": "f4-adr-files",
            "baseline": {"sha": B_SHA, "result": "fail"},
            "candidate": {"sha": C_SHA, "result": "pass"},
        },
    )
    return paths


def argv(paths):
    found = ["--baseline-sha", B_SHA, "--candidate-sha", C_SHA]
    for key, value in paths.items():
        values = value if isinstance(value, list) else [value]
        found += [f"--{key.replace('_', '-')}", *map(str, values)]
    return found


def run(paths, capsys):
    code = decide.main(argv(paths))
    return code, json.loads(capsys.readouterr().out)


def codes(out):
    return {r["code"] for r in out["reasons"]}
