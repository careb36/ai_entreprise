#!/usr/bin/env python3
"""The factory builds itself: a La Fabrique company whose project is La Fabrique (issue #329).

Runs against any running stack, the e2e one (``scripts/e2e/run.sh``, with the
fake model) or a real one (``scripts/self_host/README.md``), through the
public API only. Every step is idempotent: run it again and it finds what it
made (the company in the state file, the specifications and architectures by
title) instead of making it twice.

Steps (``--steps``, comma-separated; ``all`` by default):

- ``checkout``: a snapshot of this repository (the files git tracks or would
  track, as one commit of a fresh repository) at ``--checkout``. Never the
  live checkout, and never a ``git worktree`` of it: the Git workflow writes a
  branch per task, and a worktree shares the live repository's branches.
- ``company``: the "La Fabrique" company with that snapshot as its project,
  the Git workflow on (the daemon observes a repository), the delivery tests
  on (the stack profile names La Fabrique's suites per folder and the checks
  every delivery runs, or with ``--profile sprint-tests`` one root command),
  the sandbox set to ``--sandbox``, and the roles of the loop.
- ``records``: ``docs/architecture/specification.json`` and
  ``architecture.json`` posted and approved as the company's specification and
  architecture; the architecture's document and ADRs read back through the
  API and compared with ``docs/ARCHITECTURE.md`` and ``docs/adr/`` in the
  snapshot, then written into it by the daemon and compared with git.
- ``issue``: issue #535 as a specification (``fixtures/``), or with
  ``--spec FILE`` a finding's, checked first by the product's ``spec_check``
  (``spec_source.py``), approved; the Architect's revision for it as the
  next version of La Fabrique's one architecture (#469's covers kept by the
  product, the change adding the new ones), approved; the PO's plan,
  acceptance tests first; the sprint started on the plan.
- ``wait``: until that sprint completes (``--timeout``).
- ``review``: the tasks waiting for a person, the task branches and the
  sprint's integration (its branches merged and the suite run, the one
  request a person reviews); with ``--accept TEXT`` a person accepts the
  held tasks with that comment, and with ``--merge`` records an approving
  review of the integration (the ``--accept`` comment as its text) and merges
  it into ``main`` locally (nothing is pushed: the snapshot has no remote).
- ``pack``: the documentation pack (#530) compiled in English and in Spanish,
  with what each could not say.
- ``measure`` (by name only, with ``--spec``): the file's target measure run
  on ``--build``'s backend (the snapshot by default); exit status 3 if it fails.

It prints a JSON report (also written to ``--report``) that the e2e checks
read and the retrospective cites. Taken from OpenHands, which develops
OpenHands with OpenHands: run the product on its own repository through the
same entry points a user has (design only, no code).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE))  # spec_source beside this file, whether run or imported
import spec_source
from spec_source import revised_architecture, step_measure  # noqa: F401 - a step; re-exported

COMPANY_NAME = "La Fabrique"
ADR_FIRST_NUMBER_KEY = "adr_first_number"
STEPS = ("checkout", "company", "records", "issue", "wait", "review", "pack")  # "all"; "measure" by name
AGENTS = [
    {"role": "PO", "name": "Po Fab"},
    {"role": "Architect", "name": "Ari Fab"},
    {"role": "Developer", "name": "Dev Fab"},
    {"role": "QA", "name": "QA Fab"},
    {"role": "ScrumMaster", "name": "Sam Fab"},
    {"role": "TechWriter", "name": "Tess Fab"},
]
IDENTITY = ["-c", "user.name=La Fabrique self-hosting", "-c", "user.email=self-host@localhost"]
# La Fabrique's suites per folder, as CI runs them (.github/workflows/). The
# daemon's tests live in the backend's suite, so a change to the daemon runs it.
_BACKEND_TESTS = "PYTHONPATH=.:app poetry run python -m pytest tests -q -p no:cacheprovider"
LA_FABRIQUE_FOLDERS = {
    "backend": {"stack": "python", "install": "poetry install --no-interaction", "test": _BACKEND_TESTS},
    "frontend": {
        "stack": "node",
        "install": "npm ci --no-audit --no-fund",
        "test": "npx tsc --noEmit && npm run lint && npm run test:run",
    },
    "llm-proxy": {
        "stack": "python",
        "install": "poetry install --no-interaction --no-root",
        "test": "poetry run python -m pytest -q -p no:cacheprovider",
    },
    "vscode-daemon": {"stack": "python", "install": "", "test": f"cd ../backend && {_BACKEND_TESTS}"},
}
# What CI checks on every change, whatever it touched.
EVERY_DELIVERY = (
    "python3 scripts/check_architecture.py",
    "python3 scripts/check_frontend_architecture.py",
    "python3 scripts/check_file_sizes.py",
)
# Every local git call is bounded: a repository snapshot of a few thousand files
# takes seconds, so a call still running after this is a hung host, and the
# harness fails naming the command instead of waiting for the CI job's timeout.
GIT_TIMEOUT_SECONDS = 120


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str):
        super().__init__(f"{method} {path} -> {status}: {body[:500]}")
        self.status = status


class Api:
    def __init__(self, base: str, token: str | None):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body=None, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise ApiError(method, path, e.code, e.read().decode("utf-8", "replace")) from e
        return text if raw else (json.loads(text) if text else None)

    def get(self, path, raw=False):
        return self.call("GET", path, raw=raw)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def put(self, path, body):
        return self.call("PUT", path, body)

    def patch(self, path, body):
        return self.call("PATCH", path, body)


class GitTimeoutError(RuntimeError):
    """A local git command did not finish within ``GIT_TIMEOUT_SECONDS``."""


def _git(cwd: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *IDENTITY, *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except subprocess.TimeoutExpired as e:
        command = " ".join(["git", *args])
        raise GitTimeoutError(
            f"{command} (in {cwd}) did not finish within {GIT_TIMEOUT_SECONDS} s"
        ) from e


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- steps


def step_checkout(args, state, report):
    target = Path(args.checkout).resolve()
    if target == REPO or REPO in target.parents:
        raise SystemExit("the checkout must be outside this repository")
    if (target / ".git").exists():
        report["checkout"] = {"path": str(target), "created": False, "head": _git(target, "rev-parse", "HEAD").strip()}
        return
    files = _git(REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    copied = 0
    for name in filter(None, files):
        source = REPO / name
        if not source.is_file():
            continue  # deleted in the working tree, or a submodule
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / name)
        copied += 1
    _git(target, "init", "-q", "-b", "main")
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "La Fabrique, as the self-hosting company's project")
    report["checkout"] = {
        "path": str(target),
        "created": True,
        "files": copied,
        "head": _git(target, "rev-parse", "HEAD").strip(),
        "source_head": _git(REPO, "rev-parse", "HEAD").strip(),
    }


def _company_exists(api: Api, company_id) -> bool:
    if not company_id:
        return False
    try:
        api.get(f"/v1/companies/{company_id}/git")
        return True
    except ApiError as e:
        if e.status == 404:
            return False
        raise


def step_company(api, args, state, report):
    checkout = str(Path(args.checkout).resolve())
    created = False
    if not _company_exists(api, state.get("company_id")):
        result = api.post(
            "/v1/init_company",
            {
                "company_name": COMPANY_NAME,
                "description": "La Fabrique, built by La Fabrique (issue #329).",
                "project_path": checkout,
                "autonomy_level": args.autonomy,
                "start_now": False,
                "git_workflow": True,
                "git_base_branch": "main",
                "doc_language": "en",
                "agents": AGENTS,
            },
        )
        state["company_id"] = result["company_id"]
        # An existing repository gets no "Sprint 0 — Foundations" (retrospective F8).
        state["foundation_sprint"] = result.get("foundation_sprint")
        state.pop("sprint_id", None)
        created = True
    cid = state["company_id"]
    git = api.put(f"/v1/companies/{cid}/git", {"git_workflow": True, "git_base_branch": "main"})
    profile = api.put(f"/v1/companies/{cid}/stack_profile", {"profile": company_profile(args.profile, args.test_command)})
    sandbox = api.put(f"/v1/companies/{cid}/sandbox", {"mode": args.sandbox, "env": []})
    overrides = profile["overrides"]
    report["company"] = {
        "id": cid,
        "created": created,
        "foundation_sprint": state.get("foundation_sprint"),
        "git_workflow": git.get("git_workflow"),
        "git_base_branch": git.get("git_base_branch"),
        "profile": args.profile,
        "test_command": overrides.get("test"),
        "folders": {name: f.get("test") for name, f in (overrides.get("folders") or {}).items()},
        "every_delivery": [c["command"] for c in profile.get("every_delivery") or []],
        "sandbox": sandbox.get("mode"),
    }


def company_profile(mode: str, test_command: str) -> dict:
    """The company's stack profile (``PUT /v1/companies/{id}/stack_profile``).

    ``folders`` (the default, F5 of the self-hosting retrospective): La
    Fabrique's own suites per folder as CI runs them, each delivery running
    the suites of the folders the daemon saw it write in (a file outside them
    runs every one), and the architecture checks and the file-size ratchet on
    every delivery; the root runs no suite of its own.
    ``sprint-tests`` (the e2e's, whose sandbox has none of the repository's
    toolchains): one root command, the tests the sprint writes under tests/.
    Either way the root's install, lint, application and browser suite are
    off, each named off."""
    off = {"install": "", "lint": "", "start": "", "e2e": ""}
    if mode == "sprint-tests":
        return {"test": test_command, **off}
    return {"test": "", **off, "folders": LA_FABRIQUE_FOLDERS, "every_delivery": list(EVERY_DELIVERY)}


def _find_spec(api: Api, cid: int, title: str) -> dict | None:
    rows = api.get(f"/v1/specifications?company_id={cid}")["specifications"]
    return next((s for s in rows if s["title"] == title and s["status"] != "superseded"), None)


def _approved_spec(api: Api, cid: int, record: dict) -> dict:
    body = {k: record.get(k) for k in ("title", "summary", "requirements", "quality", "constraints")}
    spec = _find_spec(api, cid, record["title"]) or api.post(
        "/v1/specifications", {"company_id": cid, **{k: v for k, v in body.items() if v is not None}}
    )
    if spec["status"] == "draft":
        spec = api.post(f"/v1/specifications/{spec['id']}/approve")
    return spec


def _approved_architecture(api: Api, spec_id: int, record: dict) -> dict:
    """The version of the company's architecture with the record's title, found
    again whatever its status (a later version supersedes it), else posted."""
    rows = api.get(f"/v1/architectures?specification_id={spec_id}")["architectures"]
    arch = next((a for a in rows if a["title"] == record["title"]), None)
    if arch is None:
        body = {k: record[k] for k in ("title", "overview", "modules", "interfaces", "decisions", "conventions") if k in record}
        arch = api.post("/v1/architectures", {"specification_id": spec_id, **body})
    if arch["status"] == "draft":
        arch = api.post(f"/v1/architectures/{arch['id']}/approve")
    return arch


def step_records(api, args, state, report):
    cid = state["company_id"]
    checkout = Path(args.checkout).resolve()
    spec_record = _load(REPO / "docs/architecture/specification.json")
    arch_record = _load(REPO / "docs/architecture/architecture.json")
    spec = _approved_spec(api, cid, spec_record)
    arch = _approved_architecture(api, spec["id"], arch_record)
    state["specification_id"], state["architecture_id"] = spec["id"], arch["id"]
    document = api.get(f"/v1/architectures/{arch['id']}/document", raw=True)
    first = int(arch_record.get(ADR_FIRST_NUMBER_KEY) or 1)
    adrs = api.get(f"/v1/architectures/{arch['id']}/adrs?first_number={first}")["files"]
    adr_diffs = [
        a["path"]
        for a in adrs
        if not (checkout / a["path"]).is_file() or (checkout / a["path"]).read_text(encoding="utf-8") != a["markdown"]
    ]
    # Only the current version is written; run again after the #535 version
    # superseded it, the file the first run wrote is the one checked.
    written = api.post(f"/v1/architectures/{arch['id']}/write") if arch["status"] == "approved" else arch
    dirty = _git(checkout, "status", "--porcelain", "--", "docs/ARCHITECTURE.md", "docs/adr").strip()
    report["records"] = {
        "specification_id": spec["id"],
        "specification_status": spec["status"],
        "specification_blocking": spec["check"]["blocking"],
        "architecture_id": arch["id"],
        "architecture_status": arch["status"],
        "architecture_blocking": arch["check"]["blocking"],
        "modules": len(arch["modules"]),
        "decisions": len(arch["decisions"]),
        "document_matches": document == (checkout / "docs/ARCHITECTURE.md").read_text(encoding="utf-8"),
        "adrs": [a["path"] for a in adrs],
        "adr_differences": adr_diffs,
        "written_path": written.get("written_path"),
        "written_adrs": [f["path"] for f in (written.get("written_files") or []) if f.get("kind") == "adr"],
        "written_unchanged_in_git": dirty == "",
    }


def _revised_version(api: Api, cid: int, spec_id: int, change: dict) -> dict:
    """The next version of the company's architecture for *spec_id*: opened by
    the company's revision route, the fixture's change written into it, approved.
    Found again by title on a later run; an open draft is taken up."""
    view = api.get(f"/v1/companies/{cid}/architecture")
    found = next((v for v in view["versions"] if v["title"] == change["title"]), None)
    if found is not None:
        arch = api.get(f"/v1/architectures/{found['id']}")
    else:
        try:
            draft = api.post(f"/v1/companies/{cid}/architecture/revisions", {"specification_id": spec_id})
        except ApiError as exc:
            if exc.status != 409 or not (view.get("draft") or {}).get("id"):
                raise
            draft = api.get(f"/v1/architectures/{view['draft']['id']}")
        arch = api.put(f"/v1/architectures/{draft['id']}", revised_architecture(draft, change))
    if arch["status"] == "draft":
        arch = api.post(f"/v1/architectures/{arch['id']}/approve")
    return arch


def step_issue(api, args, state, report):
    cid, source = state["company_id"], args.source  # spec_source.load: #535, or --spec
    spec = _approved_spec(api, cid, source["spec"])
    arch = _revised_version(api, cid, spec["id"], source["change"])
    state["issue_specification_id"], state["issue_architecture_id"] = spec["id"], arch["id"]
    view = api.get(f"/v1/companies/{cid}/architecture")
    earlier = str(state.get("specification_id"))
    out = {
        "specification_id": spec["id"],
        "criteria": [c["id"] for r in spec["requirements"] for c in r["criteria"]],
        "specification_findings": [(f["severity"], f["where"], f["code"]) for f in spec["check"]["findings"]],
        "architecture_id": arch["id"],
        "architecture_findings": [(f["severity"], f["where"], f["code"]) for f in arch["check"]["findings"]],
        "new_decision": arch["decisions"][-1]["id"],
        # One architecture record for both specifications (follow-up 3).
        "architecture_version": arch["version"],
        "specification_ids": arch["specification_ids"],
        "versions": [(v["version"], v["status"]) for v in view["versions"]],
        "current_id": (view.get("current") or {}).get("id"),
        "earlier_covers_kept": sum(1 for m in arch["modules"] if (m.get("earlier_covers") or {}).get(earlier)),
        "planned_against": api.get(f"/v1/specifications/{spec['id']}").get("architecture_id"),
        **{k: source[k] for k in ("source", "target_measure", "warnings") if k in source},
    }
    if not state.get("sprint_id"):
        plan = api.post(
            "/v1/plan_sprint", {"company_id": cid, "scope_size": args.scope, "specification_id": spec["id"]}
        )
        out["plan"] = [
            {"title": t["title"], "kind": t.get("kind"), "assignee": t.get("assignee"), "covers": t.get("covers"), "depends_on": t.get("depends_on")}
            for t in plan["tasks"]
        ]
        out["uncovered"] = plan.get("uncovered")
        started = api.post(
            "/v1/start_sprint",
            {
                "company_id": cid,
                "sprint_name": source["sprint_name"],
                "start_date": time.strftime("%Y-%m-%d"),
                "scope_size": args.scope,
                "specification_id": spec["id"],
                "team": plan.get("suggested_team"),
                "suggested_team": plan.get("suggested_team"),
                "backlog_items": plan["tasks"],
            },
        )
        state["sprint_id"] = started["sprint_id"]
    out["sprint_id"] = state["sprint_id"]
    report["issue"] = out


def _sprint(api: Api, cid: int, sprint_id: int) -> dict:
    return next((s for s in api.get(f"/v1/sprints?company_id={cid}") if s["id"] == sprint_id), {})


def step_wait(api, args, state, report):
    cid, sid = state["company_id"], state["sprint_id"]
    deadline = time.monotonic() + args.timeout
    status = ""
    while time.monotonic() < deadline:
        status = _sprint(api, cid, sid).get("status", "")
        if status == "completed":
            break
        time.sleep(3)
    board = api.get(f"/v1/sprints/{sid}/board")
    report["wait"] = {
        "sprint_id": sid,
        "status": status,
        "board": sorted(f"{i['title']}={i['status']}" for c in board["columns"].values() for i in c),
    }


def _settled_integration(api: Api, sid: int, timeout: int) -> tuple[dict, dict]:
    """The sprint's integration plan and its latest integration, once that one
    has finished: the sprint reads "completed" before its integration (a job,
    ``integration_jobs.OPEN``) has merged the branches and run the suite."""
    start = time.monotonic()
    while True:
        integration = api.get(f"/v1/sprints/{sid}/integration")
        latest = integration.get("latest") or {}
        waited = time.monotonic() - start
        # None yet: the job may not have been queued; give it a minute.
        if not latest and waited > min(60, timeout):
            return integration, latest
        if latest and latest.get("status") not in ("pending", "running"):
            return integration, latest
        if waited > timeout:
            return integration, latest
        time.sleep(3)


def step_review(api, args, state, report):
    cid, sid = state["company_id"], state["sprint_id"]
    checkout = Path(args.checkout).resolve()
    branches = [b.strip(" *+") for b in _git(checkout, "branch", "--list", "fabrique/*").splitlines()]
    held = [t for t in api.get(f"/v1/inbox?company_id={cid}")["blocked_tasks"] if t.get("sprint_id") == sid]
    accepted = []
    if args.accept:
        # Only what QA approved and the risk tier holds for a person; a task
        # blocked for triage (its tests failed) is carried over, not accepted.
        for task in (t for t in held if t.get("risk_hold")):
            api.patch(f"/v1/backlog/{task['id']}", {"status": "done", "comment": args.accept})
            accepted.append(task["id"])
    integration, latest = _settled_integration(api, sid, args.timeout)
    if args.accept and not latest:
        # The worker integrates on its own when the sprint closes; tasks a
        # person accepted after that are integrated now, on the person's word.
        try:
            api.post(f"/v1/sprints/{sid}/integrate")
        except ApiError as e:
            report.setdefault("notes", []).append(str(e))
        integration, latest = _settled_integration(api, sid, args.timeout)
    merged = reviewed = None
    if args.merge and latest:
        # The person's reading is recorded before the merge (F7): an approving
        # review of the integration, with the --accept comment when given. It
        # may be refused (segregation of duties: the harness started the
        # sprint); the merge is then asked anyway and answers for itself.
        try:
            reviewed = api.post(
                f"/v1/sprints/{sid}/integration/reviews",
                {"integration_id": latest.get("id"), "event": "approve", "body": args.accept or "Read the integration's diff."},
            )
        except ApiError as e:
            reviewed = {"refused": str(e)}
        try:
            merged = api.post(f"/v1/sprints/{sid}/integration/merge")
        except ApiError as e:
            merged = {"refused": str(e)}
    report["review"] = {
        "branches": branches,
        "waiting_for_a_person": [
            {"id": t["id"], "title": t["title"], "risk_hold": t.get("risk_hold"), "reason": t.get("reason")} for t in held
        ],
        "accepted": accepted,
        "integration": {k: latest.get(k) for k in ("id", "status", "trigger", "commit", "branch") if latest},
        "integration_plan": {k: v for k, v in integration.items() if k not in ("latest", "history")},
        "integration_review": reviewed,
        "merged": merged,
        "base_log": _git(checkout, "log", "--oneline", "-5", "main").splitlines(),
    }


def _pack_summary(pack: dict) -> dict:
    return {
        "id": pack["id"],
        "language": pack["language"],
        "documents": [d["key"] for d in pack["documents"]],
        "empty_documents": [d["key"] for d in pack["documents"] if not d["markdown"].strip()],
        "missing_sections": sum(len(d["missing"]) for d in pack["documents"]),
        "missing": {d["key"]: d["missing"] for d in pack["documents"] if d["missing"]},
        "claims": sum(len(d["claims"]) for d in pack["documents"]),
        "persons": sum(len(d["persons"]) for d in pack["documents"]),
        "diagrams": len(pack["diagrams"]),
        "lint_blocking": sum(1 for f in pack["lint"]["findings"] if f["severity"] == "blocking"),
        "lint_warnings": sum(1 for f in pack["lint"]["findings"] if f["severity"] == "warning"),
        # The documents with a section per approved specification (F3: #469's and #535's).
        "per_specification": [d["key"] for d in pack["documents"] if "specifications" in (d.get("inputs") or [])],
    }


def step_pack(api, args, state, report):
    cid = state["company_id"]
    packs = {}
    for language in ("en", "es"):
        api.put(f"/v1/companies/{cid}/doc_language", {"doc_language": language})
        packs[language] = _pack_summary(
            api.post(f"/v1/companies/{cid}/documentation", {"prose": args.prose})
        )
    api.put(f"/v1/companies/{cid}/doc_language", {"doc_language": "en"})
    report["pack"] = packs


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default=os.environ.get("FABRIQUE_API", "http://127.0.0.1:8000"))
    parser.add_argument("--checkout", required=True, help="where the snapshot of this repository goes (on the daemon's computer)")
    parser.add_argument("--state", help="the state file (default: <checkout>.self_host.json)")
    parser.add_argument("--report", help="also write the JSON report here")
    parser.add_argument("--steps", default="all")
    parser.add_argument("--sandbox", default="container", choices=("host", "container", "bwrap"))
    parser.add_argument(
        "--profile",
        default="folders",
        choices=("folders", "sprint-tests"),
        help="company: La Fabrique's suites per folder and its every-delivery checks, or one root command (--test-command)",
    )
    parser.add_argument(
        "--test-command",
        default="python -m pytest -q -p no:cacheprovider tests",
        help="company: the root's one command, with --profile sprint-tests",
    )
    parser.add_argument("--autonomy", type=int, default=2, help="2: QA's approval closes a low-risk task; a high-risk one waits for a person")
    parser.add_argument("--scope", default="M")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--accept", help="review: accept the tasks waiting for a person, with this comment")
    parser.add_argument("--merge", action="store_true", help="review: merge the sprint's integration into main, locally, once a person has read it")
    parser.add_argument("--prose", action="store_true", help="pack: ask the TechWriter for the charter's background")
    parser.add_argument("--spec", help="issue, measure: a finding's specification file, checked first (spec_source.py)")
    parser.add_argument("--revision", help="issue: its architecture change (default: <name>-architecture.json beside it)")
    parser.add_argument("--build", help="measure: the build whose backend/ the measure runs on (default: --checkout)")
    parser.add_argument("--python", default=sys.executable, help="measure: a Python with the backend's dependencies")
    args = parser.parse_args(argv)
    steps = STEPS if args.steps == "all" else tuple(s.strip() for s in args.steps.split(","))
    unknown = [s for s in steps if s not in (*STEPS, "measure")]
    if unknown:
        parser.error(f"unknown step(s): {', '.join(unknown)}")
    if "measure" in steps and not args.spec:
        parser.error("the measure step needs --spec: the target measure is the specification's")
    try:  # Before any call: a refused file posts nothing.
        args.source = spec_source.load(args.spec, args.revision)
    except spec_source.SpecRefused as e:
        parser.error(str(e))
    state_file = Path(args.state or f"{Path(args.checkout).resolve()}.self_host.json")
    state = _load(state_file) if state_file.exists() else {}
    api = Api(args.api, os.environ.get("FABRIQUE_API_TOKEN"))
    report: dict = {"steps": list(steps)}
    try:
        for step in steps:
            if step == "checkout":
                step_checkout(args, state, report)
            else:
                globals()[f"step_{step}"](api, args, state, report)
            state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except ApiError as e:
        report["error"] = str(e)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    failed = "measure" in report and not report["measure"]["passed"]
    return 1 if "error" in report else 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
