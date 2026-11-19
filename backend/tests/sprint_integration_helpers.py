"""What the tests of a sprint's integration share (issue #314).

The daemon's handlers behind a fake NATS client, the project's test command
as a fixture, and the rows of a sprint with two done tasks whose branches were
delivered with real git. The tests are ``test_sprint_integration_*.py``.
"""

import shlex
import sys
from pathlib import Path

import pytest
from config import settings
from models import AgentRun, BacklogItem, ProjectDiscovery, Sprint, Verification

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import git_workspace
import sprint_integration
import test_git_publish
import test_git_workflow
from test_git_workflow import _company, daemon

# The fixtures of the Git workflow's tests: a repository, and one with a remote.
repo = test_git_workflow.repo
published_repo = test_git_publish.published_repo


def _deliver(root, task_id, files, merge=()):
    reply = daemon.deliver_files(
        str(root),
        [{"path": p, "content": c} for p, c in files.items()],
        {
            "branch": f"fabrique/task-{task_id}",
            "base": "",
            "message": f"Task {task_id}",
            "merge": list(merge),
        },
    )
    assert reply["ok"], reply
    return reply["git"]["commit"]


class _Daemon:
    """The real daemon's handlers behind ``request``."""

    def __init__(self):
        self.requests = []
        self.offline = False

    async def close(self):
        return None

    async def drain(self):
        return None

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        if self.offline:
            raise TimeoutError("nobody answered")
        try:
            if subject == "workspace.git.integrate":
                return sprint_integration.integrate(payload["workspace_root"], payload)
            if subject == "workspace.git.publish":
                return git_workspace.publish(payload["workspace_root"], payload)
        except git_workspace.GitError as e:
            return {"error": {"status": 409, "message": str(e)}}
        if subject == "workspace.execute":
            return daemon.execute_shell_command(
                payload["command"], payload["workspace_root"]
            )
        raise AssertionError(subject)


@pytest.fixture
def suite(monkeypatch, tmp_path):
    """The project's test command: both tasks' modules import together."""
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    monkeypatch.setattr(settings, "DELIVERY_TESTS_ENABLED", True)
    command = f'{shlex.quote(sys.executable)} -c "import a, b"'
    monkeypatch.setattr(settings, "DELIVERY_TEST_COMMAND", command)
    return command


def _sprint(session, root, *, on=True, forge="publish"):
    # A company that publishes, as every company did before issue #727.
    company = _company(session, on=on, forge=forge)
    session.add(ProjectDiscovery(company_id=company.id, project_path=str(root)))
    sprint = Sprint(company_id=company.id, sprint_name="Auth", status="completed")
    session.add(sprint)
    session.commit()
    return company, sprint


def _task(session, sprint, title, status="done", depends_on=None):
    item = BacklogItem(
        sprint_id=sprint.id, title=title, status=status, depends_on=depends_on or []
    )
    session.add(item)
    session.commit()
    return item


def _run(
    session,
    company,
    item,
    summary,
    files=(),
    verdict="verified",
    artifact="x",
    version=1,
):
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{item.id}",
        version=version,
        producer="le-fabrique-task-worker",
        agent_role="Developer",
        claims={"summary": summary},
        artifact_text=artifact,
        artifact_digest="d",
        artifact_size=len(artifact),
    )
    session.add(run)
    session.commit()
    session.add(
        Verification(
            company_id=company.id,
            agent_run_id=run.id,
            verdict=verdict,
            checks=[{"name": "files_written", "outcome": "pass", "observation": "ok"}],
            # None: the daemon never reported (no files read back).
            evidence={
                "workspace_writes": None
                if files is None
                else [
                    {
                        "path": path,
                        "status": "written",
                        "change": "added",
                        "additions": 1,
                        "deletions": 0,
                    }
                    for path in files
                ]
            },
        )
    )
    session.commit()
    return run


def _two_done_tasks(session, root, *, conflict=False):
    company, sprint = _sprint(session, root)
    first = _task(session, sprint, "Constants")
    second = _task(session, sprint, "Use them", depends_on=[first.id])
    left = _task(session, sprint, "Docs", status="blocked")
    if conflict:
        _deliver(root, first.id, {"app.py": "x = 1\n"})
        _deliver(root, second.id, {"app.py": "x = 2\n"})
    else:
        _deliver(root, first.id, {"a.py": "A = 1\n"})
        _deliver(
            root,
            second.id,
            {"b.py": "from a import A\n"},
            [f"fabrique/task-{first.id}"],
        )
    _run(session, company, first, "Adds A | the constant", ["a.py"])
    _run(session, company, second, "Uses A", ["b.py"])
    return company, sprint, first, second, left


def observed_part(text):
    return text.split("## Claimed by the producers (not observed)")[0]


def _published_body(test_client):
    nats = test_client.app.state.nats_client
    [(_, spec)] = [r for r in nats.requests if r[0] == "workspace.git.publish"][-1:]
    return spec["body"]
