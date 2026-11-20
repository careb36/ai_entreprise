"""QA reviews the task branch, not only the latest attempt (issue #766).

In sprint 3, task 17's branch held ``container_mode.py`` and the change to
``self_host.py`` from an earlier attempt; the latest rework delivered only a
test, and QA, reading that run alone, sent it back for "no implementation
change". The review now reads the daemon's diff of the delivery's commit
against the base (``services/workspace/branch_view.py``), and the agent's
message as its claim.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from models import BacklogItem, Company, Sprint
from models.agent_run import TASK_WORKER_PRODUCER, AgentRun, Verification
from services.loop import review_digest
from services.loop.worker import review_prompt
from services.workspace import branch_view

COMMIT = "3bc53a666ac02cf5df2896fb5dad5accc9f7ee18"
GIT = {
    "repository": "/home/ana/src/app",
    "branch": "fabrique/task-17",
    "base": "main",
    "commit": COMMIT,
}


def _reply(files, **extra):
    return {
        "ok": True,
        "base": "main",
        "merge_base": "a" * 40,
        "commit": COMMIT,
        "files": files,
        **extra,
    }


def _file(path, patch, change="added", **extra):
    return {
        "path": path,
        "change": change,
        "additions": patch.count("\n+"),
        "deletions": 0,
        "patch": patch,
        **extra,
    }


TASK_17 = [
    _file(
        "scripts/self_host/container_mode.py", "@@ -0,0 +1,2 @@\n+def up():\n+    ..."
    ),
    _file(
        "scripts/self_host/self_host.py",
        "@@ -1,1 +1,2 @@\n import sys\n+import container_mode",
        change="modified",
    ),
    _file(
        "scripts/self_host/tests/test_harness_container.py",
        "@@ -0,0 +1 @@\n+def test(): ...",
    ),
]


class _Nats:
    def __init__(self, reply):
        self.reply = reply
        self.asked = []

    async def request(self, subject, payload, timeout):
        self.asked.append((subject, payload))
        return self.reply


def test_the_request_names_the_delivery_s_commit_or_nothing():
    spec = branch_view.request(GIT, "fetch")
    assert spec == {
        "workspace_root": "/home/ana/src/app",
        "base": "main",
        "commit": COMMIT,
        "forge_policy": "fetch",
    }
    assert branch_view.request(None, "fetch") is None
    assert branch_view.request({**GIT, "commit": "HEAD"}, "fetch") is None
    assert branch_view.request({**GIT, "repository": ""}, "fetch") is None


def test_every_path_is_listed_with_its_patch():
    view = branch_view.render(_reply(TASK_17), "fabrique/task-17", 24000)
    assert view.startswith("Task branch fabrique/task-17 against main")
    for f in TASK_17:
        assert f"- {f['path']} ({f['change']}" in view
        assert f"# Artifact: {f['path']}" in view
    assert "+import container_mode" in view


def test_a_cut_says_so_and_binary_files_are_named():
    long = "@@ -0,0 +1,400 @@\n" + "\n".join(f"+line {i}" for i in range(400))
    files = [
        _file("big.py", long),
        _file("logo.png", "", omitted="binary"),
        _file("small.py", "@@ -0,0 +1 @@\n+x = 1"),
    ]
    view = branch_view.render(_reply(files, truncated=True), "fabrique/task-1", 2000)
    assert "more line(s) of this patch not shown here" in view
    assert "[binary file, not shown]" in view
    assert "+x = 1" in view  # a long file does not crowd out the next one
    assert "[more files changed than the daemon lists]" in view
    assert len(view) < 2600


def test_an_unreadable_reply_gives_no_view():
    assert branch_view.render({"ok": False}, "b", 100) is None
    assert branch_view.render({"ok": True, "files": "x"}, "b", 100) is None


def _setup(session, git=GIT):
    company = Company(company_name=f"Branch {uuid4().hex[:6]}")
    session.add(company)
    session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", status="active")
    session.add(sprint)
    session.commit()
    item = BacklogItem(sprint_id=sprint.id, title="Container mode", status="in_review")
    session.add(item)
    session.commit()
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        producer=TASK_WORKER_PRODUCER,
        artifact_text="# Artifact: scripts/self_host/tests/test_harness_container.py\n...",
        artifact_digest="d",
        artifact_size=1,
    )
    session.add(run)
    session.commit()
    if git is not None:
        session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict="verified",
                evidence={"git": git},
            )
        )
        session.commit()
    return sprint, item


def test_qa_reads_the_whole_branch_and_the_message_as_a_claim(sqlite_session):
    sprint, item = _setup(sqlite_session)
    nats = _Nats(_reply(TASK_17))
    branch = asyncio.run(review_prompt.task_branch(sqlite_session, nats, item, sprint))
    assert nats.asked[0][0] == "workspace.git.diff"
    assert nats.asked[0][1]["commit"] == COMMIT

    message = (
        "Files written (1):\n"
        "- scripts/self_host/tests/test_harness_container.py (1 lines, 10 bytes)"
    )
    prompt = review_prompt.build(
        sqlite_session, item, sprint, {"name": "QA", "role": "QA"}, message, 0, branch
    )
    # The implementation an earlier attempt wrote is in front of QA.
    assert "scripts/self_host/container_mode.py (added" in prompt
    assert "+import container_mode" in prompt
    assert "THIS ATTEMPT'S MESSAGE (the agent's claim)" in prompt
    assert review_prompt.BRANCH_NOTE in prompt
    assert review_digest.NOTE not in prompt


def test_without_a_committed_delivery_qa_reads_the_run_as_before(sqlite_session):
    sprint, item = _setup(sqlite_session, git=None)
    nats = _Nats(_reply(TASK_17))
    assert (
        asyncio.run(review_prompt.task_branch(sqlite_session, nats, item, sprint))
        is None
    )
    assert nats.asked == []  # no Git workflow: the daemon is not asked


def test_a_daemon_error_or_another_commit_falls_back(sqlite_session):
    sprint, item = _setup(sqlite_session)
    failing = _Nats({"error": {"status": 409, "message": "no commit 3bc53a6"}})
    assert (
        asyncio.run(review_prompt.task_branch(sqlite_session, failing, item, sprint))
        is None
    )
    other = _Nats({**_reply(TASK_17), "commit": "b" * 40})
    assert (
        asyncio.run(review_prompt.task_branch(sqlite_session, other, item, sprint))
        is None
    )
