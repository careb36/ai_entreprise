"""The loop's side of the changelog check: observe, prompt, QA's view, plan order.

- ``loop/changelog_observe``: a delivery that wrote a fragment gets the
  daemon's diff of its commit against the merge base and the bytes of the
  fragment and of the base's files, never the agent's list;
- ``loop/worker/changelog_prompt``: a changelog task is told to read the diff;
- ``loop/worker/review_evidence``: QA sees the check first and whole;
- ``planning/changelog_order``: the changelog task depends on every other task.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
import worker_stages
from models.company import ProjectDiscovery
from services.evidence import changelog_diff
from services.loop import changelog_observe
from services.loop.worker import changelog_prompt, prompt_budget, review_evidence
from services.planning import changelog_order, sprint_planner
from services.workspace import daemon_liveness

FRAGMENT = "changelog.d/701-container-sandbox-mode.md"
TEXT = (
    "Added\n\n* **Container mode (issue #701).** `scripts/self_host/self_host.py` "
    "gains `--container-mode` (`add_arguments`).\n"
)
BASE = 'def add_arguments(parser):\n    parser.add_argument("--container-mode")\n'
COMMIT, MERGE_BASE = "c" * 40, "m" * 40


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class _Daemon:
    """Answers ``workspace.git.diff`` and ``workspace.git.read_at`` as the daemon does."""

    def __init__(self, changed):
        self.changed, self.asked = changed, []

    async def request(self, subject, payload, timeout=5.0):
        self.asked.append((subject, payload))
        if subject == "workspace.git.diff":
            return {
                "ok": True,
                "base": "main",
                "merge_base": MERGE_BASE,
                "commit": payload["commit"],
                "files": [],
                "paths": self.changed,
                "paths_truncated": False,
            }
        blobs = {
            COMMIT: {FRAGMENT: TEXT},
            MERGE_BASE: {"scripts/self_host/self_host.py": BASE},
        }
        held = blobs[payload["commit"]]
        return {
            "ok": True,
            "commit": payload["commit"],
            "files": [
                {"path": p, "status": "present", "content_b64": _b64(held[p])}
                if p in held
                else {"path": p, "status": "missing"}
                for p in payload["paths"]
            ],
        }


def _evidence():
    return {
        "workspace_writes": [{"path": FRAGMENT, "status": "written", "sha256": "x"}],
        "git": {"repository": "/repo", "base": "main", "commit": COMMIT},
    }


def _observe(monkeypatch, daemon, evidence):
    monkeypatch.setattr(daemon_liveness, "incompatibility", lambda: None)
    monkeypatch.setattr(
        changelog_observe,
        "_context",
        lambda *a: (changelog_diff.sprint_issue("Issue #701"), "none"),
    )
    asyncio.run(
        changelog_observe.observe(daemon, None, {"id": 8, "company_id": 1}, evidence)
    )
    return evidence


def test_a_fragment_is_judged_against_the_daemons_diff(monkeypatch):
    daemon = _Daemon([FRAGMENT, "scripts/self_host/README.md"])
    evidence = _observe(monkeypatch, daemon, _evidence())
    record = evidence[changelog_diff.EVIDENCE_KEY]
    assert record["changed"] == [FRAGMENT, "scripts/self_host/README.md"]
    assert record["merge_base"] == MERGE_BASE
    assert record["fragments"][0]["text"] == TEXT
    assert len(record["fragments"][0]["sha256"]) == 64
    base = {f["path"]: f for f in record["base_files"]}
    assert set(base["scripts/self_host/self_host.py"]["found"]) == {
        "--container-mode",
        "add_arguments",
    }
    # The base was read at the merge base, the fragment at the delivery's commit.
    commits = [p.get("commit") for s, p in daemon.asked if s.endswith("read_at")]
    assert commits == [COMMIT, MERGE_BASE]
    result = changelog_diff.check(evidence)
    assert result["outcome"] == "refuted", result
    assert "self_host.py" in result["observation"]


def test_no_fragment_no_request(monkeypatch):
    daemon = _Daemon([])
    evidence = {"workspace_writes": [{"path": "src/a.py", "status": "written"}]}
    _observe(monkeypatch, daemon, evidence)
    assert daemon.asked == [] and changelog_diff.EVIDENCE_KEY not in evidence


def test_without_a_git_commit_the_record_says_why(monkeypatch):
    evidence = {**_evidence(), "git": None}
    _observe(monkeypatch, _Daemon([]), evidence)
    record = evidence[changelog_diff.EVIDENCE_KEY]
    assert "no Git commit" in record["error"]
    assert changelog_diff.check(evidence)["outcome"] == "unverifiable"


def test_a_silent_daemon_leaves_it_unverifiable(monkeypatch):
    class Silent:
        async def request(self, *a, **k):
            raise TimeoutError("no reply")

    evidence = _observe(monkeypatch, Silent(), _evidence())
    assert "did not answer" in evidence[changelog_diff.EVIDENCE_KEY]["error"]
    assert changelog_diff.check(evidence)["outcome"] == "unverifiable"


def test_a_changelog_task_is_told_to_read_the_diff_first():
    item = {"title": "Write the changelog fragment", "description": "changelog.d/"}
    section = changelog_prompt.section(item)
    assert section.name in prompt_budget.SECTION_NAMES
    assert section.name not in prompt_budget.REQUIRED_NAMES
    assert "workspace__git `changed`" in section.text
    assert "never what the plan" in section.text
    assert (
        changelog_prompt.section({"title": "Add login", "description": ""}).text == ""
    )


def test_the_developer_prompt_carries_it_and_it_can_be_turned_off(monkeypatch):
    from services.loop.worker import execute as worker_execute

    for name in ("repository_section",):
        monkeypatch.setattr(worker_execute.developer_tools, name, lambda *a, **k: "")
    monkeypatch.setattr(
        worker_execute.related, "related_code_section", lambda *a, **k: ""
    )
    monkeypatch.setattr(
        worker_execute.sprint_notes, "board_section", lambda *a, **k: ""
    )
    item = {"id": 1, "title": "Changelog fragment for issue #701", "description": ""}
    args = (item, {"role": "Developer"}, "S", None, None, None, 1, None, None, None)
    text, _, _ = worker_execute._developer_prompt(*args, wiki_context="")
    assert "## Changelog fragment" in text
    off = frozenset({changelog_prompt.SECTION_NAME})
    text, _, measure = worker_execute._developer_prompt(*args, wiki_context="", off=off)
    assert "## Changelog fragment" not in text
    assert measure["off_dropped"] == [changelog_prompt.SECTION_NAME]


def test_qa_sees_the_changelog_check_first_and_whole():
    long = "x" * 1500
    checks = [{"name": f"c{i}", "outcome": "pass"} for i in range(25)]
    checks.append(
        {
            "name": changelog_diff.CHECK_NAME,
            "outcome": "unverifiable",
            "observation": long,
        }
    )
    text = review_evidence.render("unverifiable", checks, {})
    lines = text.splitlines()
    assert lines[2].startswith(f"- {changelog_diff.CHECK_NAME}: unverifiable")
    assert long in text


def test_the_changelog_task_depends_on_every_other_task():
    tasks = [
        {"title": "Write the changelog fragment", "description": ""},
        {"title": "Add the flag", "description": ""},
        {"title": "Test the flag", "description": ""},
        {"title": "Document the flag", "description": ""},
    ]
    originals = [t["title"] for t in tasks]
    # The PO made the flag wait for the changelog and the changelog for nothing.
    named = [[], ["Write the changelog fragment"], ["Add the flag"], []]
    planned = sprint_planner.with_dependencies(tasks, originals, named)
    assert planned[0]["depends_on"] == [1, 2, 3]
    assert planned[1]["depends_on"] == []
    assert planned[2]["depends_on"] == [1]


def test_a_plan_without_a_changelog_task_is_unchanged():
    tasks = [{"title": "A", "depends_on": []}, {"title": "B", "depends_on": [0]}]
    assert changelog_order.last([dict(t) for t in tasks]) == tasks
    assert not changelog_order.is_changelog_task({"title": "URL fragment parser"})


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    bind = sqlite_session.get_bind()
    ProjectDiscovery.__table__.create(bind, checkfirst=True)
    worker_stages.use_engine(monkeypatch, bind)
    return sqlite_session


def test_the_deliver_stage_records_the_observation(db, tmp_path, monkeypatch):
    """Through ``_deliver_artifacts`` with the real daemon and no Git workflow:
    the fragment's evidence says why the diff was not observed, the check is
    unverifiable and the task is held for a person."""
    from services.loop.worker import deliver as worker_deliver
    from test_task_delivery import _DaemonOverNATS, _latest, _load_daemon, _task_fixture

    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    project = tmp_path / "project"
    project.mkdir()
    item, agent, sprint = _task_fixture(db, str(project))
    answer = f"```markdown path={FRAGMENT}\n{TEXT}```\n"
    asyncio.run(
        worker_deliver._deliver_artifacts(
            _DaemonOverNATS(_load_daemon()), item, agent, sprint, answer
        )
    )
    _, verification, _ = _latest(db, item["id"])
    record = verification.evidence[changelog_diff.EVIDENCE_KEY]
    assert "no Git commit" in record["error"]
    checks = {c["name"]: c for c in verification.checks}
    assert checks[changelog_diff.CHECK_NAME]["outcome"] == "unverifiable"
    assert checks["risk_tier"]["tier"] == "high"
