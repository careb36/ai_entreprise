"""What the tests of the console producers share.

The real daemon loaded from ``vscode-daemon/``, a repository and a ``PATH``
holding only the fake CLIs, the daemon's handlers behind a fake NATS client,
a company whose Developer runs a CLI (``world``), and the readers of its runs,
checks and board. The tests are ``test_console_producers_*.py``.
"""

import importlib.util
import secrets
import stat
import subprocess
import sys
from pathlib import Path

import auth_tokens
import pytest
import worker_stages
from config import settings
from models import AssistantRuntimeState, Company, CompanyMember, User
from models.agent_run import AgentRun, Verification
from models.company import ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services.producers import cli_cooldowns
from sqlmodel import Session, select

_DAEMON_DIR = Path(__file__).resolve().parents[2] / "vscode-daemon"
sys.path.insert(0, str(_DAEMON_DIR))
import console_run  # The daemon's folder is on the path now.


def _load_daemon():
    sys.path.insert(0, str(_DAEMON_DIR))
    spec = importlib.util.spec_from_file_location(
        "fabrique_daemon_console", _DAEMON_DIR / "daemon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon()


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Ana", "-c", "user.email=ana@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# App\n", encoding="utf-8")
    (root / "calc.py").write_text("def add(a, b):\n    return 0\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def cli_path(tmp_path, monkeypatch):
    """A PATH with only the fake CLIs and the system's own tools; HOME in tmp."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv("FABRIQUE_EXEC_SHELL", "/bin/sh")
    return bindir


def fake_cli(bindir, name, script):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


SPEC = {"branch": "fabrique/task-1", "base": "", "message": "Task\n"}
EDIT_CALC = (
    "printf 'def add(a, b):\\n    return a + b\\n' > calc.py\n"
    "echo 'I fixed add in calc.py'\n"
)


def _resolve(root, relative):
    return daemon._resolve_target_path(root, relative)


def _run(worktree, command, policy=None, timeout=None):
    return daemon.execute_shell_command(
        command, worktree, {"mode": "host"}, policy, timeout
    )


class _DaemonOverNATS:
    """``request`` runs the real daemon for every subject a console run uses."""

    jetstream_enabled = False

    def __init__(self):
        self.requests = []
        self.published = []

    async def request(self, subject, payload, timeout=5.0):
        self.requests.append((subject, payload))
        root = payload.get("workspace_root")
        if subject == "workspace.console.prepare":
            return console_run.prepare(root, payload)
        if subject == "workspace.console.collect":
            return console_run.collect(root, payload, _resolve)
        if subject == "workspace.execute":
            # The company's container stands in as the host here (no docker
            # in the tests); the request still names the container.
            box = payload.get("sandbox")
            return daemon.execute_shell_command(
                payload["command"],
                root,
                {"mode": "host"} if box else None,
                payload.get("command_policy"),
                payload.get("timeout_seconds"),
            )
        if subject == "workspace.generate":
            return daemon.deliver_files(root, payload["files"], payload.get("git"))
        raise TimeoutError("not served here")

    async def publish(self, subject, payload, msg_id=None):
        self.published.append(payload)


@pytest.fixture
def world(sqlite_session, repo, monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    engine = sqlite_session.get_bind()
    AssistantRuntimeState.__table__.create(engine, checkfirst=True)
    worker_stages.use_engine(monkeypatch, engine)
    monkeypatch.setattr(cli_cooldowns, "engine", engine)
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX", "host")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", True)
    company = Company(
        company_name=f"Console {secrets.token_hex(3)}",
        git_workflow=True,
        console_producers={"Developer": "claude"},
        delivery_sandbox="container",
    )
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.add(ProjectDiscovery(company_id=company.id, project_path=str(repo)))
    sprint = Sprint(company_id=company.id, sprint_name="Calc", status="active")
    sqlite_session.add(sprint)
    sqlite_session.commit()
    item = BacklogItem(
        sprint_id=sprint.id, title="Fix add", status="in_progress", assignee="Developer"
    )
    sqlite_session.add(item)
    sqlite_session.commit()
    return {
        "engine": engine,
        "company": company,
        "item": {"id": item.id, "title": item.title, "description": "Make add add."},
        "sprint": {
            "id": sprint.id,
            "company_id": company.id,
            "name": sprint.sprint_name,
        },
        "agent": {"role": "Developer", "name": "Dev", "model": "m"},
    }


def _runs(engine, item_id):
    with Session(engine) as session:
        runs = session.exec(
            select(AgentRun).where(AgentRun.external_ref == f"task-{item_id}")
        ).all()
        return [
            (
                run,
                session.exec(
                    select(Verification).where(Verification.agent_run_id == run.id)
                ).one(),
            )
            for run in runs
        ]


def _check(verification, name):
    return next(c for c in verification.checks if c["name"] == name)


def _board(engine, item_id):
    with Session(engine) as session:
        return session.get(BacklogItem, item_id).comments or []


@pytest.fixture
def enforcement(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session, company_id, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=role,
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


FSMONITOR_PAYLOAD = (
    "printf '#!/bin/sh\\ntouch %s\\nexit 1\\n' \"$MARKER\" > fsm.sh\n"
    "chmod +x fsm.sh\n"
    "rm -f .git\n"
    "git init -q .\n"
    "git add -A && git -c user.name=x -c user.email=x@x commit -qm planted\n"
    'git config core.fsmonitor "$PWD/fsm.sh"\n'
)
