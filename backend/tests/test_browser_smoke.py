"""QA's browser smoke test per criterion (issue #315, ``services/browser_smoke.py``).

A project that serves pages gets, besides the unit acceptance tests, one
Playwright smoke test per criterion a person sees in the browser; the
application check runs them against the running app when no e2e command is
configured, reads them back per criterion, and reports a missing browser
runtime as unverifiable. The real runs use the Playwright and Chromium on
this machine when there are any, and are skipped otherwise.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import sys

import app_run  # the daemon's module, on sys.path via conftest
import pytest
import worker_stages
from config import settings
from models.sprint import BacklogItem, Sprint
from services import (
    app_check,
    browser_smoke,
    criteria_tests,
    delivery_checks,
    stack_profiles,
)
from services.workspace import project_profile
from test_specifications import _company

PY = sys.executable
SERVE = f'"{PY}" -m http.server $PORT --bind 127.0.0.1'


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "DELIVERY_INSTALL_COMMAND",
        "DELIVERY_LINT_COMMAND",
        "DELIVERY_START_COMMAND",
        "DELIVERY_E2E_COMMAND",
        "DELIVERY_TEST_COMMAND",
    ):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "DELIVERY_APP_PORT", 0)
    monkeypatch.setattr(settings, "DELIVERY_APP_PROBE_PATH", "/")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", True)
    for name in ("FABRIQUE_COMMAND_DENY", "FABRIQUE_SANDBOX_REQUIRED"):
        monkeypatch.delenv(name, raising=False)


def _files(root, names: dict[str, str]) -> str:
    for name, text in names.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return str(root)


# ---------------------------------------------------------------------------
# Which projects serve pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "files, web",
    [
        ({"package.json": '{"dependencies": {"react": "18.3.1"}}'}, True),
        ({"package.json": '{"devDependencies": {"vite": "5.4.0"}}'}, True),
        ({"package.json": '{"dependencies": {"express": "4.21.0"}}'}, False),
        ({"package.json": "{}", "public/index.html": "<h1>hi</h1>"}, True),
        ({"requirements.txt": "flask==3.0.3", "templates/home.html": "x"}, True),
        ({"requirements.txt": "fastapi==0.115.0", "main.py": "x"}, False),
        ({"go.mod": "module x", "index.html": "<p>x</p>"}, True),
        ({"package.json": "not json"}, False),
    ],
)
def test_a_project_serves_pages_from_its_files(tmp_path, files, web):
    profile = stack_profiles.detect(_files(tmp_path, files))
    assert profile["web"] is web


def test_a_company_says_a_greenfield_project_serves_pages(tmp_path):
    assert stack_profiles.detect(str(tmp_path)) is None
    profile = stack_profiles.resolve(
        None, {"stack": "python", "start": "python app.py", "port": 8000, "web": True}
    )
    assert profile["web"] is True and browser_smoke.serves_web(profile)
    assert stack_profiles.resolve(None, {})["web"] is False
    with pytest.raises(ValueError, match="web must be true or false"):
        stack_profiles.validate_overrides({"web": "yes"})


def test_serving_pages_needs_the_check_a_start_command_and_a_port(monkeypatch):
    web = {"start": "npm start", "port": 3000, "web": True}
    assert browser_smoke.serves_web(web)
    assert not browser_smoke.serves_web({**web, "start": None})
    assert not browser_smoke.serves_web({**web, "port": None})
    assert not browser_smoke.serves_web({**web, "web": False})
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", False)
    assert not browser_smoke.serves_web(web)


# ---------------------------------------------------------------------------
# QA's prompt
# ---------------------------------------------------------------------------


def _test_task(session, profile, kind=criteria_tests.ACCEPTANCE_KIND, spec_id=7):
    company = _company(session)
    company.stack_profile = profile
    session.add(company)
    sprint = Sprint(
        company_id=company.id,
        sprint_name="Web",
        scope_size="S",
        specification_id=spec_id,
    )
    session.add(sprint)
    session.commit()
    item = BacklogItem(
        sprint_id=sprint.id,
        title="Acceptance tests for REQ-1",
        covers=["REQ-1.1", "REQ-1.2"],
        kind=kind,
    )
    session.add(item)
    session.commit()
    return item


class _NoDaemon:
    async def request(self, *_args, **_kwargs):
        raise TimeoutError("no daemon")


def _prompt(session, item, root):
    return asyncio.run(
        browser_smoke.with_browser_checks(
            _NoDaemon(),
            session.get_bind(),
            item.id,
            lambda _cid: str(root),
            "REQS",
            project_profile.resolved,
        )
    )


WEB = {"stack": "python", "start": "python app.py", "port": 8000, "web": True}


def test_qa_is_asked_for_a_browser_test_per_criterion_in_a_web_project(
    sqlite_session, tmp_path
):
    text = _prompt(sqlite_session, _test_task(sqlite_session, WEB), tmp_path)
    assert text.startswith("REQS\n\n## Browser checks to write")
    assert "- REQ-1.1 → e2e/req_1_1.spec.ts" in text
    assert "- REQ-1.2 → e2e/req_1_2.spec.ts" in text
    assert "process.env.APP_URL" in text and browser_smoke.SMOKE_COMMAND in text


def test_the_project_s_own_files_make_it_a_web_project(sqlite_session, tmp_path):
    _files(
        tmp_path,
        {
            "package.json": '{"dependencies": {"vue": "3.5.0"}, '
            '"scripts": {"start": "vite"}}'
        },
    )
    text = _prompt(sqlite_session, _test_task(sqlite_session, {}), tmp_path)
    assert "## Browser checks to write" in text


@pytest.mark.parametrize(
    "profile, kind, spec_id, check_on",
    [
        ({**WEB, "web": False}, criteria_tests.ACCEPTANCE_KIND, 7, True),
        ({**WEB, "start": ""}, criteria_tests.ACCEPTANCE_KIND, 7, True),
        (WEB, None, 7, True),  # an implementation task writes no tests
        (WEB, criteria_tests.ACCEPTANCE_KIND, None, True),  # no specification
        (WEB, criteria_tests.ACCEPTANCE_KIND, 7, False),  # nothing would run them
    ],
)
def test_no_browser_tests_are_asked_for_otherwise(
    sqlite_session, tmp_path, monkeypatch, profile, kind, spec_id, check_on
):
    monkeypatch.setattr(settings, "DELIVERY_APP_CHECK_ENABLED", check_on)
    item = _test_task(sqlite_session, profile, kind=kind, spec_id=spec_id)
    assert _prompt(sqlite_session, item, tmp_path) == "REQS"


# ---------------------------------------------------------------------------
# Reading Playwright's output back per criterion
# ---------------------------------------------------------------------------

LIST_OUTPUT = """
Running 4 tests using 1 worker

  ✓  1 [chromium] › e2e/req_1_1.spec.ts:3:5 › REQ-1.1 shows the greeting (412ms)
  ✘  2 [chromium] › e2e/req_1_2.spec.ts:3:5 › REQ-1.2 greets the world (1.2s)
  ✓  3 e2e/req_2_1.spec.ts:3:5 › shows the count (20ms)
  ✓  4 e2e/req_1_2.spec.ts:9:5 › Greeting › REQ-1.3 is polite (20ms)

  1) [chromium] › e2e/req_1_2.spec.ts:3:5 › REQ-1.2 greets the world ─────
"""


def test_playwright_list_lines_are_read_back_per_criterion():
    assert criteria_tests.test_lines(LIST_OUTPUT) == [
        ("REQ-1.1 shows the greeting", True),
        ("REQ-1.2 greets the world", False),
        ("e2e/req_2_1.spec.ts", True),  # an untitled test speaks through its file
        ("Greeting › REQ-1.3 is polite", True),  # never for REQ-1.2, its file's
    ]
    assert criteria_tests.results_from_output(
        LIST_OUTPUT, ["REQ-1.1", "REQ-1.2", "REQ-1.3", "REQ-2.1", "REQ-3.1"]
    ) == {
        "REQ-1.1": "pass",
        "REQ-1.2": "fail",
        "REQ-1.3": "pass",
        "REQ-2.1": "pass",
        "REQ-3.1": "missing",
    }
    ascii_output = (
        "  ok 1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 a (5ms)\n"
        "  x  2 e2e/req_1_2.spec.ts:3:5 › REQ-1.2 b (5ms)\n"
    )
    assert criteria_tests.test_lines(ascii_output) == [
        ("REQ-1.1 a", True),
        ("REQ-1.2 b", False),
    ]


def _evidence(output, exit_code=1, kind=None, excused=(), truncated=False, **e2e):
    record = {
        "command": browser_smoke.SMOKE_COMMAND,
        "exit_code": exit_code,
        "timed_out": False,
        "output_tail": "summary only" if truncated else output,
        **e2e,
    }
    if truncated:
        record["output_head"] = output
    return {
        "acceptance": {
            "kind": kind,
            "covers": ["REQ-1.1"],
            "tested": ["REQ-1.1"],
            "all": ["REQ-1.1", "REQ-1.2"],
            "excused": list(excused),
            "test_hashes": {"e2e/req_1_1.spec.ts": "a", "tests/test_req_1.py": "b"},
        },
        "test_run": {"command": "pytest -v", "exit_code": 0, "criteria": {}},
        "app_run": {"command": "npm start", "port": 3000, "e2e": record},
    }


def test_the_browser_results_are_kept_per_criterion_from_the_head():
    """A long failure report pushes the per-test lines out of the tail."""
    evidence = _evidence(
        "  ✓  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 ok (5ms)\n"
        "  ✘  2 e2e/req_1_2.spec.ts:3:5 › REQ-1.2 no (5ms)\n",
        truncated=True,
    )
    browser_smoke.read_back(evidence)
    e2e = evidence["app_run"]["e2e"]
    assert e2e["criteria"] == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    assert e2e["failing_tests"] == ["REQ-1.2 no"]


def test_failures_of_criteria_not_implemented_yet_are_excused():
    evidence = _evidence(
        "  ✓  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 ok (5ms)\n"
        "  ✘  2 e2e/req_1_2.spec.ts:3:5 › REQ-1.2 no (5ms)\n",
        excused=["REQ-1.2"],
    )
    browser_smoke.read_back(evidence)
    check = browser_smoke.check_e2e(evidence)
    assert check["outcome"] == "pass"
    assert "not implemented yet failed (REQ-1.2)" in check["observation"]
    assert check["results"] == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
    criteria = criteria_tests.check_criteria(evidence)
    assert criteria["outcome"] == "pass" and criteria["results"] == {"REQ-1.1": "pass"}


def test_a_failing_browser_test_of_the_task_s_criterion_refutes_it():
    evidence = _evidence(
        "  ✘  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 shows it (5ms)\n", excused=["REQ-1.2"]
    )
    evidence["test_run"]["criteria"] = {"REQ-1.1": "pass"}
    browser_smoke.read_back(evidence)
    assert browser_smoke.check_e2e(evidence)["outcome"] == "refuted"
    # The unit test passed; the browser saw the criterion fail.
    assert criteria_tests.check_criteria(evidence)["results"] == {"REQ-1.1": "fail"}


@pytest.mark.parametrize(
    "output, exit_code, reason",
    [
        (
            (
                "  ✘  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 a (2ms)\n"
                "Error: browserType.launch: Executable doesn't exist at /x/chrome\n"
                "    npx playwright install\n"
            ),
            1,
            "browser Playwright drives could not be launched",
        ),
        (
            "Error: Cannot find module '@playwright/test'\n",
            1,
            "package is not installed",
        ),
        ("npm error could not determine executable to run\n", 1, "not installed"),
        (
            # npm 10+ (the self-hosted runner's), with no Playwright in the
            # project: npx resolves the name and refuses to install it.
            (
                "npm error npx canceled due to missing packages and no YES option: "
                '["playwright@1.65.0"]\n'
                "npm error A complete log of this run can be found in: /h/.npm/x.log\n"
            ),
            1,
            "not installed",
        ),
        ("npm ERR! canceled\n", 1, "not installed"),
        ("sh: 1: npx: not found\n", 127, "not installed"),
        ("", 127, "exit 127"),
        ("Host system is missing dependencies to run browsers.\n", 1, "libraries"),
    ],
)
def test_a_missing_browser_runtime_is_unverifiable_never_a_pass_or_a_refutation(
    output, exit_code, reason
):
    evidence = _evidence(output, exit_code=exit_code, excused=["REQ-1.1", "REQ-1.2"])
    browser_smoke.read_back(evidence)
    e2e = evidence["app_run"]["e2e"]
    assert reason in e2e["runtime_missing"] and "criteria" not in e2e
    check = browser_smoke.check_e2e(evidence)
    assert check["outcome"] == "unverifiable" and check["runtime_missing"] is True
    assert "observed nothing in a browser" in check["observation"]
    # The unit tests still decide the criterion; the browser said nothing.
    assert criteria_tests.check_criteria(evidence)["results"] == {"REQ-1.1": "missing"}


def test_a_passing_run_that_mentions_the_installer_is_a_pass():
    evidence = _evidence(
        "run `npx playwright install` first\n  1 passed\n", exit_code=0
    )
    browser_smoke.read_back(evidence)
    assert browser_smoke.check_e2e(evidence)["outcome"] == "pass"


# ---------------------------------------------------------------------------
# The smoke suite as the application check's e2e command
# ---------------------------------------------------------------------------


def test_the_smoke_suite_runs_when_qa_wrote_it_and_no_e2e_is_configured():
    evidence = _evidence("")
    web = {"start": "npm start", "port": 3000, "e2e": None}
    assert browser_smoke.smoke_files(evidence) == ["e2e/req_1_1.spec.ts"]
    assert (
        browser_smoke.default_command({}, web, evidence) == browser_smoke.SMOKE_COMMAND
    )
    assert (
        browser_smoke.default_command({}, {**web, "e2e": "npm run e2e"}, evidence)
        is None
    )
    assert browser_smoke.default_command({"e2e": ""}, web, evidence) is None
    evidence["acceptance"]["test_hashes"] = {"tests/test_req_1.py": "b"}
    assert browser_smoke.default_command({}, web, evidence) is None


class _Recorder:
    def __init__(self, reply):
        self.reply = reply
        self.requests: list[tuple[str, dict]] = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        return self.reply


@pytest.fixture()
def host_box(monkeypatch):
    from services import sandbox

    monkeypatch.setattr(
        sandbox, "request_for_sprint", lambda *_a, **_k: {"mode": "host"}
    )


def _ctx(nats, evidence, profile, overrides=None):
    ctx = project_profile.Context(nats, {"id": 1, "company_id": 1}, evidence, None)
    ctx._company = {"id": 1, "overrides": overrides or {}, "installs": {}}
    ctx._profile = {"stack": "node", "probe_path": "/", "source": {}, **profile}
    return ctx


def test_the_application_check_runs_the_smoke_suite_and_reads_it_back(host_box):
    evidence = _evidence("", excused=["REQ-1.2"])
    del evidence["app_run"]
    evidence["workspace_root"] = "/project"
    reply = {
        "started": True,
        "listening": True,
        "probe": {"url": "http://127.0.0.1:3000/", "status": 200},
        "e2e": {
            "command": browser_smoke.SMOKE_COMMAND,
            "exit_code": 0,
            "timed_out": False,
            "output_tail": "  ✓  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 shows it (5ms)\n",
        },
        "stopped": True,
    }
    nats = _Recorder(reply)
    ctx = _ctx(nats, evidence, {"start": "npm start", "port": 3000, "e2e": None})
    asyncio.run(delivery_checks.check_application(ctx))
    ((subject, payload),) = nats.requests
    assert subject == "workspace.app.check"
    assert payload["e2e_command"] == browser_smoke.SMOKE_COMMAND
    app = evidence["app_run"]
    assert app["e2e_source"] == "smoke" and app["e2e"]["criteria"] == {
        "REQ-1.1": "pass"
    }
    checks = {c["name"]: c for c in app_check.checks(evidence)}
    assert checks["e2e_run"]["outcome"] == "pass"


def test_qa_s_own_delivery_does_not_start_the_application(host_box):
    evidence = _evidence("", kind=criteria_tests.ACCEPTANCE_KIND)
    del evidence["app_run"]
    evidence["workspace_root"] = "/project"
    nats = _Recorder({})
    ctx = _ctx(nats, evidence, {"start": "npm start", "port": 3000, "e2e": None})
    asyncio.run(delivery_checks.check_application(ctx))
    assert nats.requests == []
    assert (
        evidence["app_run"]["skipped"]
        == "QA's acceptance tests change no application code"
    )
    assert app_check.checks(evidence) == []


def test_the_daemon_keeps_the_head_of_a_long_e2e_output():
    lines = "".join(
        f"  ✓  {i} e2e/a.spec.ts:1:1 › REQ-1.{i} t (1ms)\n" for i in range(9)
    )
    record = app_run._e2e_record(
        {"e2e": "x", "e2e_seconds": 5},
        {"stdout": lines + "detail\n" * 2000, "exit_code": 1},
    )
    assert record["output_truncated"] and record["output_head"].startswith(lines)
    short = app_run._e2e_record({"e2e": "x", "e2e_seconds": 5}, {"stdout": "ok"})
    assert "output_head" not in short


# ---------------------------------------------------------------------------
# A real browser run, where this machine has Playwright and Chromium
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _playwright_import(root) -> str | None:
    """The module a spec in *root* can import ``test`` from, if any, with a
    browser that can be launched."""
    if not shutil.which("npx") or not shutil.which("node"):
        return None
    for module in ("@playwright/test", "playwright/test"):
        probe = subprocess.run(
            ["node", "-e", f"require.resolve('{module}')"],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if probe.returncode == 0:
            break
    else:
        return None
    browser = subprocess.run(
        [
            "node",
            "-e",
            (
                "const fs=require('fs');process.exit("
                "fs.existsSync(require('playwright').chromium.executablePath())?0:1)"
            ),
        ],
        cwd=root,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return module if browser.returncode == 0 else None


def _spec(module: str, criterion: str, selector: str, text: str) -> str:
    return (
        f"import {{ test, expect }} from '{module}';\n"
        f"test('{criterion} shows it', async ({{ page }}) => {{\n"
        "  await page.goto(process.env.APP_URL + '/');\n"
        f"  await expect(page.locator('{selector}')).toHaveText('{text}', "
        "{ timeout: 3000 });\n"
        "});\n"
    )


@pytest.fixture()
def web_project(tmp_path):
    module = _playwright_import(tmp_path)
    if module is None:
        pytest.skip("no Playwright with a launchable Chromium on this machine")
    _files(
        tmp_path,
        {
            "index.html": "<html><body><h1>Hello, Ana</h1></body></html>",
            "e2e/req_1_1.spec.ts": _spec(module, "REQ-1.1", "h1", "Hello, Ana"),
            "e2e/req_1_2.spec.ts": _spec(module, "REQ-1.2", "#count", "2"),
        },
    )
    return tmp_path


def _real_check(root) -> dict:
    reply = app_run.check_application(
        {
            "workspace_root": str(root),
            "start_command": SERVE,
            "port": _free_port(),
            "e2e_command": browser_smoke.SMOKE_COMMAND,
            "ready_timeout_seconds": 20,
            "e2e_timeout_seconds": 180,
            "sandbox": {"mode": "host"},
        }
    )
    evidence = _evidence("", excused=["REQ-1.2"])
    evidence["app_run"] = {"command": SERVE, "port": 1, **reply}
    browser_smoke.read_back(evidence)
    return evidence


def test_a_real_browser_runs_qa_s_smoke_tests_against_the_running_app(web_project):
    evidence = _real_check(web_project)
    e2e = evidence["app_run"]["e2e"]
    assert e2e["criteria"] == {"REQ-1.1": "pass", "REQ-1.2": "fail"}, e2e
    check = browser_smoke.check_e2e(evidence)
    # REQ-1.2 is not implemented yet: its browser check fails, excused.
    assert check["outcome"] == "pass", check
    assert app_check.check_app(evidence)["outcome"] == "pass"


def test_a_real_run_without_its_browser_is_unverifiable(web_project, monkeypatch):
    empty = web_project / "no-browsers"
    empty.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(empty))
    evidence = _real_check(web_project)
    assert evidence["app_run"]["e2e"]["runtime_missing"]
    check = browser_smoke.check_e2e(evidence)
    assert check["outcome"] == "unverifiable", check


def test_the_smoke_command_downloads_nothing():
    """npx never fetches Playwright at check time: a missing one is reported."""
    assert browser_smoke.SMOKE_COMMAND.startswith("npx --no-install playwright test")


# ---------------------------------------------------------------------------
# Review findings on the first version (PR #422)
# ---------------------------------------------------------------------------


@pytest.fixture()
def review_db(monkeypatch, sqlite_session):
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    return sqlite_session


def _unobserved_item(session, autonomy):
    """An item in review whose latest run's browser checks could not run."""
    from models.agent_run import AgentRun, Verification
    from sqlmodel import select
    from test_risk_tier import _item_in_review

    item = _item_in_review(session, "low", autonomy=autonomy)
    run = session.exec(
        select(AgentRun).where(AgentRun.external_ref == f"task-{item.id}")
    ).one()
    verification = session.exec(
        select(Verification).where(Verification.agent_run_id == run.id)
    ).one()
    verification.verdict = "unverifiable"
    verification.checks = [
        *verification.checks,
        {
            "name": "e2e_run",
            "outcome": "unverifiable",
            "observation": "observed nothing in a browser",
            "runtime_missing": True,
        },
    ]
    run.status = "unverifiable"
    session.add_all([verification, run])
    session.commit()
    return item


@pytest.mark.parametrize("autonomy", [2, 3])
def test_a_delivery_no_browser_observed_waits_for_a_person_at_any_level(
    review_db, monkeypatch, autonomy
):
    """Finding 1: QA's approval alone never closes it, and it is not sent back."""
    from test_risk_tier import _approve as approve_in_review

    item = approve_in_review(
        monkeypatch, review_db, _unobserved_item(review_db, autonomy)
    )
    assert item.status == "blocked"
    hold = item.comments[-1]
    assert hold["author"] == "System"
    assert browser_smoke.UNOBSERVED_REASON in hold["text"]
    from services.tasks.board import awaits_risk_acceptance, pending_rework_feedback

    assert awaits_risk_acceptance(item.comments)
    assert not pending_rework_feedback(item.comments)


def test_a_delivery_a_browser_observed_closes_as_before(review_db, monkeypatch):
    from test_risk_tier import _approve as approve_in_review
    from test_risk_tier import _item_in_review

    item = approve_in_review(
        monkeypatch, review_db, _item_in_review(review_db, "low", autonomy=2)
    )
    assert item.status == "done"


def test_the_history_check_wants_a_person_on_an_unobserved_delivery(
    review_db, monkeypatch
):
    from history_check import check_task
    from test_risk_tier import _approve as approve_in_review

    item = approve_in_review(monkeypatch, review_db, _unobserved_item(review_db, 3))
    runs = [{"version": 1, "status": "unverifiable"}]
    # The thread as comments alone (the events are not what this checks).
    thread = [{k: v for k, v in c.items() if k != "event_id"} for c in item.comments]
    qa_only = {"id": item.id, "status": "done", "comments": thread}
    assert "high_risk_done_without_person" in check_task(qa_only, runs, [])
    accepted = {
        **qa_only,
        "comments": [
            *thread,
            {"author": "user", "text": "Accepted after reading the evidence."},
        ],
    }
    assert check_task(accepted, runs, []) == []


def _long_run(failing_lines: str, announced: int | None, **e2e) -> dict:
    head = (f"Running {announced} tests using 1 worker\n\n" if announced else "") + (
        failing_lines
    )
    evidence = _evidence(
        head, excused=["REQ-1.2"], truncated=True, output_truncated=True, **e2e
    )
    browser_smoke.read_back(evidence)
    return evidence


def test_failures_are_excused_only_when_the_failure_set_is_complete():
    """Finding 2: a failure outside what was kept never lets the suite pass."""
    lines = (
        "  ✓  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 ok (5ms)\n"
        "  ✘  2 e2e/req_1_2.spec.ts:3:5 › REQ-1.2 no (5ms)\n"
    )
    # Every announced test has its line: complete, excused.
    assert browser_smoke.check_e2e(_long_run(lines, 2))["outcome"] == "pass"
    # A third test ran whose line was not kept: never excused.
    assert browser_smoke.check_e2e(_long_run(lines, 3))["outcome"] == "refuted"
    # No announcement to count against: never excused.
    assert browser_smoke.check_e2e(_long_run(lines, None))["outcome"] == "refuted"
    # The daemon's own bound cut the stream: its head is not the start.
    cut = _long_run(lines, 2, stream_truncated=True)
    assert browser_smoke.check_e2e(cut)["outcome"] == "refuted"


def test_more_failures_than_are_kept_are_never_excused():
    lines = "".join(
        f"  ✘  {i} e2e/req_1_2.spec.ts:{i}:5 › REQ-1.2 case {i} (5ms)\n"
        for i in range(1, browser_smoke.FAILING_CAP + 2)
    )
    evidence = _evidence(lines, excused=["REQ-1.2"])
    browser_smoke.read_back(evidence)
    assert len(evidence["app_run"]["e2e"]["failing_tests"]) == browser_smoke.FAILING_CAP
    assert browser_smoke.check_e2e(evidence)["outcome"] == "refuted"


def test_the_daemon_marks_a_stream_its_bound_cut():
    record = app_run._e2e_record(
        {"e2e": "x", "e2e_seconds": 5},
        {"stdout": "tail", "exit_code": 1, "output_truncated": True},
    )
    assert record["stream_truncated"] is True


def test_a_failing_test_that_mentions_the_installer_is_refuted():
    """Finding 3: a mere mention of the installer is not a missing browser."""
    evidence = _evidence(
        "  ✘  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 shows it (5ms)\n"
        "    Error: expect(received).toContain(expected)\n"
        "    Received: 'run npx playwright install to set up the browsers'\n",
        excused=[],
    )
    browser_smoke.read_back(evidence)
    assert "runtime_missing" not in evidence["app_run"]["e2e"]
    assert browser_smoke.check_e2e(evidence)["outcome"] == "refuted"


def test_a_manifest_qa_delivers_is_not_guarded_as_an_acceptance_test(sqlite_session):
    """Finding 4: package.json beside QA's tests stays the implementation's."""
    from models.agent_run import AgentRun, Verification
    from services import acceptance

    item = _test_task(sqlite_session, WEB)
    sprint_id, company_id = (
        item.sprint_id,
        sqlite_session.get(Sprint, item.sprint_id).company_id,
    )
    files = [
        "tests/test_req_1.py",
        "e2e/req_1_1.spec.ts",
        "package.json",
        "playwright.config.ts",
    ]
    run = AgentRun(
        company_id=company_id,
        external_ref=f"task-{item.id}",
        producer="le-fabrique-task-worker",
        claims={"files": files},
        artifact_digest="d",
        artifact_size=1,
        status="verified",
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    sqlite_session.add(
        Verification(
            company_id=company_id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[],
            evidence={
                "workspace_writes": [
                    {"path": p, "status": "written", "sha256": "h"} for p in files
                ]
            },
        )
    )
    sqlite_session.commit()
    guarded, _ = acceptance.test_files_of(sqlite_session, sprint_id, ["REQ-1.1"])
    assert guarded == ["tests/test_req_1.py", "e2e/req_1_1.spec.ts"]
    assert set(acceptance.test_hashes_of(sqlite_session, sprint_id)) == set(guarded)
    # An implementation task that changes the dependencies is not refuted.
    check = criteria_tests.check_untouched(
        ["package.json"],
        {
            "acceptance": {"kind": None, "test_files": guarded},
            "workspace_writes": [{"path": "package.json", "status": "written"}],
        },
    )
    assert check["outcome"] == "pass"


def test_reading_back_is_all_or_nothing_and_idempotent(monkeypatch):
    """Kilo: a failure partway leaves the record as it was; twice is once."""
    evidence = _evidence(
        "  ✓  1 e2e/req_1_1.spec.ts:3:5 › REQ-1.1 ok (5ms)\n"
        "  ✘  2 e2e/req_1_2.spec.ts:3:5 › REQ-1.2 no (5ms)\n",
        excused=["REQ-1.2"],
    )
    before = dict(evidence["app_run"]["e2e"])

    def boom(*_args, **_kwargs):
        raise RuntimeError("partway")

    monkeypatch.setattr(browser_smoke, "failures_complete", boom)
    with pytest.raises(RuntimeError):
        browser_smoke.read_back(evidence)
    assert evidence["app_run"]["e2e"] == before
    monkeypatch.undo()
    browser_smoke.read_back(evidence)
    once = dict(evidence["app_run"]["e2e"])
    browser_smoke.read_back(evidence)
    assert evidence["app_run"]["e2e"] == once
    assert once["criteria"] == {"REQ-1.1": "pass", "REQ-1.2": "fail"}
