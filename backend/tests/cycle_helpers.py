"""What the tests of ``scripts/rsi/cycle.py`` share (rsi-loop §8, PR 8).

``make_repo(tmp)`` builds a small git repository with a baseline B (B's
copies of the protected list, the test map, CODEOWNERS and the CI check
script, a specification with its target measure) and a candidate C on top of
it. ``FakeRunner`` stands for every external command of a cycle: the target
measure, the bench (writing scorecards graded by the commit ``--graders``
names), the Verifier bench and the test collection; ``decide.py`` runs for
real, in-process. ``FakeGitHub`` replaces the one function that reaches the
network (``cycle_github.request``). No test makes a network call.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from decide_helpers import REAL_LLM, card, decide, listing

REPO = Path(__file__).resolve().parents[2]
RSI = REPO / "scripts" / "rsi"
if str(RSI) not in sys.path:
    sys.path.insert(0, str(RSI))
import cycle  # scripts/rsi/cycle.py, by path
import cycle_github
import cycle_record
import cycle_steps

SPEC = "scripts/self_host/specs/demo-specification.json"
MEASURE = "scripts/self_host/measures/test_demo.py"
B_COPIES = (
    "backend/app/services/contracts/protected_paths.py",
    "docs/architecture/contexts.json",
    ".github/CODEOWNERS",
    "scripts/protected_paths_check.py",
)
SPEC_TEXT = json.dumps(
    {
        "title": "Demo finding",
        "target_measure": MEASURE,
        "requirements": [{"id": "REQ-1", "text": "The system shall lint."}],
    },
    indent=2,
)
GIT_ENV = {
    "GIT_AUTHOR_NAME": "cycle-test",
    "GIT_AUTHOR_EMAIL": "cycle-test@example.invalid",
    "GIT_COMMITTER_NAME": "cycle-test",
    "GIT_COMMITTER_EMAIL": "cycle-test@example.invalid",
}


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return done.stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp: Path, change: str = "backend/app/services/tools/demo.py"):
    """A repository with B and C; C changes *change*. Returns (repo, B, C)."""
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for path in B_COPIES:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / path, target)
    _write(repo, SPEC, SPEC_TEXT)
    _write(repo, MEASURE, "def test_demo():\n    assert False\n")
    _write(repo, "bench/README.md", "the bench\n")
    b_sha = commit(repo, "B")
    _write(repo, change, "VALUE = 1\n")
    c_sha = commit(repo, "C")
    return repo, b_sha, c_sha


def findings_file(tmp: Path, *, reverted: bool = False) -> Path:
    from services.contracts.specification_text import specification_sha256

    outcomes = [{"decision": "revert", "reasons": []}] if reverted else []
    rows = [
        {
            "id": 7,
            "source_ref": "F5",
            "title": "Demo finding",
            "status": "reverted" if reverted else "open",
            "specification_sha256": specification_sha256(SPEC_TEXT),
            "outcomes": outcomes,
        }
    ]
    path = tmp / "findings.json"
    path.write_text(json.dumps({"findings": rows}), encoding="utf-8")
    return path


class FakeRunner:
    """Every external command of a cycle, faked; ``decide.py`` for real."""

    def __init__(self, b_sha: str, c_sha: str) -> None:
        self.shas = {"B": b_sha, "C": c_sha}
        self.measure = {"B": cycle_steps.MEASURE_FAILED, "C": 0}
        self.graders_sha: str | None = None  # None: the commit --graders names
        self.calls: list[list[str]] = []
        self.runs = {"B": 0, "C": 0}

    def __call__(self, argv, cwd, env, timeout):
        argv = list(argv)
        self.calls.append(argv)
        if argv[1].endswith("decide.py"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = decide.main(argv[2:])
            return status, out.getvalue()
        if argv[1].endswith("self_host.py"):
            side = Path(argv[argv.index("--build") + 1]).name
            status = self.measure[side]
            report = Path(argv[argv.index("--report") + 1])
            report.write_text(json.dumps({"measure": {"exit_code": status}}), "utf-8")
            return status, f"measure on {side}: exit {status}\n"
        if argv[0].endswith("run.sh"):
            return self._bench(argv, Path(cwd).name, env)
        if "verifier.run" in argv:
            return 0, (
                "case                 verdict        expected\n"
                + "-" * 84
                + "\ntests_pass_but_they_failed  refuted   refuted  ok\n"
                + "honest_control              verified  verified  ok\n"
            )
        if "--collect-only" in argv:
            return 0, "4000 tests collected in 3.21s\n"
        raise AssertionError(f"unexpected command {argv}")

    def _bench(self, argv, side, env):
        out = Path(argv[argv.index("--out") + 1])
        graders = argv[argv.index("--graders") + 1]
        fake = env is not None and not env.get("BENCH_LLM_URL")
        index = 9 if fake else self.runs[side]
        if not fake:
            self.runs[side] += 1
        found = card(self.shas[side], index, llm="fake" if fake else REAL_LLM)
        found["graders"] = {
            "sha": self.graders_sha or graders,
            "trees": {"bench": "a" * 40, "scripts/bench": "b" * 40},
            "sha256": "e" * 64,
        }
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{found['stamp']}.json").write_text(json.dumps(found), "utf-8")
        (out / f"{found['stamp']}.md").write_text("card\n", "utf-8")
        return 0, f"scorecard written to {out}\n"


class FakeGitHub:
    """``cycle_github.request``, answered from data; every call recorded."""

    def __init__(self, c_sha: str, heads: list[str] | None = None) -> None:
        self.heads = list(heads or [c_sha, c_sha])
        self.listings: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method, url, body=None):
        self.calls.append((method, url, body))
        if method == "PATCH":
            return 200, {"state": "closed"}
        if "/check-runs" in url:
            sha = url.split("/commits/")[1].split("/")[0]
            return 200, self.listings.get(sha, listing(sha))
        if url.split("?")[0].endswith("/reviews"):
            return 200, []
        if "/pulls/" in url:
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return 200, {"head": {"sha": head}}
        raise AssertionError(f"unexpected call {method} {url}")


def argv(repo: Path, tmp: Path, c_sha: str | None, *extra: str, **opts) -> list[str]:
    """The ``run`` command line for a cycle in *tmp*."""
    findings = opts["findings"] if "findings" in opts else findings_file(tmp)
    found = [
        "run",
        "--repo", str(repo),
        "--baseline", "main~1",
        "--spec", SPEC,
        "--cycle-id", "cycle-1-demo",
        "--work", str(tmp / "work"),
        "--out", str(tmp / "cycle.json"),
        "--repository", "example/la-fabrique",
        "--python", sys.executable,
    ]  # fmt: skip
    if findings is not None:
        found += ["--findings", str(findings)]
    if c_sha:
        found += ["--candidate", c_sha]
    return found + list(extra)


def run_cycle(monkeypatch, tmp, repo, c_sha, runner, gh, *extra, **opts):
    """Run the cycle with the fakes; (exit status, the record written)."""
    monkeypatch.setenv("BENCH_LLM_URL", "http://llm-proxy:5100")
    monkeypatch.setattr(cycle_github, "request", gh)
    status = cycle.main(argv(repo, tmp, c_sha, *extra, **opts), runner=runner)
    return status, json.loads((tmp / "cycle.json").read_text("utf-8"))


__all__ = [
    "RSI",
    "SPEC",
    "FakeGitHub",
    "FakeRunner",
    "commit",
    "cycle",
    "cycle_github",
    "cycle_record",
    "cycle_steps",
    "findings_file",
    "git",
    "make_repo",
    "run_cycle",
]
