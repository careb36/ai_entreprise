"""What the tests of the watch after a merge share (rsi-loop §1.6, REQ-19).

``make_repo`` builds a small git repository in ``tmp_path`` with a real merge
commit (the cycle's), a later commit on ``main`` and an unrelated root, so the
ancestry the watch verifies is git's, not a fixture's word. ``record`` and
``card`` build a valid cycle record and nightly scorecards from the committed
fake reference (relabelled with a real ``llm``, the card's own commit and
stamp). ``run_watch`` runs ``scripts/rsi/watch.py`` over them and reads its
JSON back. The tests are ``test_rsi_cycle_record.py``, ``test_rsi_watch.py``
and ``test_rsi_watch_revert.py``.
"""

import copy
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "rsi"))
import cycle_record  # scripts/rsi/cycle_record.py, by path
import revert  # scripts/rsi/revert.py, by path
import watch  # scripts/rsi/watch.py, by path
from decide_helpers import REAL_LLM, REFERENCE

MERGED_AT = "2026-11-15T12:00:00Z"
NIGHT_1 = "20261116T021700Z"
NIGHT_2 = "20261117T021700Z"
BEFORE_MERGE = "20261114T021700Z"
# The reference card's served models (repeated.served_models over its specs).
SERVED = {"Developer": ["fake-model"], "QA": ["fake-model"]}


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Watch Test",
            "-c",
            "user.email=watch@example.com",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def make_repo(tmp_path: Path) -> dict:
    """A repository with the cycle merged: its shas by role.

    ``merged`` is a real merge commit on ``main``, ``after`` a later one and
    the tip ``origin/main`` names, ``branch`` a commit on a feature branch
    descended from ``after`` that never reached main, ``other`` an unrelated
    root (no ancestor but itself), ``root`` the first commit (the graders' sha
    in the records the tests build).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "f.txt").write_text("1", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "root")
    root = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "side")
    (repo / "f.txt").write_text("2", encoding="utf-8")
    git(repo, "commit", "-am", "the cycle's change")
    git(repo, "checkout", "main")
    (repo / "g.txt").write_text("main", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "work on main")
    git(repo, "merge", "--no-ff", "side", "-m", "Merge the cycle")
    merged = git(repo, "rev-parse", "HEAD")
    (repo / "h.txt").write_text("after", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "after the merge")
    after = git(repo, "rev-parse", "HEAD")
    # The trusted tip of main, as the workflow's full checkout holds it.
    git(repo, "update-ref", "refs/remotes/origin/main", after)
    git(repo, "checkout", "-b", "feature")
    (repo / "i.txt").write_text("feature", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "a feature branch after the merge")
    branch = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--orphan", "other")
    git(repo, "commit", "-m", "an unrelated root")
    other = git(repo, "rev-parse", "HEAD")
    return {
        "repo": repo,
        "root": root,
        "merged": merged,
        "after": after,
        "branch": branch,
        "other": other,
    }


def summary(median, spread, runs=3):
    return {"median": median, "spread": spread, "runs": runs}


def baseline_totals(**over):
    """B's totals summaries; a plain reference card regresses on none."""
    found = {
        "criteria_rate": summary(0.9, 0.02),
        "first_attempt_rate": summary(0.8, 0.04),
        "blocked_share": summary(0.1, 0.02),
        "rework_per_task": summary(0.5, 0.1),
        "tokens_reported": summary(100000, 20000),
        "wall_clock_seconds": summary(1000.0, 100.0),
    }
    found.update(over)
    return found


def baseline_spec(**over):
    return baseline_totals(**over)


def record(shas: dict, **over) -> dict:
    """A cycle record that validates; *over* replaces its top-level fields."""
    found = {
        "schema_version": cycle_record.SCHEMA_VERSION,
        "cycle_id": "cycle-1-folder-lint",
        "merged_sha": shas["merged"],
        "merged_at": MERGED_AT,
        "graders_sha": shas["root"],
        "models": copy.deepcopy(SERVED),
        "baseline_sha": shas["root"],
        "pull_request": 630,
        "baseline": {"totals": baseline_totals()},
    }
    found.update(over)
    return found


def card(sha: str, stamp: str, *, llm: str = REAL_LLM, **totals) -> dict:
    """A nightly card of the reference's shape on *sha*, made at *stamp*."""
    found = copy.deepcopy(REFERENCE)
    found["commit"] = sha
    found["llm"] = llm
    found["stamp"] = stamp
    found["totals"].update(totals)
    return found


def cut_run(card: dict) -> dict:
    """The card with a run block the token budget cut (schema-valid)."""
    card["run"] = {
        "status": "budget_exceeded",
        "budget": 300000,
        "tokens_spent": 300001,
        "turns_reported": 10,
        "turns_unreported": 0,
        "skipped": [],
    }
    return card


def write_card(folder: Path, found: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{found['stamp']}.json"
    path.write_text(json.dumps(found), encoding="utf-8")
    return path


def run_watch(tmp_path: Path, shas: dict, rec: dict, cards: list[dict], capsys):
    """Write the record and the cards, run watch.py; (exit status, JSON)."""
    record_path = tmp_path / "cycle.json"
    record_path.write_text(json.dumps(rec), encoding="utf-8")
    folder = tmp_path / "cards"
    paths = [str(write_card(folder, one)) for one in cards]
    argv = ["--cycle", str(record_path), "--repo", str(shas["repo"])]
    if paths:
        argv += ["--cards", *paths]
    code = watch.main(argv)
    return code, json.loads(capsys.readouterr().out)


def two_ok_nights(shas: dict) -> list[dict]:
    return [card(shas["after"], NIGHT_1), card(shas["after"], NIGHT_2)]


def revert_argv(tmp_path: Path, shas: dict, rec: dict, cards: list[dict]) -> list[str]:
    """Write the record and the cards; revert.py's argv over them."""
    record_path = tmp_path / "cycle.json"
    record_path.write_text(json.dumps(rec), encoding="utf-8")
    folder = tmp_path / "cards"
    paths = [str(write_card(folder, one)) for one in cards]
    argv = ["--cycle", str(record_path), "--repo", str(shas["repo"])]
    if paths:
        argv += ["--cards", *paths]
    return argv


class FakeRunner:
    """Records the git and gh commands; answers canned results by substring."""

    def __init__(self, results: dict[str, tuple[int, str]] | None = None):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.results = results or {}

    def __call__(self, cmd, cwd=None, input_text=None):
        self.calls.append(list(cmd))
        self.inputs.append(input_text)
        joined = " ".join(map(str, cmd))
        for key, (code, out) in self.results.items():
            if key in joined:
                return subprocess.CompletedProcess(cmd, code, out, "")
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    def ran(self, *fragment: str) -> bool:
        return any(all(part in call for part in fragment) for call in self.calls)

    def call_with(self, *fragment: str) -> list[str] | None:
        return next(
            (call for call in self.calls if all(part in call for part in fragment)),
            None,
        )


def run_revert(tmp_path: Path, shas: dict, rec: dict, cards: list[dict], runner=None):
    """Run revert.py over the record and cards with an injected runner."""
    return revert.main(revert_argv(tmp_path, shas, rec, cards), runner or FakeRunner())
