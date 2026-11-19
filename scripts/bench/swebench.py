#!/usr/bin/env python3
"""SWE-bench Lite through the factory (issue #687, schema v6).

    swebench.py prepare --dest DIR [--ids IDS] [--fake] [--instances FILE]
                        [--repo-url TEMPLATE]

``run.sh --swebench`` drives a fixed subset of SWE-bench Lite's instances
through the factory and grades each with the instance's own tests, run by the
scorer (``swebench_grade.py``), never by the factory's report.

- **The dataset** is ``princeton-nlp/SWE-bench_Lite`` on Hugging Face at
  revision ``REVISION``. Nothing of it is committed: each instance is fetched
  at run time (``ROWS_URL``, one row by its index) and refused unless the
  sha256 the bench computes over the fields it uses (``FIELDS``, canonical
  JSON) is the one pinned beside its id in ``PINS``. The ids, row indices and
  hashes are the bench's own data; the texts and tests are the instances'
  upstream repositories', under their licences (bench/README.md).
- **The repository** is cloned from GitHub (``--repo-url`` names another
  source, ``{repo}`` standing for ``owner/name``) into a cache under
  ``--dest`` and its tree at the instance's base commit is written out as the
  specification's ``seed/``, so ``seed.py`` commits it as the project's base
  as for any seeded specification.
- **The problem statement is the specification.** A specification's texts
  are cut at 1000 characters (``spec_check.TEXT_LIMIT``), so the statement
  goes whole into the seed as ``ISSUE_FILE`` and the specification names it,
  quoting its beginning.
- **The hidden tests** (the test patch, FAIL_TO_PASS, PASS_TO_PASS) go into
  ``acceptance/instance.json`` beside the specification, never into the
  project or the specification.
- **The fake producer** has no answers of its own: with ``--fake`` its
  delivery is the instance's reference patch applied at run time to the base
  (``fake/``, in the work folder only), so the plumbing can run green without
  a model. A fake resolve rate measures nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

DATASET = "princeton-nlp/SWE-bench_Lite"
REVISION = "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"
SPLIT = "test"
LICENSE = (
    "none declared by the dataset; the harness (SWE-bench/SWE-bench) is MIT, "
    "each instance's text and tests are its upstream repository's"
)
ROWS_URL = (
    "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Lite"
    "&config=default&split=test&offset={row}&length=1"
)
REPO_URL = "https://github.com/{repo}.git"
FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "version",
    "problem_statement",
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
)
ISSUE_FILE = "SWEBENCH_ISSUE.md"
INSTANCE_FILE = "instance.json"
QA_FILE = "test_req_1_swebench.py"
SPEC_PREFIX = "swebench-"
MAX_BYTES = 4 * 1024 * 1024
RETRY_SECONDS = 10  # the dataset server limits its rate (HTTP 429)
QUOTED = 600  # characters of the statement quoted in the summary
TEST_SECONDS = 600  # per instance's test run (swebench_grade.py)
_ID = re.compile(r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d{1,6}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class Pin(NamedTuple):
    instance_id: str
    row: int  # the row's index in the split at REVISION
    sha256: str  # over FIELDS, canonical JSON (instance_sha256)


# Five instances from small repositories with fast suites, chosen at REVISION
# (bench/README.md, "SWE-bench Lite through the factory", says how).
PINS: tuple[Pin, ...] = (
    Pin(
        "pallets__flask-4992",
        148,
        "79251061201c38007c9a84e7abf1eb6b16bcbc1f02fb296cab9394d9c216c74f",
    ),
    Pin(
        "pallets__flask-5063",
        149,
        "339e331c776aeba9e523b26bc4ba5b822771cbe18071835e05a4a4797e9484a4",
    ),
    Pin(
        "psf__requests-3362",
        154,
        "60542bb09998a6591f6c69aea8d1b76e448a74f0a21b37e6d2785ef4eb876973",
    ),
    Pin(
        "pytest-dev__pytest-11143",
        167,
        "88881219e5b94f9d2ee1638c198e9ccc49df729864eb371c661242cfe43bd722",
    ),
    Pin(
        "pytest-dev__pytest-11148",
        168,
        "bc297b595a68e517d3db213c7d5862d9db1c14a55679674bfcbda58dded922b6",
    ),
)


class DatasetError(Exception):
    """An instance could not be fetched, checked, read or prepared."""


# ---------------------------------------------------------------------------
# The instances: fetched one row at a time, each pinned by sha256
# ---------------------------------------------------------------------------


def instance_sha256(row: dict[str, Any]) -> str:
    """The sha256 of the fields the bench uses, as canonical JSON: what a
    pin names, so a row changed upstream is refused."""
    data = {k: row.get(k) for k in FIELDS}
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tests(value: Any, field: str) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise DatasetError(f"{field} is not a JSON list") from None
    if not isinstance(value, list) or not all(isinstance(t, str) and t for t in value):
        raise DatasetError(f"{field} must be a list of test ids")
    return list(value)


def check_row(row: Any, pin: Pin) -> dict[str, Any]:
    """The instance from *row*, once its fields are there and hash to the
    pin; DatasetError otherwise. The test lists come back as lists."""
    if not isinstance(row, dict):
        raise DatasetError(f"{pin.instance_id}: the row is not an object")
    missing = [k for k in FIELDS if not isinstance(row.get(k), (str, list))]
    if missing:
        raise DatasetError(f"{pin.instance_id}: the row lacks {', '.join(missing)}")
    if row["instance_id"] != pin.instance_id:
        raise DatasetError(
            f"row {pin.row} is {row['instance_id']!r}, not {pin.instance_id}: refused"
        )
    digest = instance_sha256(row)
    if digest != pin.sha256:
        raise DatasetError(
            f"{pin.instance_id} has sha256 {digest}, not the pinned {pin.sha256}: refused"
        )
    instance = {k: row[k] for k in FIELDS}
    for field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        instance[field] = _tests(row[field], field)
    if not instance["FAIL_TO_PASS"]:
        raise DatasetError(f"{pin.instance_id}: FAIL_TO_PASS is empty")
    if not _REPO.match(instance["repo"]) or not _SHA.match(instance["base_commit"]):
        raise DatasetError(f"{pin.instance_id}: unexpected repo or base commit")
    return instance


def _download(url: str, tries: int = 4) -> bytes:
    """The body at *url*; a rate limit or a server error is retried, waiting
    longer each time."""
    if not url.startswith("https://"):
        raise DatasetError(f"{url}: only https is fetched")
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(MAX_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if attempt == tries or not (exc.code == 429 or exc.code >= 500):
                raise DatasetError(f"{url} could not be downloaded: {exc}") from None
            time.sleep(RETRY_SECONDS * attempt)
        except OSError as exc:
            raise DatasetError(f"{url} could not be downloaded: {exc}") from None
    if len(data) > MAX_BYTES:
        raise DatasetError(f"{url} is larger than {MAX_BYTES} bytes")
    return data


def fetch_row(pin: Pin, url: str = ROWS_URL) -> dict[str, Any]:
    """The instance's row from the dataset server, by its index; a cell the
    server cut short is refused."""
    try:
        body = json.loads(_download(url.format(row=pin.row)))
        (entry,) = body["rows"]
    except (ValueError, KeyError, TypeError):
        raise DatasetError(
            f"{pin.instance_id}: the server's answer has no row"
        ) from None
    if entry.get("truncated_cells"):
        raise DatasetError(
            f"{pin.instance_id}: the server truncated {entry['truncated_cells']}"
        )
    return entry.get("row")


def read_rows(path: Path) -> dict[str, dict[str, Any]]:
    """Rows from a local JSON-lines file (a mirror, or a test's instance), by
    id; they are checked against the pins like a download."""
    rows: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise DatasetError(f"{path}:{number} is not JSON") from None
        if not isinstance(row, dict) or not isinstance(row.get("instance_id"), str):
            raise DatasetError(f"{path}:{number} has no instance_id")
        rows[row["instance_id"]] = row
    return rows


def resolve_ids(request: str, pins: tuple[Pin, ...] = PINS) -> list[Pin]:
    """The pins a run takes: ``default`` (or empty) for all of them, or ids
    comma-separated, each one of the pinned."""
    request = request.strip()
    by_id = {p.instance_id: p for p in pins}
    if request in ("", "default"):
        chosen = list(pins)
    else:
        names = list(dict.fromkeys(p.strip() for p in request.split(",") if p.strip()))
        unknown = [n for n in names if n not in by_id]
        if unknown:
            raise DatasetError(
                f"not a pinned instance: {', '.join(unknown)} "
                f"(pinned: {', '.join(by_id) or 'none'})"
            )
        chosen = [by_id[n] for n in names]
    if not chosen:
        raise DatasetError("no SWE-bench instance selected")
    return chosen


# ---------------------------------------------------------------------------
# The repository at the base commit
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    try:
        done = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=" + os.devnull,
                "-c",
                "core.autocrlf=false",
                *args,
            ],
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DatasetError(f"git {args[0]}: {exc}") from None
    if done.returncode != 0:
        raise DatasetError(
            f"git {args[0]}: {(done.stderr or done.stdout).strip()[-500:]}"
        )
    return done.stdout


def checkout(
    instance: dict[str, Any], cache: Path, seed: Path, repo_url: str = REPO_URL
) -> int:
    """Write the repository's tree at the base commit into *seed* (emptied
    first), from a clone kept in *cache*; the number of files. A tree holding
    a symbolic link or a submodule is refused (``seed.py`` copies files only)."""
    repo, base = instance["repo"], instance["base_commit"]
    clone = cache / repo.replace("/", "__")
    if not (clone / ".git").is_dir():
        shutil.rmtree(clone, ignore_errors=True)
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(
            "clone", "--quiet", "--no-checkout", repo_url.format(repo=repo), str(clone)
        )
    try:
        _git("cat-file", "-e", f"{base}^{{commit}}", cwd=clone)
    except DatasetError:
        _git("fetch", "--quiet", "origin", base, cwd=clone)
    odd = [
        line.split("\t", 1)[1]
        for line in _git("ls-tree", "-r", base, cwd=clone).splitlines()
        if line.startswith(("120000", "160000"))
    ]
    if odd:
        raise DatasetError(
            f"{instance['instance_id']}: a link or submodule in the tree: {odd[:3]}"
        )
    shutil.rmtree(seed, ignore_errors=True)
    seed.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="swebench-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git("read-tree", base, cwd=clone, env=env)
        _git(
            "checkout-index", "--all", f"--prefix={seed.resolve()}/", cwd=clone, env=env
        )
    return sum(1 for p in seed.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# An instance as a specification
# ---------------------------------------------------------------------------


def spec_name(instance_id: str) -> str:
    return SPEC_PREFIX + instance_id.replace("__", "-").lower()


def requirement_title(instance: dict[str, Any]) -> str:
    return f"Resolve {instance['instance_id']}"


def to_spec(instance: dict[str, Any]) -> dict[str, Any]:
    """The specification, as ``POST /v1/specifications`` takes it: the
    problem statement by the file that holds it, quoted from its start. It
    never carries the tests or the reference patch."""
    iid, repo = instance["instance_id"], instance["repo"]
    statement = " ".join(instance["problem_statement"].split())
    quoted = statement[:QUOTED] + ("…" if len(statement) > QUOTED else "")
    return {
        "title": f"{iid}: an issue reported in {repo}",
        "summary": (
            f"Resolve an issue reported in the {repo} repository, which the project "
            f"already holds at commit {instance['base_commit'][:12]}. The issue's full "
            f"text is in {ISSUE_FILE} at the project root; it begins: {quoted}"
        ),
        "requirements": [
            {
                "title": requirement_title(instance),
                "description": (
                    f"Change the {repo} source so the behaviour the issue in {ISSUE_FILE} "
                    "reports is fixed as the issue expects, keeping the repository's "
                    "public interface except where the issue asks for a change."
                ),
                "criteria": [
                    {
                        "given": f"the situation the issue in {ISSUE_FILE} describes",
                        "when": "it is reproduced against the changed repository",
                        "then": (
                            "the behaviour is the one the issue expects, and the "
                            "repository's existing tests still pass"
                        ),
                    }
                ],
            }
        ],
        "quality": [
            {
                "attribute": "reliability",
                "target": "0 of the repository's existing tests that passed before the change fail after it",
            },
            {
                "attribute": "performance",
                "target": f"the repository's test files the change touches run in under {TEST_SECONDS} seconds",
            },
        ],
        "constraints": [
            f"The project is the {repo} repository; keep its layout, language and Python versions.",
            f"Change the source; {ISSUE_FILE} is the issue's text and stays as it is.",
            "Tests live where the repository keeps them and run with pytest from the project root.",
            "Add no conftest.py, sitecustomize.py or pytest plugin, and change none.",
        ],
    }


def fake_files(instance: dict[str, Any], seed: Path) -> dict[str, str]:
    """The files the reference patch changes, with their content after it:
    the fake producer's delivery, computed at run time and never committed."""
    with tempfile.TemporaryDirectory(prefix="swebench-fake-") as tmp:
        work = Path(tmp) / "tree"
        shutil.copytree(seed, work)
        patch = Path(tmp) / "gold.patch"
        patch.write_text(instance["patch"], encoding="utf-8")
        _git(
            "apply",
            "--whitespace=nowarn",
            str(patch),
            cwd=work,
            env={"GIT_CEILING_DIRECTORIES": tmp},
        )
        changed: dict[str, str] = {}
        for path in sorted(work.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(work).as_posix()
            before = seed / relative
            if not before.is_file() or before.read_bytes() != path.read_bytes():
                changed[relative] = path.read_text(encoding="utf-8")
        gone = [
            p.relative_to(seed).as_posix()
            for p in seed.rglob("*")
            if p.is_file() and not (work / p.relative_to(seed)).exists()
        ]
    if gone:
        raise DatasetError(
            f"{instance['instance_id']}: the fake cannot delete {gone[:3]}"
        )
    if not changed:
        raise DatasetError(
            f"{instance['instance_id']}: the reference patch changes nothing"
        )
    return changed


QA_TEST = '''"""QA's acceptance test for {iid} in the fake run: the issue's text is there."""

from pathlib import Path


def test_req_1_1_issue_text_is_present():
    assert (Path(__file__).resolve().parent / "{issue}").is_file()
'''


def fake_plan(instance: dict[str, Any], files: list[str]) -> dict[str, Any]:
    """The fake producer's plan (``fake_llm.load``): one Developer task
    delivering *files*, and QA's acceptance file."""
    iid = instance["instance_id"]
    return {
        "sprint_name": f"{iid} 1",
        "goal": f"Resolve {iid}",
        "tasks": [
            {
                "title": f"Fix the issue of {iid}",
                "description": f"The change {iid}'s issue asks for, in {instance['repo']}.",
                "priority": "high",
                "assignee": "Developer",
                "covers": ["REQ-1.1"],
                "depends_on": [],
                "files": files,
            }
        ],
        "acceptance": {"REQ-1": QA_FILE},
    }


def materialize(
    instance: dict[str, Any],
    dest: Path,
    cache: Path,
    *,
    fake: bool = False,
    repo_url: str = REPO_URL,
) -> dict[str, Any]:
    """Write the instance's specification folder under *dest*: ``spec.json``,
    ``seed/`` (the repository at its base commit and ``ISSUE_FILE``),
    ``acceptance/instance.json`` (the hidden tests) and, with *fake*,
    ``fake/`` from the reference patch."""
    iid = instance["instance_id"]
    folder = dest / spec_name(iid)
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "acceptance").mkdir(parents=True)
    files = checkout(instance, cache, folder / "seed", repo_url)
    if (folder / "seed" / ISSUE_FILE).exists():
        raise DatasetError(f"{iid}: the repository already holds {ISSUE_FILE}")
    (folder / "seed" / ISSUE_FILE).write_text(
        f"# {iid}\n\n{instance['problem_statement'].rstrip()}\n", encoding="utf-8"
    )
    (folder / "spec.json").write_text(
        json.dumps(to_spec(instance), indent=2) + "\n", encoding="utf-8"
    )
    hidden = {k: instance[k] for k in FIELDS if k not in ("patch", "problem_statement")}
    (folder / "acceptance" / INSTANCE_FILE).write_text(
        json.dumps(hidden, indent=2) + "\n", encoding="utf-8"
    )
    if fake:
        delivery = fake_files(instance, folder / "seed")
        for relative, text in delivery.items():
            target = folder / "fake" / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        (folder / "fake" / "files" / QA_FILE).write_text(
            QA_TEST.format(iid=iid, issue=ISSUE_FILE), encoding="utf-8"
        )
        (folder / "fake" / "plan.json").write_text(
            json.dumps(fake_plan(instance, sorted(delivery)), indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "instance_id": iid,
        "repo": instance["repo"],
        "version": instance["version"],
        "spec": folder.name,
        "files": files + 1,
        "fail_to_pass": len(instance["FAIL_TO_PASS"]),
        "pass_to_pass": len(instance["PASS_TO_PASS"]),
        "fake": fake,
    }


def dataset_record(source: str, pins: list[Pin]) -> dict[str, Any]:
    return {
        "name": DATASET,
        "revision": REVISION,
        "split": SPLIT,
        "source": source,
        "license": LICENSE,
        "instances": {p.instance_id: p.sha256 for p in pins},
    }


def prepare(
    dest: Path,
    request: str = "default",
    *,
    fake: bool = False,
    rows_file: Path | None = None,
    repo_url: str = REPO_URL,
    pins: tuple[Pin, ...] = PINS,
    url: str = ROWS_URL,
) -> dict[str, Any]:
    """Fetch and check each selected instance, write its specification under
    ``dest/specs`` and the index ``dest/instances.json``. Every instance is
    checked before anything is written."""
    chosen = resolve_ids(request, pins)
    local = read_rows(rows_file) if rows_file else None
    instances = []
    for pin in chosen:
        row = local.get(pin.instance_id) if local is not None else fetch_row(pin, url)
        if row is None:
            raise DatasetError(f"{pin.instance_id} is not in {rows_file}")
        instances.append(check_row(row, pin))
    specs = dest / "specs"
    shutil.rmtree(specs, ignore_errors=True)
    specs.mkdir(parents=True)
    index = {
        "dataset": dataset_record(
            str(rows_file) if rows_file else url.split("?")[0], chosen
        ),
        "subset": [p.instance_id for p in chosen],
        "instances": [
            materialize(i, specs, dest / "repos", fake=fake, repo_url=repo_url)
            for i in instances
        ],
    }
    (dest / "instances.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--dest", required=True, type=Path)
    p.add_argument("--ids", default="default", help="default, or pinned instance ids")
    p.add_argument("--fake", action="store_true", help="the fake producer's delivery")
    p.add_argument(
        "--instances", type=Path, help="local JSON-lines rows, checked against the pins"
    )
    p.add_argument(
        "--repo-url", default=REPO_URL, help="where to clone from; {repo} is owner/name"
    )
    args = parser.parse_args(argv)
    try:
        index = prepare(
            args.dest,
            args.ids,
            fake=args.fake,
            rows_file=args.instances,
            repo_url=args.repo_url or REPO_URL,
        )
    except (DatasetError, OSError) as exc:
        print(f"swebench: {exc}", file=sys.stderr)
        return 2
    for instance in index["instances"]:
        print(args.dest / "specs" / instance["spec"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
