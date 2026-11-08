#!/usr/bin/env python3
"""The bench's scorecard: grade a produced project, collect a run's numbers,
write the scorecard (JSON and Markdown).

Subcommands, used by ``scripts/bench/run.sh``:

- ``grade --spec DIR --project DIR --out FILE`` runs the specification's
  acceptance tests, written by a person (``<spec>/acceptance/``), against the
  project the factory produced, and reads the result back per criterion from
  pytest's JUnit report. A test named ``test_req_1_2_...`` decides REQ-1.2; a
  criterion passes when it has tests and every one of them passes. This is an
  observation the bench makes, never a claim of the factory's agents.
- ``collect`` reads the sprint's numbers through the API (board, runs, sprint
  list, the specification's own coverage) and the reported tokens from the
  audit log (only rows whose usage the provider reported, ``tokens:
  reported``), and writes one spec entry.
- ``write --out-dir DIR ENTRY...`` joins the entries into one scorecard,
  checks it against the schema (``validate``) and writes
  ``<stamp>.json`` and ``<stamp>.md``.

The pure functions (``criterion_of_test``, ``criteria_from_junit``,
``summarize_spec``, ``build_scorecard``, ``validate``, ``render_markdown``)
are what ``backend/tests/test_bench_scorecard.py`` checks.

Credit: OpenHands' evaluation harness, which states the harness version
beside every number (here: the schema version, the factory's commit and the
models served), and SWE-bench Verified, a bench graded by tests a person
wrote rather than by the model under test.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PASS, FAIL, MISSING = "pass", "fail", "missing"
SPEC_STATUSES = ("completed", "timeout", "error")
_TEST_NAME = re.compile(r"^test_req_(\d+)_(\d+)(?:_|\[|$)")


# ---------------------------------------------------------------------------
# Grading: the person-written acceptance tests on the produced project
# ---------------------------------------------------------------------------


def criteria_of(spec: dict[str, Any]) -> list[str]:
    """The criterion ids of *spec* in the order the backend numbers them
    (``services/spec_check.normalize``: REQ-<n>.<m>, from 1, in order)."""
    return [
        f"REQ-{i}.{j}"
        for i, requirement in enumerate(spec.get("requirements") or [], start=1)
        for j, _criterion in enumerate(requirement.get("criteria") or [], start=1)
    ]


def criterion_of_test(name: str) -> str | None:
    """``test_req_1_2_reads`` (or ``test_req_1_2[case]``) → ``REQ-1.2``."""
    match = _TEST_NAME.match(name or "")
    return f"REQ-{match.group(1)}.{match.group(2)}" if match else None


def criteria_from_junit(xml_text: str, criteria: list[str]) -> dict[str, str]:
    """Per criterion: ``pass`` when it has tests and all passed, ``fail`` when
    one failed, errored or was skipped, ``missing`` when no test named it ran.
    A report that cannot be read leaves every criterion missing."""
    seen: dict[str, list[bool]] = {}
    try:
        root = ET.fromstring(xml_text) if xml_text.strip() else None
    except ET.ParseError:
        root = None
    for case in root.iter("testcase") if root is not None else []:
        criterion = criterion_of_test(case.get("name", ""))
        if criterion is None:
            continue
        failed = any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
        seen.setdefault(criterion, []).append(not failed)
    return {
        c: (MISSING if c not in seen else PASS if all(seen[c]) else FAIL)
        for c in criteria
    }


def grade(spec_dir: Path, project: Path, python: str, timeout: int = 300) -> dict[str, Any]:
    """Run ``<spec_dir>/acceptance`` against *project* and read the criteria back.

    The tests are copied out of the repository so no configuration found
    above them applies, and the project goes on ``PYTHONPATH``; the project
    never sees the tests, and the tests never import the factory."""
    spec = json.loads((spec_dir / "spec.json").read_text(encoding="utf-8"))
    criteria = criteria_of(spec)
    with tempfile.TemporaryDirectory(prefix="bench-grade-") as tmp:
        tests = Path(tmp) / "acceptance"
        shutil.copytree(spec_dir / "acceptance", tests)
        report = Path(tmp) / "junit.xml"
        env = {**os.environ, "PYTHONPATH": str(project.resolve()), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            run = subprocess.run(
                [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", os.devnull,
                 "--rootdir", tmp, f"--junitxml={report}", str(tests)],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=timeout,
            )
            output, exit_code = run.stdout[-4000:], run.returncode
        except subprocess.TimeoutExpired:
            output, exit_code = "acceptance tests timed out", None
        xml_text = report.read_text(encoding="utf-8") if report.exists() else ""
    results = criteria_from_junit(xml_text, criteria)
    return {
        "passed": sum(1 for r in results.values() if r == PASS),
        "total": len(criteria),
        "results": results,
        "exit_code": exit_code,
        "output_tail": output,
    }


# ---------------------------------------------------------------------------
# One spec's entry
# ---------------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def summarize_spec(
    *,
    name: str,
    status: str,
    items: list[dict[str, Any]],
    runs_per_task: dict[int, int],
    rework_requests: int,
    graded: dict[str, Any],
    factory_verified: int | None,
    wall_clock_seconds: float,
    tokens: dict[str, int],
    models: dict[str, list[str]],
    error: str | None = None,
) -> dict[str, Any]:
    """One spec's entry from what was observed.

    A task is done at the first attempt when it is done and the factory
    recorded exactly one delivery (one AgentRun) for it: a rework request, a
    quality-gate send-back or a refuted delivery each mean another one."""
    total = len(items)
    done = sum(1 for i in items if i.get("status") == "done")
    blocked = sum(1 for i in items if i.get("status") == "blocked")
    first = sum(
        1 for i in items if i.get("status") == "done" and runs_per_task.get(i.get("id"), 0) == 1
    )
    return {
        "name": name,
        "status": status,
        "error": error,
        "criteria": {
            "passed": int(graded.get("passed", 0)),
            "total": int(graded.get("total", 0)),
            "results": dict(graded.get("results") or {}),
        },
        "factory_criteria_verified": factory_verified,
        "tasks": {"total": total, "done": done, "blocked": blocked, "done_first_attempt": first},
        "rework_requests": int(rework_requests),
        "rework_per_task": _ratio(rework_requests, total),
        "blocked_share": _ratio(blocked, total),
        "first_attempt_rate": _ratio(first, total),
        "wall_clock_seconds": round(float(wall_clock_seconds), 1),
        "tokens": {
            "reported": int(tokens.get("reported", 0)),
            "turns_reported": int(tokens.get("turns_reported", 0)),
            "turns_unreported": int(tokens.get("turns_unreported", 0)),
        },
        "models": {role: sorted(set(m)) for role, m in sorted(models.items())},
    }


def build_scorecard(
    specs: list[dict[str, Any]], *, stamp: str, commit: str, llm: str
) -> dict[str, Any]:
    """The run's scorecard: every spec's entry and the totals over them."""
    criteria_passed = sum(s["criteria"]["passed"] for s in specs)
    criteria_total = sum(s["criteria"]["total"] for s in specs)
    tasks = sum(s["tasks"]["total"] for s in specs)
    first = sum(s["tasks"]["done_first_attempt"] for s in specs)
    blocked = sum(s["tasks"]["blocked"] for s in specs)
    rework = sum(s["rework_requests"] for s in specs)
    return {
        "schema_version": SCHEMA_VERSION,
        "stamp": stamp,
        "commit": commit,
        "llm": llm,
        "specs": sorted(specs, key=lambda s: s["name"]),
        "totals": {
            "specs": len(specs),
            "specs_completed": sum(1 for s in specs if s["status"] == "completed"),
            "criteria_passed": criteria_passed,
            "criteria_total": criteria_total,
            "criteria_rate": _ratio(criteria_passed, criteria_total),
            "tasks": tasks,
            "done_first_attempt": first,
            "first_attempt_rate": _ratio(first, tasks),
            "rework_per_task": _ratio(rework, tasks),
            "blocked_share": _ratio(blocked, tasks),
            "wall_clock_seconds": round(sum(s["wall_clock_seconds"] for s in specs), 1),
            "tokens_reported": sum(s["tokens"]["reported"] for s in specs),
        },
    }


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SPEC_FIELDS = {
    "name": str, "status": str, "criteria": dict, "tasks": dict,
    "rework_requests": int, "rework_per_task": (int, float), "blocked_share": (int, float),
    "first_attempt_rate": (int, float), "wall_clock_seconds": (int, float),
    "tokens": dict, "models": dict,
}
_TOTAL_FIELDS = (
    "specs", "specs_completed", "criteria_passed", "criteria_total", "criteria_rate", "tasks",
    "done_first_attempt", "first_attempt_rate", "rework_per_task", "blocked_share",
    "wall_clock_seconds", "tokens_reported",
)


def validate(card: Any) -> list[str]:
    """What is wrong with *card* against the schema; empty when it is sound."""
    if not isinstance(card, dict):
        return ["the scorecard is not an object"]
    problems = []
    if card.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version is {card.get('schema_version')!r}, expected {SCHEMA_VERSION}")
    for key in ("stamp", "commit", "llm"):
        if not isinstance(card.get(key), str) or not card.get(key):
            problems.append(f"{key} must be a non-empty string")
    specs = card.get("specs")
    if not isinstance(specs, list):
        return [*problems, "specs must be a list"]
    for index, spec in enumerate(specs):
        where = f"specs[{index}]"
        if not isinstance(spec, dict):
            problems.append(f"{where} is not an object")
            continue
        for key, kind in _SPEC_FIELDS.items():
            if not isinstance(spec.get(key), kind) or isinstance(spec.get(key), bool):
                problems.append(f"{where}.{key} is missing or of the wrong type")
        if spec.get("status") not in SPEC_STATUSES:
            problems.append(f"{where}.status must be one of {', '.join(SPEC_STATUSES)}")
        criteria = spec.get("criteria") if isinstance(spec.get("criteria"), dict) else {}
        results = criteria.get("results") if isinstance(criteria.get("results"), dict) else None
        if results is None:
            problems.append(f"{where}.criteria.results must be an object")
        else:
            if any(r not in (PASS, FAIL, MISSING) for r in results.values()):
                problems.append(f"{where}.criteria.results holds a value other than pass, fail, missing")
            if criteria.get("total") != len(results):
                problems.append(f"{where}.criteria.total does not match its results")
            if criteria.get("passed") != sum(1 for r in results.values() if r == PASS):
                problems.append(f"{where}.criteria.passed does not match its results")
        tasks = spec.get("tasks") if isinstance(spec.get("tasks"), dict) else {}
        if not all(isinstance(tasks.get(k), int) for k in ("total", "done", "blocked", "done_first_attempt")):
            problems.append(f"{where}.tasks needs integer total, done, blocked, done_first_attempt")
        elif not tasks["done_first_attempt"] <= tasks["done"] <= tasks["total"]:
            problems.append(f"{where}.tasks: done_first_attempt <= done <= total does not hold")
    totals = card.get("totals")
    if not isinstance(totals, dict):
        problems.append("totals must be an object")
    else:
        problems.extend(f"totals.{k} is missing" for k in _TOTAL_FIELDS if k not in totals)
    return problems


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def render_markdown(card: dict[str, Any]) -> str:
    t = card["totals"]
    lines = [
        f"# Bench scorecard {card['stamp']}",
        "",
        f"Factory commit `{card['commit']}`, models from `{card['llm']}`, schema v{card['schema_version']}.",
        "Criteria are graded by the acceptance tests a person wrote for each specification;",
        "tokens count only the turns whose usage the provider reported.",
        "",
        "| Spec | Status | Criteria | First attempt | Rework / task | Blocked | Wall-clock | Tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in card["specs"]:
        lines.append(
            f"| {s['name']} | {s['status']} | {s['criteria']['passed']}/{s['criteria']['total']} "
            f"| {s['tasks']['done_first_attempt']}/{s['tasks']['total']} | {s['rework_per_task']:.2f} "
            f"| {_pct(s['blocked_share'])} | {s['wall_clock_seconds']:.0f} s | {s['tokens']['reported']} |"
        )
    lines.append(
        f"| **Total** | {t['specs_completed']}/{t['specs']} completed | {t['criteria_passed']}/{t['criteria_total']} "
        f"| {t['done_first_attempt']}/{t['tasks']} | {t['rework_per_task']:.2f} | {_pct(t['blocked_share'])} "
        f"| {t['wall_clock_seconds']:.0f} s | {t['tokens_reported']} |"
    )
    for s in card["specs"]:
        failing = [c for c, r in s["criteria"]["results"].items() if r != PASS]
        models = "; ".join(f"{role}: {', '.join(m)}" for role, m in s["models"].items()) or "none recorded"
        lines += ["", f"## {s['name']}", "", f"- Models served: {models}"]
        if s.get("factory_criteria_verified") is not None:
            lines.append(
                f"- The factory's own verified criteria: {s['factory_criteria_verified']} "
                "(its Verifier, beside the person-written grade)"
            )
        lines.append(f"- Criteria not passing: {', '.join(failing) if failing else 'none'}")
        if s.get("error"):
            lines.append(f"- Error: {s['error']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Collection (the runner's side: the API and the audit log)
# ---------------------------------------------------------------------------


def _get(api: str, path: str) -> Any:
    with urllib.request.urlopen(f"{api}{path}", timeout=30) as response:  # noqa: S310 - the bench's own backend.
        return json.loads(response.read())


def reported_tokens(dsn: dict[str, str], company_id: int) -> tuple[dict[str, int], dict[str, list[str]]]:
    """The company's KPI turns in the audit log: tokens of the reported ones,
    how many were and were not reported, and the model served per role."""
    import psycopg2  # the backend's environment has it

    tokens = {"reported": 0, "turns_reported": 0, "turns_unreported": 0}
    models: dict[str, list[str]] = {}
    with psycopg2.connect(**dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT details->>'agent_role', details->>'model', details->>'tokens', cost_tokens "
            "FROM audit_log WHERE company_id = %s AND details->>'tokens' IS NOT NULL",
            (company_id,),
        )
        for role, model, status, cost in cur.fetchall():
            if status == "reported":
                tokens["reported"] += int(cost or 0)
                tokens["turns_reported"] += 1
            else:
                tokens["turns_unreported"] += 1
            if role and model:
                models.setdefault(role, []).append(model)
    return tokens, models


def collect(args: argparse.Namespace) -> dict[str, Any]:
    graded = json.loads(Path(args.grade).read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    runs: dict[int, int] = {}
    rework, verified, error = 0, None, args.error or None
    tokens: dict[str, int] = {}
    models: dict[str, list[str]] = {}
    try:
        if args.sprint:
            board = _get(args.api, f"/v1/sprints/{args.sprint}/board")
            items = [i for column in board["columns"].values() for i in column]
            for item in items:
                found = _get(args.api, f"/v1/agent_runs?company_id={args.company}&external_ref=task-{item['id']}")
                runs[item["id"]] = len(found.get("agent_runs") or [])
            sprints = _get(args.api, f"/v1/sprints?company_id={args.company}")
            rework = next((s["rework"] for s in sprints if s["id"] == args.sprint), 0)
        if args.spec_id:
            spec = _get(args.api, f"/v1/specifications/{args.spec_id}")
            verified = (spec.get("coverage") or {}).get("verified")
        if args.company:
            dsn = dict(zip(("host", "port", "user", "password", "dbname"), args.pg, strict=True))
            tokens, models = reported_tokens(dsn, args.company)
    except Exception as exc:  # noqa: BLE001 - a failed collection is recorded, not hidden.
        error = f"{error + '; ' if error else ''}collection failed: {exc}"
    return summarize_spec(
        name=args.name, status=args.status, items=items, runs_per_task=runs,
        rework_requests=rework, graded=graded, factory_verified=verified,
        wall_clock_seconds=args.wall, tokens=tokens, models=models, error=error,
    )


def _commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grade")
    g.add_argument("--spec", required=True, type=Path)
    g.add_argument("--project", required=True, type=Path)
    g.add_argument("--python", default=sys.executable)
    g.add_argument("--out", required=True, type=Path)
    c = sub.add_parser("collect")
    c.add_argument("--api", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--status", required=True, choices=SPEC_STATUSES)
    c.add_argument("--company", type=int, default=0)
    c.add_argument("--sprint", type=int, default=0)
    c.add_argument("--spec-id", type=int, default=0)
    c.add_argument("--grade", required=True)
    c.add_argument("--wall", type=float, default=0.0)
    c.add_argument("--pg", nargs=5, metavar=("HOST", "PORT", "USER", "PASSWORD", "DB"))
    c.add_argument("--error", default="")
    c.add_argument("--out", required=True, type=Path)
    w = sub.add_parser("write")
    w.add_argument("--out-dir", required=True, type=Path)
    w.add_argument("--llm", default="fake")
    w.add_argument("--stamp", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    w.add_argument("entries", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.command == "grade":
        args.out.write_text(json.dumps(grade(args.spec, args.project, args.python), indent=2))
        return 0
    if args.command == "collect":
        args.out.write_text(json.dumps(collect(args), indent=2))
        return 0
    entries = [json.loads(p.read_text(encoding="utf-8")) for p in args.entries]
    card = build_scorecard(entries, stamp=args.stamp, commit=_commit(), llm=args.llm)
    problems = validate(card)
    if problems:
        print("the scorecard does not match its schema:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{args.stamp}.json").write_text(json.dumps(card, indent=2) + "\n")
    (args.out_dir / f"{args.stamp}.md").write_text(render_markdown(card))
    print(args.out_dir / f"{args.stamp}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
