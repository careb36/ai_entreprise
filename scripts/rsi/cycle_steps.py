"""The steps of a cycle as commands, each recorded by the runner (REQ-26, REQ-27).

``Steps`` runs every external command of ``scripts/rsi/cycle.py`` through one
function (``run_command``, which the tests replace), saves its output under
the cycle's folder and records the command, its exit status and the sha256 of
its output, all computed here. Every file the cycle keeps (scorecards, the
check-run listings, the target measure's reports, the decision) is hashed
here too, from its bytes, and listed with its hash; ``verify_kept`` hashes
them again, so a file changed after it was recorded is found.

The trees: B and C are each a clone of the repository (``git clone --shared``,
its own refs, never a worktree of the live checkout) checked out at the sha.
B's tree is where the graders come from: the target measure
(``scripts/self_host/``), the Verifier bench's cases (``bench/verifier/``) and
``scripts/rsi/decide.py``; the bench runs from each side's tree with
``run.sh --graders <B>``, so C's build is graded by B's tests (REQ-4).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The protected grader paths whose trees must be equal on B and C (rsi-loop
# §2.3, §3.1): the bench, the harness's measures, the decision and evidence.
GRADER_PATHS = (
    "bench",
    "scripts/bench",
    "scripts/self_host",
    "scripts/e2e",
    "scripts/rsi",
    "backend/app/services/evidence",
)
# Never passed to a command (``Steps.command``): the GitHub token.
WITHHELD_ENV = ("GITHUB_TOKEN", "GH_TOKEN")
STEP_TIMEOUT_SECONDS = 6 * 3600  # the design's wall-clock per cycle
GIT_TIMEOUT_SECONDS = 300
MEASURE_FAILED = 3  # self_host.py's exit status when the measure fails
_COLLECTED = re.compile(r"(\d+) tests? collected")
_VERIFIER_ROW = re.compile(r"^(\S+)\s+\S.*\s(ok|FAIL)$")

Runner = Callable[
    [Sequence[str], Path, Mapping[str, str] | None, int], tuple[int | None, str]
]


class CycleError(RuntimeError):
    """The cycle cannot go on; the message says why."""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_command(
    argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None, timeout: int
) -> tuple[int | None, str]:
    """Run one external command: its exit status (None when it did not end)
    and its standard output and error together."""
    try:
        done = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        said = "".join(
            x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
            for x in (exc.stdout, exc.stderr)
        )
        return None, f"{said}\n[stopped after {timeout} s]"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return done.returncode, done.stdout + done.stderr


def git(repo: Path, *args: str) -> str:
    """A read of git (or a clone into the cycle's folder); raises CycleError."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CycleError(f"git {args[0]} could not run: {exc}") from exc
    if done.returncode != 0:
        raise CycleError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()}")
    return done.stdout


def resolve(repo: Path, rev: str) -> str:
    """The full sha of the commit *rev* names, read from git."""
    if not rev or rev.startswith("-"):
        raise CycleError(f"not a revision: {rev!r}")
    return git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()


def file_at(repo: Path, sha: str, path: str) -> str:
    """A file's text at *sha*, from git's objects (never a working tree)."""
    return git(repo, "show", f"{sha}:{path}")


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    try:
        git(repo, "merge-base", "--is-ancestor", older, newer)
    except CycleError:
        return False
    return True


def make_tree(repo: Path, sha: str, dest: Path) -> Path:
    """A clone of *repo* at *sha* in *dest*, with refs of its own."""
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "clone", "--quiet", "--shared", "--no-checkout", str(repo), str(dest))
    git(dest, "checkout", "--quiet", "--detach", sha)
    if git(dest, "rev-parse", "HEAD").strip() != sha:
        raise CycleError(f"the tree at {dest} is not at {sha}")
    return dest


def grader_trees(repo: Path, sha: str) -> dict[str, Any]:
    """Git's tree id of each grader path at *sha*, and one digest over them."""
    trees: dict[str, str | None] = {}
    for path in GRADER_PATHS:
        try:
            trees[path] = git(repo, "rev-parse", f"{sha}:{path}").strip()
        except CycleError:
            trees[path] = None
    lines = "".join(f"{p}\t{t or '-'}\n" for p, t in trees.items())
    return {"trees": trees, "digest": hashlib.sha256(lines.encode()).hexdigest()}


class Steps:
    """The cycle's steps and kept files, each hashed here."""

    def __init__(self, work: Path, runner: Runner | None = None) -> None:
        self.work = work
        self.runner = runner or run_command
        self.steps: list[dict[str, Any]] = []
        self.kept: dict[str, str] = {}
        (work / "logs").mkdir(parents=True, exist_ok=True)

    def rel(self, path: Path) -> str:
        path = Path(path).resolve()
        try:
            return path.relative_to(self.work.resolve()).as_posix()
        except ValueError:
            return str(path)

    def keep(self, path: Path) -> str:
        """Hash *path* and list it among the kept files."""
        digest = sha256_file(path)
        self.kept[self.rel(path)] = digest
        return digest

    def write_json(self, path: Path, obj: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", "utf-8")
        self.keep(path)
        return path

    def command(
        self,
        name: str,
        argv: Sequence[str],
        cwd: Path,
        *,
        env: Mapping[str, str | None] | None = None,
        timeout: int = STEP_TIMEOUT_SECONDS,
    ) -> tuple[int | None, str]:
        """Run *argv*; record the command, its exit status and output's sha256.

        *env* holds what the step sets on top of this process's environment
        (``None`` unsets a name); only those names and values are recorded,
        never the rest of the environment.
        """
        # The API token never reaches a command: C's bench and measures run
        # the build under test, which is the party being recorded (B12).
        full = {k: v for k, v in os.environ.items() if k not in WITHHELD_ENV}
        if env:
            for key, value in env.items():
                if value is None:
                    full.pop(key, None)
                else:
                    full[key] = value
        started = now()
        exit_status, output = self.runner([str(a) for a in argv], cwd, full, timeout)
        log = self.work / "logs" / f"{name}.log"
        log.write_text(output, encoding="utf-8")
        self.steps.append(
            {
                "step": name,
                "command": [str(a) for a in argv],
                "cwd": self.rel(cwd),
                "env": {k: v for k, v in (env or {}).items()},
                "exit_status": exit_status,
                "output": self.rel(log),
                "output_sha256": self.keep(log),
                "started_at": started,
                "finished_at": now(),
            }
        )
        return exit_status, output

    def note(self, name: str, **fields: Any) -> dict[str, Any]:
        """A step that is not a command (a read of GitHub's API, a refusal)."""
        entry = {"step": name, "at": now(), **fields}
        self.steps.append(entry)
        return entry

    def kept_files(self) -> list[dict[str, str]]:
        return [{"path": p, "sha256": h} for p, h in sorted(self.kept.items())]


def verify_kept(work: Path, kept: list[dict[str, str]]) -> list[str]:
    """The kept files whose bytes no longer hash to what was recorded."""
    problems = []
    for entry in kept:
        path = Path(entry["path"])
        path = path if path.is_absolute() else work / path
        try:
            digest = sha256_file(path)
        except OSError as exc:
            problems.append(f"{entry['path']}: unreadable ({exc.strerror or exc})")
            continue
        if digest != entry["sha256"]:
            problems.append(
                f"{entry['path']}: sha256 {digest} differs from the recorded "
                f"{entry['sha256']}"
            )
    return problems


# ── The commands ──────────────────────────────────────────────────────────────


def measure_argv(
    b_tree: Path, build: Path, spec: str, python: str, report: Path
) -> list[str]:
    """B's copy of the harness runs B's target measure on *build* (REQ-4)."""
    return [
        python,
        str(b_tree / "scripts" / "self_host" / "self_host.py"),
        "--checkout",
        str(build),
        "--build",
        str(build),
        "--state",
        str(report.with_suffix(".state.json")),
        "--steps",
        "measure",
        "--spec",
        str(b_tree / spec),
        "--python",
        python,
        "--report",
        str(report),
    ]


def measure_result(exit_status: int | None) -> str:
    """The target's result from the exit status the runner observed."""
    if exit_status == 0:
        return "pass"
    if exit_status == MEASURE_FAILED:
        return "fail"
    return "error"


def bench_argv(tree: Path, out: Path, graders_sha: str) -> list[str]:
    """The side's bench, graded by B's tests (``run.sh --graders <B>``)."""
    return [
        str(tree / "scripts" / "bench" / "run.sh"),
        "--out",
        str(out),
        "--graders",
        graders_sha,
    ]


def verifier_argv(python: str) -> list[str]:
    return [python, "-m", "verifier.run"]


def collect_argv(python: str) -> list[str]:
    return [
        python,
        "-m",
        "pytest",
        "tests",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]


def parse_collected(output: str) -> int | None:
    """The number pytest says it collected; None when it did not say."""
    if re.search(r"\bno tests (collected|ran)\b", output):
        return 0
    found = _COLLECTED.findall(output)
    return int(found[-1]) if found else None


def parse_verifier(output: str) -> dict[str, str]:
    """Each case of ``verifier.run``'s table: ``pass`` when it behaved as
    specified (``ok``), ``fail`` otherwise."""
    cases = {}
    for line in output.splitlines():
        row = _VERIFIER_ROW.match(line.rstrip())
        if row and row.group(1) != "case":
            cases[row.group(1)] = "pass" if row.group(2) == "ok" else "fail"
    return cases


def cards_in(folder: Path) -> list[tuple[Path, Any]]:
    """The scorecards a bench run wrote into *folder* (``<stamp>.json``)."""
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            found.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            found.append((path, None))
    return found
