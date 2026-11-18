#!/usr/bin/env python3
"""HumanEval through the factory (issue #316, schema v5).

    humaneval.py prepare --dest DIR [--ids IDS] [--fake] [--cache DIR] [--url URL]

``run.sh --humaneval`` drives a subset of OpenAI's HumanEval problems
through the factory as specifications and grades each with HumanEval's own
tests, in the same grading sandbox as the reference specifications
(``bench_grade.grade``), never with the factory's own report.

- **The dataset** is downloaded at run time from its canonical published
  source, the ``openai/human-eval`` repository at a pinned commit
  (``DATASET_URL``), and is never committed. The bench computes the
  download's sha256 and refuses it, writing nothing, unless it is
  ``DATASET_SHA256``; a copy kept in ``--cache`` is checked again before
  every use and refused the same way. ``--url`` (``BENCH_HUMANEVAL_URL``)
  names a mirror; the pin does not change. Licence: MIT (``DATASET_LICENSE``).
- **Each problem becomes a specification** shaped like the bench's own: a
  title, a summary carrying the prompt, one requirement with one criterion
  (REQ-1.1), a quality target with a number and the constraints. The prompt
  (signature and docstring) is also the project's starting point: a
  ``seed/solution.py`` holding it verbatim, committed as the base
  (``seed.py``), since the specification's texts are whitespace-collapsed
  and a function body is completed in place, as HumanEval asks.
- **The hidden tests** are the problem's ``check`` function, written into
  ``acceptance/test_humaneval.py`` (``test_req_1_1_humaneval``) beside the
  specification, never into the project or the specification: they grade,
  as a reference specification's person-written tests do. The test runs the
  prompt first (its helper functions, as HumanEval's harness has them), then
  the delivered module's names over it, then ``check(<entry point>)`` under
  a time limit.
- **The fake producer** has answers for a few problems only
  (``bench/humaneval/fake/HumanEval_<n>/``, written for the bench, not the
  dataset's canonical solutions), so the plumbing runs green with the fake
  LLM; with the fake, the default subset is those problems.

The results go on the card as the ``humaneval`` block, apart from
``specs`` (``humaneval_card.py``, schema v5).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any

DATASET_COMMIT = "b9453e8f1865df0a0057a1383767caa400acfbcf"
DATASET_URL = (
    "https://raw.githubusercontent.com/openai/human-eval/"
    f"{DATASET_COMMIT}/data/HumanEval.jsonl.gz"
)
DATASET_SHA256 = "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef"
DATASET_LICENSE = "MIT"
DATASET_FILE = "HumanEval.jsonl.gz"
MAX_BYTES = 8 * 1024 * 1024
FIELDS = ("task_id", "prompt", "entry_point", "test")
# Fixed, so two runs of the default subset compare problem by problem.
DEFAULT_IDS = tuple(f"HumanEval/{n}" for n in (0, 2, 4, 7, 12, 23, 35, 53, 129, 162))
FAKE_ROOT = Path(__file__).resolve().parents[2] / "bench" / "humaneval" / "fake"
QA_FILE = "tests/test_req_1.py"
SPEC_PREFIX = "humaneval-"
CHECK_SECONDS = 10  # per problem's check(); HumanEval's own harness gave 3
_ID = re.compile(r"^(?:HumanEval/)?(\d{1,4})$")


class DatasetError(Exception):
    """The dataset could not be fetched, checked, read or mapped."""


# ---------------------------------------------------------------------------
# The dataset: fetched, pinned by sha256, read
# ---------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    if not url.startswith(("https://", "file://")):
        raise DatasetError(f"{url}: only https (or a local file://) is fetched")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(MAX_BYTES + 1)
    except OSError as exc:
        raise DatasetError(f"{url} could not be downloaded: {exc}") from None
    if len(data) > MAX_BYTES:
        raise DatasetError(f"{url} is larger than {MAX_BYTES} bytes")
    return data


def fetch(url: str, expected: str, cache: Path | None = None) -> tuple[bytes, str]:
    """The dataset's bytes and their sha256, computed here. A copy in
    *cache* is used only when it hashes to *expected*; a download is written
    there only after it does. Anything else raises DatasetError."""
    kept = cache / DATASET_FILE if cache else None
    if kept is not None and kept.is_file():
        data = kept.read_bytes()
        digest = sha256_hex(data)
        if digest != expected:
            raise DatasetError(
                f"the cached {kept} has sha256 {digest}, not the pinned {expected}: "
                "refused (delete it to download again)"
            )
        return data, digest
    data = _download(url)
    digest = sha256_hex(data)
    if digest != expected:
        raise DatasetError(
            f"{url} has sha256 {digest}, not the pinned {expected}: refused, nothing written"
        )
    if kept is not None:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
    return data, digest


def parse(data: bytes) -> dict[str, dict[str, str]]:
    """The problems by task id, from the gzipped JSON lines."""
    try:
        text = gzip.decompress(data).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise DatasetError(f"the dataset is not gzipped UTF-8 text: {exc}") from None
    problems: dict[str, dict[str, str]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise DatasetError(f"line {number} is not JSON") from None
        if not isinstance(row, dict) or not all(
            isinstance(row.get(k), str) and row[k] for k in FIELDS
        ):
            raise DatasetError(f"line {number} lacks one of {', '.join(FIELDS)}")
        if not _ID.match(row["task_id"]) or not row["entry_point"].isidentifier():
            raise DatasetError(f"line {number}: unexpected task id or entry point")
        if row["task_id"] in problems:
            raise DatasetError(f"line {number} repeats {row['task_id']}")
        problems[row["task_id"]] = {k: row[k] for k in FIELDS}
    if not problems:
        raise DatasetError("the dataset holds no problem")
    return problems


def task_id(text: str) -> str:
    """``7`` or ``HumanEval/7`` → ``HumanEval/7``."""
    match = _ID.match(text.strip())
    if not match:
        raise DatasetError(f"{text!r} is not a HumanEval task id")
    return f"HumanEval/{int(match.group(1))}"


def fake_dir(tid: str, root: Path = FAKE_ROOT) -> Path:
    return root / tid.replace("/", "_")


def resolve_ids(
    request: str,
    problems: dict[str, Any],
    *,
    fake: bool = False,
    fake_root: Path = FAKE_ROOT,
) -> list[str]:
    """The task ids a run takes: ``default`` (or empty) for ``DEFAULT_IDS``,
    with the fake the problems it has answers for; ``all``; or ids,
    comma-separated. With the fake every id needs an answer."""
    request = request.strip()
    if request in ("", "default"):
        ids = (
            [t for t in DEFAULT_IDS if fake_dir(t, fake_root).is_dir()]
            if fake
            else list(DEFAULT_IDS)
        )
    elif request == "all":
        ids = sorted(problems, key=lambda t: int(t.split("/")[1]))
    else:
        ids = list(dict.fromkeys(task_id(p) for p in request.split(",") if p.strip()))
    if not ids:
        raise DatasetError("no HumanEval problem selected")
    unknown = [t for t in ids if t not in problems]
    if unknown:
        raise DatasetError(f"not in the dataset: {', '.join(unknown)}")
    if fake:
        missing = [t for t in ids if not fake_dir(t, fake_root).is_dir()]
        if missing:
            raise DatasetError(
                f"the fake LLM has no answer for {', '.join(missing)}; "
                "name a real proxy (BENCH_LLM_URL) or the problems in bench/humaneval/fake"
            )
    return ids


# ---------------------------------------------------------------------------
# A problem as a specification
# ---------------------------------------------------------------------------


def spec_name(tid: str) -> str:
    return f"{SPEC_PREFIX}{int(tid.split('/')[1]):03d}"


def signature(problem: dict[str, str]) -> str:
    """The entry point's ``def`` line(s), as one line."""
    lines = problem["prompt"].splitlines()
    start = next(
        (
            i
            for i, s in enumerate(lines)
            if s.startswith(f"def {problem['entry_point']}(")
        ),
        None,
    )
    if start is None:
        return f"def {problem['entry_point']}(...)"
    found = []
    for line in lines[start:]:
        found.append(line.strip())
        if line.rstrip().endswith(":"):
            break
    return " ".join(found).rstrip(":")


def requirement_title(problem: dict[str, str]) -> str:
    return f"Complete {problem['entry_point']} ({problem['task_id']})"


def to_spec(problem: dict[str, str]) -> dict[str, Any]:
    """The specification, as ``POST /v1/specifications`` takes it. It never
    carries the tests."""
    entry, tid = problem["entry_point"], problem["task_id"]
    prompt = " ".join(problem["prompt"].split())
    return {
        "title": f"{tid}: {entry}",
        "summary": (
            f"Complete the Python function {entry} in solution.py, which the project "
            f"already holds with its signature and docstring ({tid}). The docstring is "
            f"the specification. solution.py as given: {prompt}"
        ),
        "requirements": [
            {
                "title": requirement_title(problem),
                "description": (
                    f"solution.py at the project root defines {signature(problem)}. "
                    "Complete its body so the function returns what its docstring "
                    "says for every input the docstring describes; keep its name, "
                    "parameters and docstring."
                ),
                "criteria": [
                    {
                        "given": f"the inputs the docstring of {entry} describes, its examples included",
                        "when": f"{entry} is imported from solution and called with each",
                        "then": "it returns the value the docstring specifies for that input",
                    }
                ],
            }
        ],
        "quality": [
            {
                "attribute": "performance",
                "target": f"each call on the docstring's inputs returns in under {CHECK_SECONDS} seconds",
            }
        ],
        "constraints": [
            "Python 3.11 or later, standard library only.",
            (
                f"solution.py at the project root, which the project already holds, "
                f"defines {entry}; `from solution import {entry}` must work."
            ),
            (
                "Keep the function's name, parameters and docstring as given; helper "
                "functions solution.py already defines stay."
            ),
            "Tests live in tests/ and run with pytest from the project root.",
        ],
    }


def acceptance_test(problem: dict[str, str]) -> str:
    """The hidden test: HumanEval's ``check`` on the delivered function."""
    return f'''"""{problem["task_id"]}'s own tests (HumanEval's check), never shown to the factory.

Written by scripts/bench/humaneval.py from the pinned dataset. The prompt runs
first (its helpers, as HumanEval's harness has them), then the delivered
module's names over it, then check(<entry point>) under a time limit.
"""

import signal

PROMPT = {problem["prompt"]!r}
TEST = {problem["test"]!r}
ENTRY_POINT = {problem["entry_point"]!r}
SECONDS = {CHECK_SECONDS}


def _expired(signum, frame):
    raise TimeoutError(f"check() ran past {{SECONDS}} s")


def test_req_1_1_humaneval():
    import solution  # the delivered module: the project is on sys.path, last

    namespace = {{"__name__": "humaneval_check"}}
    exec(compile(PROMPT, "<prompt>", "exec"), namespace)
    namespace.update(
        {{k: v for k, v in vars(solution).items() if not k.startswith("__")}}
    )
    exec(compile(TEST, "<test>", "exec"), namespace)
    candidate = getattr(solution, ENTRY_POINT)
    alarm = hasattr(signal, "SIGALRM")
    if alarm:
        signal.signal(signal.SIGALRM, _expired)
        signal.alarm(SECONDS)
    try:
        namespace["check"](candidate)
    finally:
        if alarm:
            signal.alarm(0)
'''


def fake_plan(problem: dict[str, str], files: list[str]) -> dict[str, Any]:
    """The fake producer's plan (``fake_llm.load``): one Developer task
    delivering *files*, and QA's acceptance file."""
    entry, tid = problem["entry_point"], problem["task_id"]
    return {
        "sprint_name": f"{tid} 1",
        "goal": f"Complete {entry}",
        "tasks": [
            {
                "title": f"Implement {entry} for {tid}",
                "description": f"solution.{entry} completed, with its unit tests.",
                "priority": "high",
                "assignee": "Developer",
                "covers": ["REQ-1.1"],
                "depends_on": [],
                "files": [f for f in files if f != QA_FILE],
            }
        ],
        "acceptance": {"REQ-1": QA_FILE},
    }


def _files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


def materialize(
    problem: dict[str, str], dest: Path, fake_root: Path = FAKE_ROOT
) -> dict[str, Any]:
    """Write the problem's specification folder under *dest*: ``spec.json``,
    ``seed/`` (the prompt as ``solution.py``), ``acceptance/`` (the hidden
    test) and, when the bench has an answer for it, ``fake/``."""
    tid = problem["task_id"]
    folder = dest / spec_name(tid)
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "seed").mkdir(parents=True)
    (folder / "acceptance").mkdir()
    (folder / "spec.json").write_text(
        json.dumps(to_spec(problem), indent=2) + "\n", encoding="utf-8"
    )
    (folder / "seed" / "solution.py").write_text(problem["prompt"], encoding="utf-8")
    (folder / "seed" / "README.md").write_text(
        f"# {tid}\n\nComplete `{problem['entry_point']}` in `solution.py`.\n",
        encoding="utf-8",
    )
    (folder / "acceptance" / "test_humaneval.py").write_text(
        acceptance_test(problem), encoding="utf-8"
    )
    answers = fake_dir(tid, fake_root)
    if answers.is_dir():
        files = _files(answers)
        if QA_FILE not in files or "solution.py" not in files:
            raise DatasetError(f"{answers} needs solution.py and {QA_FILE}")
        shutil.copytree(answers, folder / "fake" / "files")
        (folder / "fake" / "plan.json").write_text(
            json.dumps(fake_plan(problem, files), indent=2) + "\n", encoding="utf-8"
        )
    return {
        "task_id": tid,
        "spec": folder.name,
        "entry_point": problem["entry_point"],
        "fake": answers.is_dir(),
    }


def dataset_record(url: str, digest: str, count: int) -> dict[str, Any]:
    return {
        "name": "HumanEval",
        "source": url,
        "sha256": digest,
        "license": DATASET_LICENSE,
        "problems": count,
    }


def prepare(
    dest: Path,
    request: str = "default",
    *,
    fake: bool = False,
    cache: Path | None = None,
    url: str = DATASET_URL,
    fake_root: Path = FAKE_ROOT,
    expected: str = DATASET_SHA256,
) -> dict[str, Any]:
    """Fetch and check the dataset, select the problems, write each one's
    specification under ``dest/specs`` and the index ``dest/problems.json``."""
    data, digest = fetch(url, expected, cache)
    problems = parse(data)
    ids = resolve_ids(request, problems, fake=fake, fake_root=fake_root)
    specs = dest / "specs"
    shutil.rmtree(specs, ignore_errors=True)
    specs.mkdir(parents=True)
    index = {
        "dataset": dataset_record(url, digest, len(problems)),
        "subset": ids,
        "problems": [materialize(problems[t], specs, fake_root) for t in ids],
    }
    (dest / "problems.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--dest", required=True, type=Path)
    p.add_argument(
        "--ids", default="default", help="default, all, or ids: 0,2,HumanEval/7"
    )
    p.add_argument("--fake", action="store_true", help="the fake LLM answers")
    p.add_argument("--cache", type=Path, help="a folder that keeps the checked dataset")
    p.add_argument("--url", default=DATASET_URL, help="a mirror; the sha256 pin stays")
    args = parser.parse_args(argv)
    try:
        index = prepare(
            args.dest,
            args.ids,
            fake=args.fake,
            cache=args.cache,
            url=args.url or DATASET_URL,
        )
    except (DatasetError, OSError) as exc:
        print(f"humaneval: {exc}", file=sys.stderr)
        return 2
    for problem in index["problems"]:
        print(args.dest / "specs" / problem["spec"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
