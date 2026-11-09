"""
Console producers: a terminal coding agent produces a role's task, and the
delivery is what the daemon observes (``services/console_producer.py``,
``services/console_clis.py``, ``vscode-daemon/console_run.py``).

Real git in ``tmp_path`` and the real daemon: a fake CLI on ``PATH`` (as
``test_console_assistants`` and the e2e's ``e2e-copilot`` do) edits a file in
the task's worktree; the daemon reads it back and puts the worktree back; the
task worker delivers it through ``workspace.generate`` on the task's branch,
and the Verifier verifies it. A CLI that only claims success, fails, times
out or is missing delivers nothing and is never a pass.

Run with:
    cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_console_producers.py -q
"""

import asyncio
import hashlib
import importlib.util
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path

import auth_tokens
import pytest
from config import settings
from models import AssistantRuntimeState, Company, CompanyMember, User
from models.agent_run import AgentRun, Verification
from models.company import ProjectDiscovery
from models.sprint import BacklogItem, Sprint
from services import (
    console_assistants,
    console_clis,
    console_producer,
    task_worker,
)
from sqlmodel import Session, select

_DAEMON_DIR = Path(__file__).resolve().parents[2] / "vscode-daemon"


def _load_daemon():
    sys.path.insert(0, str(_DAEMON_DIR))
    spec = importlib.util.spec_from_file_location(
        "fabrique_daemon_console", _DAEMON_DIR / "daemon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon()
import command_policy  # The daemon's folder is on the path now.
import console_run


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


# ---------------------------------------------------------------------------
# The daemon: prepare, run, collect
# ---------------------------------------------------------------------------


def _run(worktree, command, policy=None, timeout=None):
    return daemon.execute_shell_command(
        command, worktree, {"mode": "host"}, policy, timeout
    )


def test_the_daemon_reads_back_what_the_cli_changed_and_puts_the_worktree_back(
    repo, cli_path
):
    fake_cli(
        cli_path, "claude", EDIT_CALC + "mkdir -p pkg && echo 'X = 1' > pkg/new.py\n"
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    assert prepared["ok"], prepared
    worktree = prepared["worktree"]
    assert worktree.endswith("fabrique__task-1")
    command = console_clis.render_command(
        "claude", "Fix add. It's 'quoted' $(rm -rf /)\nok"
    )
    result = _run(worktree, command, console_clis.command_policy("claude"))
    assert result["success"], result
    assert result["sandbox"]["mode"] == "host"

    collected = console_run.collect(
        worktree, {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert collected["ok"] and not collected["refused"]
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path["calc.py"]["change"] == "modified"
    assert by_path["calc.py"]["content"] == "def add(a, b):\n    return a + b\n"
    assert by_path["pkg/new.py"]["change"] == "added"
    assert (
        by_path["calc.py"]["sha256"]
        == hashlib.sha256(b"def add(a, b):\n    return a + b\n").hexdigest()
    )
    # The worktree is back at its HEAD, the person's checkout never touched.
    assert git(worktree, "status", "--porcelain") == ""
    assert (Path(worktree) / "calc.py").read_text().endswith("return 0\n")
    assert (repo / "calc.py").read_text().endswith("return 0\n")
    assert git(repo, "status", "--porcelain") == ""


def test_the_command_policy_runs_only_the_cli_and_the_prompt_cannot_escape(
    tmp_path,
):
    policy = console_clis.command_policy("claude")
    tricky = 'a\'b "c" `id` $(id) ; rm -rf / && echo\nnext line | cat > /etc/x'
    assert (
        command_policy.refusal(console_clis.render_command("claude", tricky), policy)
        is None
    )
    assert command_policy.refusal("rm -rf /", policy)
    assert command_policy.refusal("claude --help; rm -rf /", policy)
    assert command_policy.refusal(console_clis.render_command("codex", "x"), policy)
    # Every CLI's own command passes its own policy.
    for cli in console_clis.CLIS:
        command = console_clis.render_command(cli, tricky)
        assert command_policy.refusal(command, console_clis.command_policy(cli)) is None


def test_the_cli_committing_itself_is_collected_and_undone(repo, cli_path):
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + "git -c user.name=x -c user.email=x@x commit -qam 'mine'\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["committed_by_cli"] is True
    assert [f["path"] for f in collected["files"]] == ["calc.py"]
    assert git(prepared["worktree"], "rev-parse", "HEAD") == prepared["head"]


def test_hooks_secrets_symlinks_and_binaries_are_never_delivered(
    repo, cli_path, tmp_path
):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    fake_cli(
        cli_path,
        "claude",
        "echo 'TOKEN=x' > .env\n"
        f"ln -s {outside} link.txt\n"
        "printf '\\377\\376' > blob.py\n" + EDIT_CALC,
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path[".env"]["status"] == "refused" and "content" not in by_path[".env"]
    assert by_path["link.txt"]["status"] == "refused"
    assert by_path["blob.py"]["status"] == "binary"
    assert by_path["calc.py"]["status"] == "changed"
    _, deliver = console_clis.observed_files(collected)
    assert [f["path"] for f in deliver] == ["calc.py"]

    # A hook planted in the repository: nothing is delivered, and it is said.
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC
        + "echo 'echo pwned' > \"$(git rev-parse --git-common-dir)/hooks/pre-commit\"\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["ok"] is False
    assert "metadata" in collected["refused"][0]
    assert all("content" not in f for f in collected["files"])
    # The person's checkout changed while it ran: refused too.
    fake_cli(cli_path, "claude", EDIT_CALC + f"echo x > {repo}/stray.py\n")
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert "checkout changed" in collected["refused"][-1]


def test_collect_reads_only_a_task_worktree(repo):
    head = git(repo, "rev-parse", "HEAD")
    assert console_run.collect(str(repo), {"head": head}, _resolve)["ok"] is False
    assert console_run.collect(str(repo), {"head": "main"}, _resolve)["ok"] is False
    assert console_run.prepare(str(repo), {"git": {"branch": "main"}})["ok"] is False


# ---------------------------------------------------------------------------
# Through the task worker
# ---------------------------------------------------------------------------


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
    for module in (task_worker, console_assistants):
        monkeypatch.setattr(module, "engine", engine)
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


def test_a_console_producer_delivers_what_the_daemon_observed_and_it_is_verified(
    world, cli_path, repo
):
    fake_cli(cli_path, "claude", EDIT_CALC)
    nats = _DaemonOverNATS()
    item, agent, sprint = world["item"], world["agent"], world["sprint"]

    deliverable = asyncio.run(console_producer.produce(nats, item, agent, sprint))
    assert deliverable is not None
    # The CLI's words are a claim in the text; the files are the daemon's.
    assert "a claim, not evidence" in deliverable
    assert "> I fixed add in calc.py" in deliverable
    assert task_worker.parse_file_manifest(deliverable) == []
    assert [e["path"] for e in agent["tool_edits"]] == ["calc.py"]
    assert agent["served_model"] is None
    ok, _ = task_worker._validate_minimum_deliverable(
        item, deliverable, agent["tool_edits"]
    )
    assert ok
    subjects = [s for s, _ in nats.requests]
    assert subjects == [
        "workspace.console.prepare",
        "workspace.execute",
        "workspace.console.collect",
    ]
    execute = nats.requests[1][1]
    assert execute["command_policy"] == {
        "mode": "allow",
        "allow": ["claude -p --permission-mode bypassPermissions"],
    }
    assert execute["sandbox"]["mode"] == "container"
    assert execute["sandbox"]["network"] is True
    assert execute["workspace_root"].endswith(f"fabrique__task-{item['id']}")

    asyncio.run(task_worker._deliver_artifacts(nats, item, agent, sprint, deliverable))
    generate = nats.requests[-1][1]
    assert generate["files"][0]["source"] == "console_producer"
    [(run, verification)] = _runs(world["engine"], item["id"])
    assert run.producer == "le-fabrique-task-worker" and run.model is None
    assert verification.verdict == "verified", [
        (c["name"], c["outcome"], c.get("observation")) for c in verification.checks
    ]
    assert _check(verification, "files_written")["outcome"] == "pass"
    console = _check(verification, "console_run")
    assert console["outcome"] == "pass" and console["via"] == "console:claude"
    evidence = verification.evidence["console_run"]
    assert evidence["exit_code"] == 0 and evidence["duration_seconds"] is not None
    assert evidence["files"][0]["delivered"] is True
    assert "stdout" not in evidence and "I fixed" not in str(evidence)
    # Committed on the task's branch, as any delivery; the checkout untouched.
    branch = f"fabrique/task-{item['id']}"
    assert git(repo, "rev-parse", branch) == verification.evidence["git"]["commit"]
    assert "return a + b" in git(repo, "show", f"{branch}:calc.py")
    assert (repo / "calc.py").read_text().endswith("return 0\n")


def _board(engine, item_id):
    with Session(engine) as session:
        return session.get(BacklogItem, item_id).comments or []


@pytest.mark.parametrize(
    ("mode", "network", "said"),
    [
        ("host", True, "need a container or bwrap sandbox"),
        ("bwrap", False, "network on"),
    ],
)
def test_the_host_sandbox_or_no_network_leaves_the_task_to_the_model_and_says_why(
    world, cli_path, sqlite_session, monkeypatch, mode, network, said
):
    # A CLI that would edit the file: it must never run.
    fake_cli(cli_path, "claude", EDIT_CALC)
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", network)
    company = sqlite_session.get(Company, world["company"].id)
    company.delivery_sandbox = mode
    sqlite_session.add(company)
    sqlite_session.commit()
    nats = _DaemonOverNATS()
    item, agent, sprint = world["item"], world["agent"], world["sprint"]

    assert asyncio.run(console_producer.produce(nats, item, agent, sprint)) is None
    assert nats.requests == []  # Nothing prepared, nothing run.
    assert said in agent["console_skipped"]["reason"]
    notes = [c["text"] for c in _board(world["engine"], item["id"])]
    assert any(
        "Console producer Claude Code not used" in n and said in n for n in notes
    )
    assert nats.published[-1]["producer_decision"] == "skipped"

    # The model's delivery that follows carries why on its evidence.
    answer = "Fixed.\n\n```python path=calc.py\ndef add(a, b):\n    return a + b\n```\n"
    asyncio.run(task_worker._deliver_artifacts(nats, item, agent, sprint, answer))
    [(_, verification)] = _runs(world["engine"], item["id"])
    skipped = verification.evidence["console_producer_skipped"]
    assert skipped["via"] == "console:claude" and said in skipped["reason"]
    assert "console_run" not in verification.evidence


def test_codex_keeps_its_own_sandbox_and_no_cli_runs_outside_ours():
    assert console_clis.CLIS["codex"]["argv"][:3] == ["codex", "exec", "--full-auto"]
    flat = " ".join(" ".join(c["argv"]) for c in console_clis.CLIS.values())
    assert "dangerously-bypass-approvals-and-sandbox" not in flat
    assert console_clis.sandbox_refusal({"mode": "host", "network": True})
    assert console_clis.sandbox_refusal(None)
    assert console_clis.sandbox_refusal({"mode": "container", "network": False})
    assert console_clis.sandbox_refusal({"mode": "container", "network": True}) is None
    assert console_clis.sandbox_refusal({"mode": "bwrap", "network": True}) is None


def test_a_cli_that_only_claims_success_is_never_a_pass(world, cli_path):
    fake_cli(
        cli_path, "claude", "echo 'Done: calc.py is fixed and every test passes.'\n"
    )
    nats = _DaemonOverNATS()
    agent = world["agent"]
    with pytest.raises(console_producer.ConsoleProducerError) as failed:
        asyncio.run(
            console_producer.produce(nats, world["item"], agent, world["sprint"])
        )
    assert "changed no file" in str(failed.value)
    [(run, verification)] = _runs(world["engine"], world["item"]["id"])
    assert verification.verdict == "refuted"
    assert _check(verification, "console_run")["outcome"] == "refuted"
    assert run.claims["files"] == []


def test_the_clis_word_that_tests_pass_is_held_to_a_test_run(world, cli_path):
    fake_cli(cli_path, "claude", EDIT_CALC + "echo 'All tests pass.'\n")
    nats = _DaemonOverNATS()
    item, agent, sprint = world["item"], world["agent"], world["sprint"]
    deliverable = asyncio.run(console_producer.produce(nats, item, agent, sprint))
    asyncio.run(task_worker._deliver_artifacts(nats, item, agent, sprint, deliverable))
    [(_, verification)] = _runs(world["engine"], item["id"])
    # No test run was observed: the claim is not taken.
    assert _check(verification, "claimed_tests_evidenced")["outcome"] == "unverifiable"
    assert verification.verdict != "verified"


def test_a_cli_that_times_out_is_refuted_and_its_half_work_discarded(
    world, cli_path, monkeypatch, repo
):
    monkeypatch.setattr(settings, "CONSOLE_PRODUCER_TIMEOUT_SECONDS", 1)
    fake_cli(cli_path, "claude", EDIT_CALC + "sleep 5\n")
    nats = _DaemonOverNATS()
    with pytest.raises(console_producer.ConsoleProducerError, match="timeout"):
        asyncio.run(
            console_producer.produce(
                nats, world["item"], world["agent"], world["sprint"]
            )
        )
    [(_, verification)] = _runs(world["engine"], world["item"]["id"])
    assert verification.verdict == "refuted"
    assert verification.evidence["console_run"]["timed_out"] is True
    worktree = Path(verification.evidence["console_run"]["worktree"])
    assert (worktree / "calc.py").read_text().endswith("return 0\n")


def test_a_missing_binary_is_unverifiable_and_the_model_takes_over_while_it_cools_down(
    world, cli_path, sqlite_session
):
    company = sqlite_session.get(Company, world["company"].id)
    company.console_producers = {"Developer": "gemini"}
    sqlite_session.add(company)
    sqlite_session.commit()
    nats = _DaemonOverNATS()
    with pytest.raises(console_producer.ConsoleProducerError, match="not installed"):
        asyncio.run(
            console_producer.produce(
                nats, world["item"], world["agent"], world["sprint"]
            )
        )
    [(_, verification)] = _runs(world["engine"], world["item"]["id"])
    assert verification.verdict == "unverifiable"
    assert verification.evidence["console_run"]["cooldown"] == "missing"
    assert console_assistants.cooldown_until(company.id, "gemini") is not None
    # Cooling down: the model produces the next task; nothing is asked of the daemon.
    again = _DaemonOverNATS()
    assert (
        asyncio.run(
            console_producer.produce(
                again, world["item"], dict(world["agent"]), world["sprint"]
            )
        )
        is None
    )
    assert again.requests == []
    assert again.published[-1]["producer_decision"] == "skipped"


def test_a_quota_stop_starts_the_cooldown(world, cli_path):
    fake_cli(cli_path, "claude", "echo 'Credit balance is too low' >&2\nexit 1\n")
    with pytest.raises(console_producer.ConsoleProducerError, match="exit 1"):
        asyncio.run(
            console_producer.produce(
                _DaemonOverNATS(), world["item"], world["agent"], world["sprint"]
            )
        )
    [(_, verification)] = _runs(world["engine"], world["item"]["id"])
    assert verification.evidence["console_run"]["cooldown"] == "quota"
    assert console_assistants.cooldown_until(world["company"].id, "claude")


def test_without_the_git_workflow_or_a_cli_the_model_produces(
    world, sqlite_session, cli_path
):
    nats = _DaemonOverNATS()
    other = dict(world["agent"], role="QA")
    assert (
        asyncio.run(
            console_producer.produce(nats, world["item"], other, world["sprint"])
        )
        is None
    )
    company = sqlite_session.get(Company, world["company"].id)
    company.git_workflow = False
    sqlite_session.add(company)
    sqlite_session.commit()
    assert (
        asyncio.run(
            console_producer.produce(
                nats, world["item"], world["agent"], world["sprint"]
            )
        )
        is None
    )
    assert nats.requests == []
    assert "Git workflow is off" in nats.published[-1]["message"]


def test_the_prompt_is_the_task_and_says_the_report_is_a_claim(world):
    prompt = console_producer.build_prompt(
        dict(world["item"], comments=[]),
        world["agent"],
        world["sprint"],
        "\n\n## Requirements\nREQ-1",
        None,
        "\n\n## Procedures\nKeep it small.",
    )
    assert "## Task: Fix add" in prompt and "Make add add." in prompt
    assert "REQ-1" in prompt and "Keep it small." in prompt
    assert "a claim" in prompt and "Do not commit" in prompt
    assert len(console_clis.render_command("claude", "x" * 300_000)) < 110_000


def test_the_verifier_check_reads_the_run():
    assert console_clis.check_console_run({}) is None
    not_run = console_clis.check_console_run(
        {
            "console_run": {
                "cli": "codex",
                "kind": "not_run",
                "outcome": "failed",
                "reason": "no",
            }
        }
    )
    assert not_run["outcome"] == "unverifiable"


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


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


def test_a_manager_names_a_console_producer_and_a_viewer_reads_it(
    sqlite_session, test_client, enforcement, monkeypatch
):
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", False)
    AssistantRuntimeState.__table__.create(sqlite_session.get_bind(), checkfirst=True)
    company = Company(
        company_name=f"Set {secrets.token_hex(3)}",
        git_workflow=False,
        delivery_sandbox="host",
    )
    sqlite_session.add(company)
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, None, "Owner")
    url = f"/v1/companies/{company.id}/console_producers"

    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200
    body = read.json()
    assert body["producers"] == {} and body["git_workflow"] is False
    assert [c["id"] for c in body["clis"]] == ["claude", "codex", "gemini", "cursor"]
    assert "Developer" in body["roles"]
    assert test_client.get(url, headers=outsider).status_code == 403
    assert test_client.get(url).status_code == 401
    choice = {"producers": {"Developer": "claude"}}
    assert test_client.put(url, json=choice, headers=viewer).status_code == 403
    assert test_client.put(url, json=choice, headers=outsider).status_code == 403
    # Without the Git workflow: refused, the same way edits never touch the checkout.
    refused = test_client.put(url, json=choice, headers=manager)
    assert refused.status_code == 422 and "Git workflow" in refused.json()["detail"]

    sqlite_session.refresh(company)
    company.git_workflow = True
    sqlite_session.add(company)
    sqlite_session.commit()
    saved = test_client.put(url, json=choice, headers=manager)
    assert saved.status_code == 200 and saved.json()["producers"] == {
        "Developer": "claude"
    }
    # Accepted on the host sandbox, with the warning that no CLI runs there.
    assert saved.json()["sandbox"]["mode"] == "host"
    assert "container or bwrap" in saved.json()["warnings"][0]
    sqlite_session.refresh(company)
    company.delivery_sandbox = "container"
    sqlite_session.add(company)
    sqlite_session.commit()
    assert "network on" in test_client.get(url, headers=viewer).json()["warnings"][0]
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", True)
    assert test_client.get(url, headers=viewer).json()["warnings"] == []
    assert test_client.get(url, headers=viewer).json()["producers"] == {
        "Developer": "claude"
    }
    for bad in ({"Developer": "copilot"}, {"PO": "claude"}, {"Developer": "rm -rf"}):
        assert (
            test_client.put(url, json={"producers": bad}, headers=manager).status_code
            == 422
        )
    cleared = test_client.put(
        url, json={"producers": {"Developer": ""}}, headers=manager
    )
    assert cleared.json()["producers"] == {}
    # Another company's Manager cannot touch it.
    other = Company(company_name=f"Other {secrets.token_hex(3)}", git_workflow=True)
    sqlite_session.add(other)
    sqlite_session.commit()
    stranger = _member(sqlite_session, other.id, "Manager")
    assert test_client.put(url, json=choice, headers=stranger).status_code == 403


def test_the_daemon_image_copies_the_module():
    dockerfile = (_DAEMON_DIR / "Dockerfile").read_text()
    assert "console_run.py" in dockerfile
    assert os.path.exists(_DAEMON_DIR / "console_run.py")
