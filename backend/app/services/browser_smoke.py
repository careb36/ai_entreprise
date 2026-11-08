"""QA's browser smoke test per criterion, for a project that serves pages.

In a sprint planned from a specification, QA's test-writing task writes one
unit test per criterion (``services/acceptance.py``). When the project
serves pages a browser renders, it also writes one **Playwright smoke test
per criterion a person can see in the browser**: ``e2e/req_1_2.spec.ts``
for ``REQ-1.2``, each test titled with the criterion id. The condition is
deterministic (``serves_web``): the application check is on
(``DELIVERY_APP_CHECK_ENABLED``, the only thing that runs them) and the
resolved stack profile has a start command, a port and ``web`` (an HTML page
in the project or a browser framework among its dependencies,
``stack_profiles.serves_pages``, or the company's profile saying so for a
project that has neither yet). Only then does QA's prompt carry the section.

When such a suite exists (files matching ``e2e/*.spec.*`` among the test
files QA's deliveries wrote, as the daemon read them back) and no e2e
command is configured, the application check runs ``SMOKE_COMMAND`` against
the running application (``APP_URL``). Its per-test lines are read back
into criterion ids like the unit tests' (``criteria_tests``): an
observation of the daemon's output, kept on the e2e record as ``criteria``.
A browser runtime missing on the daemon's machine (no Playwright, no
browser, missing system libraries) is recorded as ``runtime_missing`` and
makes the ``e2e_run`` check unverifiable with the reason: never a pass, and
never a refutation of the delivery.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from config import settings
from logging_config import logger

from services.criteria_tests import (
    ACCEPTANCE_KIND,
    RESULT_MISSING,
    excused_among,
    failing_tests,
    results_from_output,
    token,
)
from services.orchestration_metrics import VERDICT_REFUTED, VERDICT_UNVERIFIABLE

CHECK_E2E = "e2e_run"
SOURCE_SMOKE = "smoke"
SMOKE_DIR = "e2e"
# ``--no-install``: never download Playwright at check time; a machine
# without it says so, and the check reports the runtime missing.
SMOKE_COMMAND = (
    "npx --no-install playwright test e2e --reporter=list --retries=0 --workers=1"
)
_SMOKE_FILE = re.compile(r"^e2e/[^/]+\.spec\.[cm]?[jt]s$")
_SECTION_HEADER = "## Browser checks to write"

# What a runner prints when the browser runtime, not the code, is missing.
_RUNTIME_MISSING: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"Executable doesn't exist at|playwright install", re.IGNORECASE),
        "the browser Playwright drives is not installed on the daemon's machine",
    ),
    (
        re.compile(
            r"Host system is missing dependencies to run browsers", re.IGNORECASE
        ),
        "the daemon's machine lacks the system libraries the browser needs",
    ),
    (
        re.compile(
            r"Cannot find (?:module|package) '(?:@playwright/test|playwright)"
            r"|No module named '?(?:playwright|pytest_playwright)",
            re.IGNORECASE,
        ),
        "the Playwright package is not installed for this project",
    ),
    (
        re.compile(
            r"could not determine executable to run"
            r"|\b(?:npx|playwright): (?:command )?not found",
            re.IGNORECASE,
        ),
        "Playwright (or npx) is not installed on the daemon's machine",
    ),
)


def smoke_path(criterion_id: str) -> str:
    """The smoke test file of a criterion: ``REQ-1.2`` → ``e2e/req_1_2.spec.ts``."""
    return f"{SMOKE_DIR}/{token(criterion_id)}.spec.ts"


def is_smoke_file(path: Any) -> bool:
    return isinstance(path, str) and bool(_SMOKE_FILE.match(path.replace("\\", "/")))


def serves_web(profile: dict[str, Any] | None) -> bool:
    """Whether the application check would serve this project's pages: the
    check is on and the profile starts it on a port and says it serves
    pages a browser renders."""
    profile = profile or {}
    return bool(
        settings.DELIVERY_APP_CHECK_ENABLED
        and profile.get("start")
        and profile.get("port")
        and profile.get("web")
    )


def section(covers: list[str]) -> str:
    """The prompt section asking QA for a browser smoke test per criterion."""
    if not covers:
        return ""
    lines = [
        f"\n\n{_SECTION_HEADER}",
        (
            "This project serves pages a browser renders, and the application "
            "check runs a browser smoke test per criterion against it while it "
            "runs. Besides the unit tests, for each criterion below that a person "
            "can see in the browser, write one Playwright test file holding only "
            "that criterion's tests, each test titled starting with the criterion "
            'id (e.g. "REQ-1.2 shows the greeting"). Import `test` and `expect` '
            "from `@playwright/test`, open the page with "
            "`page.goto(process.env.APP_URL + '/the/path')` (the daemon sets "
            "APP_URL to the running application) and assert what the criterion's "
            "`then` shows on the page. Keep each test a short smoke test. A "
            "criterion nothing on a page shows gets no browser test. In a Node "
            "project, add `@playwright/test` to the devDependencies. The tests "
            f"run with `{SMOKE_COMMAND}`."
        ),
        "",
    ]
    lines += [f"- {cid} → {smoke_path(cid)}" for cid in covers]
    return "\n".join(lines)


def _task_facts(engine: Any, item_id: int) -> dict[str, Any] | None:
    from models import BacklogItem, Sprint
    from sqlmodel import Session

    with Session(engine) as session:
        item = session.get(BacklogItem, item_id)
        if item is None or item.kind != ACCEPTANCE_KIND:
            return None
        sprint = session.get(Sprint, item.sprint_id)
        if sprint is None or sprint.specification_id is None:
            return None
        return {
            "sprint_id": sprint.id,
            "company_id": sprint.company_id,
            "covers": [str(c) for c in item.covers or []],
        }


async def with_browser_checks(
    nats_client: Any, engine: Any, item_id: int, resolve_root: Any, text: str
) -> str:
    """*text* (the task's requirements context), plus the browser checks
    section when *item_id* is QA's test-writing task of a project that
    ``serves_web``. Anything that cannot be read leaves *text* as it was."""
    if not settings.DELIVERY_APP_CHECK_ENABLED:
        return text
    try:
        facts = await asyncio.to_thread(_task_facts, engine, item_id)
        if not facts:
            return text
        root = await asyncio.to_thread(resolve_root, facts["company_id"])
        if not root:
            return text
        from services import app_check

        ctx = app_check.Context(
            nats_client,
            {"id": facts["sprint_id"], "company_id": facts["company_id"]},
            {"workspace_root": root},
            engine,
        )
        if serves_web(await ctx.profile()):
            return text + section(facts["covers"])
    except Exception as exc:  # noqa: BLE001 - Context only; the unit tests still get written.
        logger.debug("Browser checks not offered for item=%s: %s", item_id, exc)
    return text


# ---------------------------------------------------------------------------
# Running the suite and reading it back
# ---------------------------------------------------------------------------


def smoke_files(evidence: dict[str, Any]) -> list[str]:
    """QA's smoke test files, as the daemon read them back when QA's
    deliveries wrote them (``acceptance["test_hashes"]``)."""
    hashes = (evidence.get("acceptance") or {}).get("test_hashes") or {}
    return sorted(p for p in hashes if is_smoke_file(p))


def default_command(
    overrides: dict[str, Any] | None, profile: dict[str, Any], evidence: dict
) -> str | None:
    """``SMOKE_COMMAND`` when QA wrote a smoke suite and no e2e command is
    configured; ``None`` when one is, or the company turned e2e off."""
    if profile.get("e2e"):
        return None
    own = overrides or {}
    if "e2e" in own and own["e2e"] is not None and not str(own["e2e"]).strip():
        return None
    return SMOKE_COMMAND if smoke_files(evidence) else None


def _output(e2e: dict[str, Any]) -> str:
    """The e2e run's output as the daemon kept it: the head (where a list
    reporter prints its per-test lines) and the tail."""
    head = e2e.get("output_head") or ""
    tail = e2e.get("output_tail") or ""
    return f"{head}\n{tail}" if head else tail


def runtime_missing(text: str, exit_code: Any) -> str | None:
    """Why the run could not observe anything in a browser, from its output;
    ``None`` for a run that exited 0 or failed for another reason."""
    if exit_code == 0:
        return None
    for pattern, reason in _RUNTIME_MISSING:
        if pattern.search(text or ""):
            return reason
    if exit_code == 127:
        return "the e2e runner was not found on the daemon's machine (exit 127)"
    return None


def read_back(evidence: dict[str, Any]) -> None:
    """Annotate ``evidence["app_run"]["e2e"]`` with what its output says:
    ``runtime_missing``, or per criterion the browser tests' results
    (``criteria``, only the criteria a test named) and ``failing_tests``."""
    app = evidence.get("app_run")
    e2e = app.get("e2e") if isinstance(app, dict) else None
    if not isinstance(e2e, dict) or not e2e.get("command"):
        return
    text = _output(e2e)
    missing = (
        None if e2e.get("timed_out") else runtime_missing(text, e2e.get("exit_code"))
    )
    if missing:
        e2e["runtime_missing"] = missing
        return
    acceptance = evidence.get("acceptance")
    if not acceptance:
        return
    results = results_from_output(text, acceptance.get("all") or [])
    criteria = {c: r for c, r in results.items() if r != RESULT_MISSING}
    if criteria:
        e2e["criteria"] = criteria
    e2e["failing_tests"] = failing_tests(text)[:50]


def _tail(record: dict[str, Any], limit: int = 400) -> str:
    tail = (record.get("output_tail") or "").strip()[-limit:]
    return f": {tail}" if tail else ""


def check_e2e(evidence: dict[str, Any]) -> dict[str, Any] | None:
    """``e2e_run``: present only when an e2e command ran against the app.

    A missing browser runtime is unverifiable, with the reason; failures
    that are all browser tests of criteria not implemented yet are excused
    as the unit tests' are (``criteria_tests.excused_among``)."""
    app = evidence.get("app_run")
    e2e = app.get("e2e") if isinstance(app, dict) else None
    if not isinstance(e2e, dict) or not e2e.get("command"):
        return None
    target = f"`{e2e['command']}`"
    extra: dict[str, Any] = {}
    if e2e.get("criteria"):
        extra["results"] = e2e["criteria"]
    if e2e.get("runtime_missing"):
        return {
            "name": CHECK_E2E,
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": (
                f"{target} observed nothing in a browser: {e2e['runtime_missing']}"
                f"{_tail(e2e, 200)}"
            ),
            "runtime_missing": True,
        }
    if e2e.get("timed_out"):
        outcome = VERDICT_REFUTED
        observation = f"{target} did not finish within {e2e.get('timeout_seconds')}s"
    elif e2e.get("exit_code") == 0:
        outcome, observation = "pass", f"{target} passed against the running app"
    elif isinstance(e2e.get("exit_code"), int):
        excused_all, excused = excused_among(
            e2e.get("failing_tests") or [], evidence.get("acceptance") or {}
        )
        if excused_all:
            outcome = "pass"
            observation = (
                f"{target} exited {e2e['exit_code']}: only the browser checks of "
                f"criteria not implemented yet failed ({', '.join(excused)})"
            )
        else:
            outcome = VERDICT_REFUTED
            observation = f"{target} exited {e2e['exit_code']}{_tail(e2e)}"
    else:
        outcome = VERDICT_UNVERIFIABLE
        observation = f"{target} reported no exit status"
    return {"name": CHECK_E2E, "outcome": outcome, "observation": observation, **extra}
