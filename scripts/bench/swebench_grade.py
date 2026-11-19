#!/usr/bin/env python3
"""The grade of a SWE-bench instance the factory worked on (issue #687).

    swebench_grade.py grade --spec DIR --project DIR --work DIR --out FILE

The scorer runs the instance's tests itself; the factory's report is never
read. The steps follow SWE-bench's own harness (``SWE-bench/SWE-bench``, MIT:
the test files the test patch touches are reset to the base, the patch is
applied, the named tests are read back, and an instance is resolved when
every FAIL_TO_PASS and every PASS_TO_PASS test passes):

1. **The environment** is built from the recipe for the instance's
   repository and version (``ENVIRONMENTS``, after the harness's
   specifications): a virtual environment on the interpreter the recipe
   names, its packages, then the repository at its base commit installed.
2. **The environment check.** The base, with the test patch applied, must
   reproduce the instance: every PASS_TO_PASS test passes and at least one
   FAIL_TO_PASS test does not. An environment that cannot be built or does
   not reproduce the instance makes the instance ``unmeasured``, with the
   reason: never a pass and never a fail, since the factory did not cause it.
3. **The delivery.** The project is copied, the test patch's files reset to
   the base and the patch applied, the copy installed in the same
   environment without its dependencies, and the tests run. A delivery that
   cannot be installed (the base could), that adds or changes a file that
   runs inside the test run (``conftest.py``, ``sitecustomize.py``, a
   ``pytest`` module, a ``.pth``), or whose run ends without a report fails.

The tests run on this machine, in that environment: the delivered code runs
with the runner's files and network (``isolation: host_venv``), like
``BENCH_GRADE_IN_PROCESS=1``, which ``run.sh --swebench`` requires. A
container per repository, as SWE-bench's harness has, is not built yet.

Writes a grade shaped like ``scorecard.py grade``'s (REQ-1.1 is "resolved")
with the details under ``swebench``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from junit_xml import PASS, XFAIL, JUnitError, cases

ISOLATION = "host_venv"
CRITERION = "REQ-1.1"
FAIL, MISSING = "fail", "missing"
INSTANCE_FILE = "instance.json"
BUILD_SECONDS = 900
TEST_SECONDS = 600
SHOWN = 20  # test ids listed per group that did not pass
SKIPPED_DIRS = {".git", "__pycache__", ".pytest_cache"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}
# Files that run inside the test run: a delivery may not add or change them.
RUNS_IN_GRADE = re.compile(
    r"(^|/)(conftest\.py|sitecustomize\.py|usercustomize\.py|pytest\.py)$|\.pth$"
)

# Per repository and version: the interpreter, the packages pinned before
# the repository is installed, and how it is installed. Taken from SWE-bench's
# harness specifications for these versions; a pair not listed is unmeasured.
_FLASK = {
    "python": "3.11",
    "packages": [
        "setuptools==70.0.0",
        "Werkzeug==2.3.7",
        "Jinja2==3.1.2",
        "itsdangerous==2.1.2",
        "click==8.1.3",
        "MarkupSafe==2.1.3",
        "pytest==7.4.4",
        "python-dotenv==1.0.0",
    ],
    "install": "editable",
}
_PYTEST_8 = {
    "python": "3.9",
    "packages": [
        "iniconfig==2.0.0",
        "packaging==23.1",
        "pluggy==1.3.0",
        "exceptiongroup==1.1.3",
        "tomli==2.0.1",
        "setuptools==68.0.0",
    ],
    "install": "editable",
    "env": {"SETUPTOOLS_SCM_PRETEND_VERSION": "8.0.0"},
}
_REQUESTS = {"python": "3.9", "packages": ["pytest==7.4.4"], "install": "regular"}
ENVIRONMENTS: dict[tuple[str, str], dict[str, Any]] = {
    ("pallets/flask", "2.3"): _FLASK,
    ("pytest-dev/pytest", "8.0"): _PYTEST_8,
    ("psf/requests", "2.10"): _REQUESTS,
}


class Unmeasured(Exception):
    """The instance cannot be measured here (environment, data)."""


# ---------------------------------------------------------------------------
# Test ids, files and reports
# ---------------------------------------------------------------------------


def junit_key(test_id: str) -> tuple[str, str]:
    """``path/to/test_x.py::Class::test[p]`` → (classname, name) as pytest's
    JUnit report writes them (its ``mangle_test_address``)."""
    path, bracket, params = test_id.partition("[")
    names = path.split("::")
    names[0] = re.sub(r"\.py$", "", names[0].replace("/", "."))
    names[-1] += bracket + params
    return ".".join(names[:-1]), names[-1]


def patched_files(patch: str) -> list[str]:
    """The files a unified diff touches (``b/`` side), in order."""
    found = re.findall(r"^diff --git a/(\S+) b/(\S+)$", patch, re.MULTILINE)
    return list(dict.fromkeys(b for _a, b in found))


def _merge(
    seen: dict[tuple[str, str], str], key: tuple[str, str], passed: bool
) -> None:
    seen[key] = PASS if passed and seen.get(key, PASS) == PASS else FAIL


def results(xml_text: str, tests: list[str]) -> dict[str, str]:
    """Per test id: ``pass`` (a pass or an expected failure, as the harness
    counts them), ``fail``, or ``missing`` when the report does not name it.

    The harness logged an id with a space in its parameters cut at the space
    (``test_x[a(1,``), so an id with an unclosed ``[`` stands for every case
    whose name, cut at its first space, is that id: it passes when all do."""
    seen: dict[tuple[str, str], str] = {}
    cut: dict[tuple[str, str], str] = {}
    for classname, name, outcome in cases(xml_text):
        passed = outcome in (PASS, XFAIL)
        _merge(seen, (classname, name), passed)
        _merge(cut, (classname, name.split(" ")[0]), passed)
    found = {}
    for test in tests:
        key = junit_key(test)
        truncated = "[" in key[1] and not key[1].endswith("]")
        found[test] = seen.get(key) or (cut.get(key) if truncated else None) or MISSING
    return found


def _group(found: dict[str, str]) -> dict[str, Any]:
    not_passed = [t for t, r in found.items() if r != PASS]
    return {
        "passed": len(found) - len(not_passed),
        "total": len(found),
        "not_passed": not_passed[:SHOWN],
    }


def _files(root: Path) -> dict[str, Path]:
    found = {}
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
        for name in names:
            path = Path(here) / name
            if path.suffix not in SKIPPED_SUFFIXES and not path.is_symlink():
                found[path.relative_to(root).as_posix()] = path
    return found


def changed_files(base: Path, project: Path) -> list[str]:
    """What the project changed against the base: added, changed, deleted."""
    before, after = _files(base), _files(project)
    return sorted(
        p
        for p in set(before) | set(after)
        if p not in before
        or p not in after
        or before[p].read_bytes() != after[p].read_bytes()
    )


def _copy(source: Path, target: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(
        source,
        target,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            *SKIPPED_DIRS, *(f"*{s}" for s in SKIPPED_SUFFIXES)
        ),
    )


def _run(
    command: list[str], cwd: Path, env: dict[str, str], timeout: int
) -> tuple[int | None, str]:
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout} s"
    except OSError as exc:
        return None, str(exc)
    return done.returncode, (done.stdout + done.stderr)[-4000:]


def apply_test_patch(tree: Path, base: Path, instance: dict[str, Any]) -> None:
    """Reset the files the test patch touches to the base, then apply it."""
    for relative in patched_files(instance["test_patch"]):
        target, pristine = tree / relative, base / relative
        target.unlink(missing_ok=True)
        if pristine.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pristine, target)
    with tempfile.TemporaryDirectory(prefix="swebench-patch-") as tmp:
        patch = Path(tmp) / "test.patch"
        patch.write_text(instance["test_patch"], encoding="utf-8")
        code, output = _run(
            ["git", "apply", "--whitespace=nowarn", str(patch)],
            tree,
            # Never a repository above the copy: the patch applies to the copy.
            {
                **os.environ,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CEILING_DIRECTORIES": str(tree.parent),
            },
            120,
        )
    if code != 0:
        raise Unmeasured(f"the test patch does not apply: {output.strip()[-300:]}")


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


class Environment:
    """A virtual environment built from a recipe; ``python`` runs the tests."""

    def __init__(self, recipe: dict[str, Any], where: Path):
        self.recipe, self.where = recipe, where
        self.env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
        }
        self.env.update({"PYTHONDONTWRITEBYTECODE": "1", **recipe.get("env", {})})
        self.uv = shutil.which("uv")
        self.python = ""

    def _must(self, command: list[str], cwd: Path, what: str) -> None:
        code, output = _run(command, cwd, self.env, BUILD_SECONDS)
        if code != 0:
            raise Unmeasured(f"{what} failed: {output.strip()[-400:]}")

    def build(self) -> None:
        wanted = str(self.recipe["python"])
        if self.recipe.get("venv", True) is False:
            self.python = wanted  # an interpreter used as it is (tests, local runs)
            return
        shutil.rmtree(self.where, ignore_errors=True)
        if self.uv:
            self._must(
                [self.uv, "venv", "--quiet", "--python", wanted, str(self.where)],
                self.where.parent,
                f"a Python {wanted} environment",
            )
        else:
            interpreter = (
                wanted if os.path.isabs(wanted) else shutil.which(f"python{wanted}")
            )
            if not interpreter:
                raise Unmeasured(
                    f"no Python {wanted} on this machine (and no uv to fetch one)"
                )
            self._must(
                [interpreter, "-m", "venv", str(self.where)],
                self.where.parent,
                f"a Python {wanted} environment",
            )
        self.python = str(self.where / "bin" / "python")
        if self.recipe.get("packages"):
            self.install(self.recipe["packages"], self.where.parent, "the packages")

    def install(self, args: list[str], cwd: Path, what: str) -> None:
        if self.uv:
            command = [
                self.uv,
                "pip",
                "install",
                "--quiet",
                "--python",
                self.python,
                *args,
            ]
        else:
            command = [self.python, "-m", "pip", "install", "--quiet", *args]
        self._must(command, cwd, what)

    def install_tree(self, tree: Path, *, deps: bool) -> None:
        mode = self.recipe.get("install")
        if not mode:
            return
        args = ["-e", "."] if mode == "editable" else ["."]
        if not deps:
            args = [
                "--no-deps",
                "--reinstall" if self.uv else "--force-reinstall",
                *args,
            ]
        self.install(args, tree, "installing the repository")

    def run_tests(
        self, tree: Path, files: list[str], timeout: int
    ) -> tuple[int | None, str, str]:
        """(exit status, output tail, JUnit report or "")."""
        with tempfile.TemporaryDirectory(prefix="swebench-report-") as tmp:
            report = Path(tmp) / f"{os.urandom(8).hex()}.xml"
            command = [
                self.python,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-rA",
                f"--junitxml={report}",
                *files,
            ]
            # The tests reach no proxy the build used: a test that expects a
            # connection to time out or be refused must see it do so.
            env = {
                k: v for k, v in self.env.items() if not k.lower().endswith("_proxy")
            }
            code, output = _run(command, tree, env, timeout)
            text = (
                report.read_text(encoding="utf-8", errors="replace")
                if report.is_file()
                else ""
            )
        return code, output, text


# ---------------------------------------------------------------------------
# The grade
# ---------------------------------------------------------------------------


def _measure(
    env: Environment, tree: Path, instance: dict[str, Any], timeout: int
) -> tuple[dict[str, str], dict[str, str], int | None, str, str]:
    """Run the instance's test files in *tree*: (F2P, P2P, exit status,
    output tail, why the report could not be read or "")."""
    f2p, p2p = instance["FAIL_TO_PASS"], instance["PASS_TO_PASS"]
    files = list(
        dict.fromkeys(
            [
                *patched_files(instance["test_patch"]),
                *(t.split("::")[0] for t in f2p + p2p),
            ]
        )
    )
    files = [f for f in files if (tree / f).is_file() and f.endswith(".py")]
    code, output, report = env.run_tests(tree, files, timeout)
    why = ""
    if code is None:
        why = output
    elif code not in (0, 1, 2) or not report.strip():
        why = f"exit status {code} and {'no' if not report.strip() else 'a'} report: pytest did not finish a run"
    try:
        found = results(report, f2p + p2p) if not why else {}
    except JUnitError as exc:
        found, why = {}, f"the report could not be read: {exc}"
    return (
        {t: found.get(t, MISSING) for t in f2p},
        {t: found.get(t, MISSING) for t in p2p},
        code,
        output,
        why,
    )


def grade(
    spec_dir: Path,
    project: Path,
    work: Path,
    *,
    recipe: dict[str, Any] | None = None,
    timeout: int = TEST_SECONDS,
) -> dict[str, Any]:
    """The instance's grade from its own tests, run here on *project*."""
    started = time.monotonic()
    instance = json.loads(
        (spec_dir / "acceptance" / INSTANCE_FILE).read_text(encoding="utf-8")
    )
    base = spec_dir / "seed"
    iid = instance["instance_id"]
    recipe = recipe or ENVIRONMENTS.get((instance["repo"], instance["version"]))
    detail: dict[str, Any] = {"instance_id": iid, "recipe": recipe}
    outcome, reason, code, output = MISSING, None, None, ""
    root = work / "swebench-grade" / iid
    try:
        if recipe is None:
            raise Unmeasured(
                f"no environment recipe for {instance['repo']} {instance['version']}"
            )
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        env = Environment(recipe, root / "venv")
        env.build()
        reference = root / "base"
        _copy(base, reference)
        apply_test_patch(reference, base, instance)
        env.install_tree(reference, deps=True)
        f2p, p2p, code, output, why = _measure(env, reference, instance, timeout)
        detail["base"] = {"fail_to_pass": _group(f2p), "pass_to_pass": _group(p2p)}
        if why:
            raise Unmeasured(f"the base's tests: {why}")
        if any(r != PASS for r in p2p.values()):
            raise Unmeasured(
                f"the environment does not reproduce the instance: "
                f"{sum(r != PASS for r in p2p.values())} PASS_TO_PASS test(s) do not pass on the base"
            )
        if all(r == PASS for r in f2p.values()):
            raise Unmeasured(
                "the environment does not reproduce the instance: FAIL_TO_PASS already passes on the base"
            )
        changed = changed_files(base, project)
        detail["changed_files"] = changed[:200]
        delivered = root / "delivery"
        _copy(project, delivered)
        apply_test_patch(delivered, base, instance)
        planted = [p for p in changed if RUNS_IN_GRADE.search(p)]
        if planted:
            outcome, reason = (
                FAIL,
                f"the delivery adds or changes what runs inside the test run: {', '.join(planted[:5])}",
            )
        else:
            try:
                env.install_tree(delivered, deps=False)
            except Unmeasured as exc:
                outcome, reason = (
                    FAIL,
                    f"the delivery could not be installed (the base could): {exc}",
                )
        if outcome == MISSING:
            f2p, p2p, code, output, why = _measure(env, delivered, instance, timeout)
            detail["fail_to_pass"], detail["pass_to_pass"] = _group(f2p), _group(p2p)
            resolved = all(r == PASS for r in [*f2p.values(), *p2p.values()])
            outcome = PASS if resolved and not why else FAIL
            reason = why or None
    except Unmeasured as exc:
        outcome, reason = MISSING, str(exc)
    except OSError as exc:
        outcome, reason = MISSING, f"the grade could not run: {exc}"
    finally:
        shutil.rmtree(root, ignore_errors=True)
    detail["seconds"] = round(time.monotonic() - started, 1)
    grade_out = {
        "passed": int(outcome == PASS),
        "total": 1,
        "results": {CRITERION: outcome},
        "exit_code": code,
        "output_tail": output,
        "isolation": ISOLATION,
        "swebench": {**detail, "reason": reason},
    }
    if outcome == MISSING:
        grade_out["unmeasured"] = reason
    return grade_out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grade")
    g.add_argument("--spec", required=True, type=Path)
    g.add_argument("--project", required=True, type=Path)
    g.add_argument(
        "--work", required=True, type=Path, help="where the environments are built"
    )
    g.add_argument("--out", required=True, type=Path)
    g.add_argument("--timeout", type=int, default=TEST_SECONDS)
    args = parser.parse_args(argv)
    result = grade(args.spec, args.project, args.work, timeout=args.timeout)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
