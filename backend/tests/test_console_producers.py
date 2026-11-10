"""
Console producers: a terminal coding agent produces a role's task, and the
delivery is what the daemon observes (``services/producers/console_producer.py``,
``services/producers/console_clis.py``, ``vscode-daemon/console_run.py``).

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
import shlex
import shutil
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
from services import (
    console_assistants,
    console_clis,
    console_host,
    console_producer,
    task_worker,
)
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import quality_gate
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

    # A hook planted through the git the CLI sees lands in its private
    # repository, which is discarded: the person's repository never gets it.
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
    assert collected["ok"] and not (repo / ".git" / "hooks" / "pre-commit").exists()
    assert (Path(prepared["worktree"]) / ".git").is_file()
    # A hook written into the repository's own metadata (reachable only on
    # the host sandbox): nothing is delivered, and it is said.
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + f"echo 'echo pwned' > {repo}/.git/hooks/pre-commit\n",
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
    # The person keeps working in their checkout while it runs: no sandbox
    # mounts it, so their edits never refuse the run.
    fake_cli(cli_path, "claude", EDIT_CALC + f"echo x > {repo}/stray.py\n")
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["ok"] and collected["refused"] == []
    assert "checkout" not in prepared["guard"]


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
    worker_stages.use_engine(monkeypatch, engine)
    monkeypatch.setattr(console_assistants, "engine", engine)
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

    deliverable = asyncio.run(console_host.produce(nats, item, agent, sprint))
    assert deliverable is not None
    # The CLI's words are a claim in the text; the files are the daemon's.
    assert "a claim, not evidence" in deliverable
    assert "> I fixed add in calc.py" in deliverable
    assert task_worker.parse_file_manifest(deliverable) == []
    assert [e["path"] for e in agent["tool_edits"]] == ["calc.py"]
    assert agent["served_model"] is None
    ok, _ = quality_gate._validate_minimum_deliverable(
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

    asyncio.run(
        worker_deliver._deliver_artifacts(nats, item, agent, sprint, deliverable)
    )
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

    assert asyncio.run(console_host.produce(nats, item, agent, sprint)) is None
    assert nats.requests == []  # Nothing prepared, nothing run.
    assert said in agent["console_skipped"]["reason"]
    notes = [c["text"] for c in _board(world["engine"], item["id"])]
    assert any(
        "Console producer Claude Code not used" in n and said in n for n in notes
    )
    assert nats.published[-1]["producer_decision"] == "skipped"

    # The model's delivery that follows carries why on its evidence.
    answer = "Fixed.\n\n```python path=calc.py\ndef add(a, b):\n    return a + b\n```\n"
    asyncio.run(worker_deliver._deliver_artifacts(nats, item, agent, sprint, answer))
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
        asyncio.run(console_host.produce(nats, world["item"], agent, world["sprint"]))
    assert "changed no file" in str(failed.value)
    [(run, verification)] = _runs(world["engine"], world["item"]["id"])
    assert verification.verdict == "refuted"
    assert _check(verification, "console_run")["outcome"] == "refuted"
    assert run.claims["files"] == []


def test_the_clis_word_that_tests_pass_is_held_to_a_test_run(world, cli_path):
    fake_cli(cli_path, "claude", EDIT_CALC + "echo 'All tests pass.'\n")
    nats = _DaemonOverNATS()
    item, agent, sprint = world["item"], world["agent"], world["sprint"]
    deliverable = asyncio.run(console_host.produce(nats, item, agent, sprint))
    asyncio.run(
        worker_deliver._deliver_artifacts(nats, item, agent, sprint, deliverable)
    )
    [(_, verification)] = _runs(world["engine"], item["id"])
    # No test run was observed: the claim is not taken.
    assert _check(verification, "claimed_tests_evidenced")["outcome"] == "unverifiable"
    assert verification.verdict != "verified"


@pytest.mark.parametrize("delivers", [True, False], ids=["changed", "failed"])
def test_the_base_the_branch_started_from_is_kept_on_the_console_run(
    world, cli_path, repo, tmp_path, delivers
):
    main = git(repo, "rev-parse", "main")
    git(repo, "remote", "add", "origin", (tmp_path / "nowhere.git").as_uri())
    fake_cli(cli_path, "claude", EDIT_CALC if delivers else "echo 'Done.'\n")
    nats = _DaemonOverNATS()
    item, agent, sprint = world["item"], world["agent"], world["sprint"]

    if delivers:
        deliverable = asyncio.run(console_host.produce(nats, item, agent, sprint))
        asyncio.run(
            worker_deliver._deliver_artifacts(nats, item, agent, sprint, deliverable)
        )
    else:
        with pytest.raises(console_producer.ConsoleProducerError):
            asyncio.run(console_host.produce(nats, item, agent, sprint))

    runs = _runs(world["engine"], item["id"])
    evidence = runs[0][1].evidence["console_run"]
    assert evidence["base_ref"] == "main" and evidence["base_commit"] == main
    assert evidence["started_from"] == "main"
    assert evidence["base_remote"] == "origin/main"
    assert evidence["remote_unreachable"]


def test_a_cli_that_times_out_is_refuted_and_its_half_work_discarded(
    world, cli_path, monkeypatch, repo
):
    monkeypatch.setattr(settings, "CONSOLE_PRODUCER_TIMEOUT_SECONDS", 1)
    fake_cli(cli_path, "claude", EDIT_CALC + "sleep 5\n")
    nats = _DaemonOverNATS()
    with pytest.raises(console_producer.ConsoleProducerError, match="timeout"):
        asyncio.run(
            console_host.produce(nats, world["item"], world["agent"], world["sprint"])
        )
    [(_, verification)] = _runs(world["engine"], world["item"]["id"])
    assert verification.verdict == "refuted"
    assert verification.evidence["console_run"]["timed_out"] is True
    worktree = Path(verification.evidence["console_run"]["worktree"])
    assert (worktree / "calc.py").read_text().endswith("return 0\n")


def test_a_missing_binary_hands_the_task_to_the_model_while_it_cools_down(
    world, cli_path, sqlite_session
):
    company = sqlite_session.get(Company, world["company"].id)
    company.console_producers = {"Developer": "gemini"}
    sqlite_session.add(company)
    sqlite_session.commit()
    nats = _DaemonOverNATS()
    item, agent, sprint = world["item"], world["agent"], world["sprint"]
    # Not installed: the model produces this very task, and says why.
    assert asyncio.run(console_host.produce(nats, item, agent, sprint)) is None
    skipped = agent["console_skipped"]
    assert "not installed" in skipped["reason"]
    assert skipped["attempt"]["cooldown"] == "missing"
    assert skipped["attempt"]["exit_code"] == 127
    assert "console_run" not in agent and not agent.get("tool_edits")
    notes = [c["text"] for c in _board(world["engine"], item["id"])]
    assert any("Gemini CLI not used" in n and "not installed" in n for n in notes)
    assert _runs(world["engine"], item["id"]) == []  # No refuted run of its own.
    assert console_assistants.cooldown_until(company.id, "producer:gemini")
    # The host adviser's cooldown is its own.
    assert console_assistants.cooldown_until(company.id, "gemini") is None
    # The model's delivery carries the attempt on its evidence.
    answer = "Fixed.\n\n```python path=calc.py\ndef add(a, b):\n    return a + b\n```\n"
    asyncio.run(worker_deliver._deliver_artifacts(nats, item, agent, sprint, answer))
    [(_, verification)] = _runs(world["engine"], item["id"])
    on_evidence = verification.evidence["console_producer_skipped"]
    assert on_evidence["attempt"]["cooldown"] == "missing"
    assert "console_run" not in verification.evidence
    # Cooling down: the model produces the next task; nothing is asked of the daemon.
    again = _DaemonOverNATS()
    assert asyncio.run(console_host.produce(again, item, dict(agent), sprint)) is None
    assert again.requests == []
    assert again.published[-1]["producer_decision"] == "skipped"


def test_a_quota_stop_starts_the_cooldown_and_the_model_produces(world, cli_path):
    fake_cli(cli_path, "claude", "echo 'Credit balance is too low' >&2\nexit 1\n")
    agent = world["agent"]
    assert (
        asyncio.run(
            console_host.produce(
                _DaemonOverNATS(), world["item"], agent, world["sprint"]
            )
        )
        is None
    )
    assert agent["console_skipped"]["attempt"]["cooldown"] == "quota"
    assert "quota" in agent["console_skipped"]["reason"]
    assert console_assistants.cooldown_until(world["company"].id, "producer:claude")
    assert console_assistants.cooldown_until(world["company"].id, "claude") is None


def test_the_host_advisers_cooldown_does_not_stop_the_producer(world, cli_path):
    from datetime import UTC, datetime, timedelta

    console_assistants.set_runtime_state(
        world["company"].id,
        "claude",
        cooldown_until=datetime.now(UTC) + timedelta(hours=1),
    )
    fake_cli(cli_path, "claude", EDIT_CALC)
    agent = world["agent"]
    assert asyncio.run(
        console_host.produce(_DaemonOverNATS(), world["item"], agent, world["sprint"])
    )
    assert [e["path"] for e in agent["tool_edits"]] == ["calc.py"]


def test_without_the_git_workflow_or_a_cli_the_model_produces(
    world, sqlite_session, cli_path
):
    nats = _DaemonOverNATS()
    other = dict(world["agent"], role="QA")
    assert (
        asyncio.run(console_host.produce(nats, world["item"], other, world["sprint"]))
        is None
    )
    company = sqlite_session.get(Company, world["company"].id)
    company.git_workflow = False
    sqlite_session.add(company)
    sqlite_session.commit()
    assert (
        asyncio.run(
            console_host.produce(nats, world["item"], world["agent"], world["sprint"])
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


class _RecordingHost(console_host.TaskWorkerHost):
    """The task worker's host, noting which of the loop's parts were used."""

    def __init__(self):
        self.calls = []

    def engine(self):
        self.calls.append("engine")
        return super().engine()

    def load_procedures_context(self, company_id, role):
        self.calls.append("load_procedures_context")
        return super().load_procedures_context(company_id, role)

    def resolve_workspace_root(self, company_id):
        self.calls.append("resolve_workspace_root")
        return super().resolve_workspace_root(company_id)

    def previous_attempt_context(self, item):
        self.calls.append("previous_attempt_context")
        return "\n\n## Previous attempt\nPREVIOUS-ATTEMPT"

    def edit_plan_section(self, architect_advice):
        self.calls.append("edit_plan_section")
        return "\n\n## Edit plan to apply\nEDIT-PLAN"

    def record_task_run(self, *args):
        self.calls.append("record_task_run")
        return super().record_task_run(*args)


def test_the_producer_reaches_the_loop_only_through_the_host_it_is_given(
    world, cli_path
):
    fake_cli(cli_path, "claude", EDIT_CALC)
    host, nats = _RecordingHost(), _DaemonOverNATS()
    item, agent, sprint = world["item"], world["agent"], world["sprint"]
    assert asyncio.run(console_producer.produce(nats, item, agent, sprint, host=host))
    assert host.calls == [
        "engine",  # The CLI the company named.
        "engine",  # The Git spec.
        "resolve_workspace_root",
        "engine",  # The sandbox.
        "load_procedures_context",
        "previous_attempt_context",
        "edit_plan_section",
    ]
    # The loop's sections, in the prompt's order: the previous attempt, the plan.
    command = next(p for s, p in nats.requests if s == "workspace.execute")["command"]
    assert command.index("PREVIOUS-ATTEMPT") < command.index("EDIT-PLAN")

    # A run that delivered nothing is recorded through the host as well.
    fake_cli(cli_path, "claude", "echo 'Done.'\n")
    failing = _RecordingHost()
    with pytest.raises(console_producer.ConsoleProducerError):
        asyncio.run(
            console_producer.produce(
                _DaemonOverNATS(), item, dict(agent), sprint, host=failing
            )
        )
    assert failing.calls[-1] == "record_task_run"
    [(_, verification)] = _runs(world["engine"], item["id"])
    assert verification.verdict == "refuted"


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


# ---------------------------------------------------------------------------
# Review findings on PR #447: the host git, the bounds, the alarms
# ---------------------------------------------------------------------------

# A fake CLI that replaces the worktree's .git with a repository of its own
# whose core.fsmonitor writes a marker: any host git run there would run it.
FSMONITOR_PAYLOAD = (
    "printf '#!/bin/sh\\ntouch %s\\nexit 1\\n' \"$MARKER\" > fsm.sh\n"
    "chmod +x fsm.sh\n"
    "rm -f .git\n"
    "git init -q .\n"
    "git add -A && git -c user.name=x -c user.email=x@x commit -qm planted\n"
    'git config core.fsmonitor "$PWD/fsm.sh"\n'
)


def test_the_private_git_is_discarded_and_its_config_never_runs_on_the_host(
    repo, cli_path, tmp_path, monkeypatch
):
    """The CLI may configure its private repository as it likes (here an
    fsmonitor that writes a marker): collect discards it with file
    operations and writes the pointer back before any host git runs."""
    marker = tmp_path / "pwned"
    monkeypatch.setenv("MARKER", str(marker))
    fake_cli(cli_path, "claude", EDIT_CALC + FSMONITOR_PAYLOAD)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    marker.unlink(missing_ok=True)  # The CLI's own git ran it, in its sandbox.
    collected = console_run.collect(
        str(worktree), {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert collected["ok"], collected
    assert "calc.py" in [f["path"] for f in collected["files"]]
    assert not marker.exists()
    assert (worktree / ".git").is_file()
    assert git(str(worktree), "rev-parse", "HEAD") == prepared["head"]
    assert not marker.exists()


def test_the_worktree_carries_a_private_git_the_sandbox_can_use(repo, tmp_path):
    """A linked worktree's .git names the host's git dir, which no sandbox
    mounts: prepare gives it a repository of its own, at the task's head,
    that names nothing of the host, so git works wherever the folder is
    mounted (copied elsewhere here, as at /workspace)."""
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    assert prepared["ok"] and prepared["private_git"] is True
    worktree = Path(prepared["worktree"])
    assert (worktree / ".git").is_dir()
    common = str((repo / ".git").resolve())
    for path in (worktree / ".git").rglob("*"):
        if path.is_file():
            assert common.encode() not in path.read_bytes(), path
    assert not (worktree / ".git" / "objects" / "info" / "alternates").exists()
    mounted = tmp_path / "workspace"
    shutil.copytree(worktree, mounted, symlinks=True)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def inside(*args):
        return subprocess.run(
            ["git", *args],
            cwd=mounted,
            env=clean,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    assert inside("rev-parse", "HEAD") == prepared["head"]
    assert inside("status", "--porcelain") == ""
    assert inside("remote") == ""
    (mounted / "calc.py").write_text("x = 1\n")
    assert "calc.py" in inside("diff", "--name-only", "HEAD")
    # The person's repository gained no object, ref or remote from it.
    assert git(repo, "remote") == ""


def test_a_private_git_left_by_a_run_never_collected_is_removed_before_host_git(
    repo, cli_path, tmp_path, monkeypatch
):
    """A run whose collect never came (the backend stopped) leaves the
    private repository, configured by the CLI; the next delivery in that
    worktree puts git's pointer back before any git runs there."""
    marker = tmp_path / "pwned"
    monkeypatch.setenv("MARKER", str(marker))
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC
        + "printf '#!/bin/sh\\ntouch %s\\nexit 1\\n' \"$MARKER\" > fsm.sh\n"
        + "chmod +x fsm.sh\n"
        + 'git config core.fsmonitor "$PWD/fsm.sh"\n',
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    marker.unlink(missing_ok=True)
    result = daemon.deliver_files(
        str(repo), [{"path": "other.py", "content": "Y = 2\n"}], SPEC
    )
    assert result["ok"], result
    assert result["git"]["commit"] != prepared["head"]
    assert not marker.exists()
    assert (worktree / ".git").is_file()


def test_a_worktree_with_a_command_still_running_keeps_its_private_git(
    repo, cli_path, tmp_path
):
    """A backend that restarted re-queues the task while the daemon still runs
    its CLI: the next attempt in that worktree is refused and the running
    CLI's private repository is left alone."""
    import sprint_integration

    fake_cli(cli_path, "claude", EDIT_CALC)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    assert (worktree / ".git").is_dir()

    def again():
        return daemon.deliver_files(
            str(repo), [{"path": "other.py", "content": "Y = 2\n"}], SPEC
        )

    result = sprint_integration.while_active(str(worktree), again)
    assert not result["ok"]
    assert "still running" in str(result)
    assert (worktree / ".git").is_dir()


def test_a_replaced_private_git_is_refused_and_no_git_runs_on_the_host(
    repo, cli_path, tmp_path, monkeypatch
):
    marker = tmp_path / "pwned"
    monkeypatch.setenv("MARKER", str(marker))
    payload = FSMONITOR_PAYLOAD.replace("rm -f .git", "rm -rf .git")
    planted = tmp_path / "planted"
    payload += f"mv .git {planted} && ln -s {planted} .git\n"
    fake_cli(cli_path, "claude", EDIT_CALC + payload)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    assert (worktree / ".git").is_symlink()  # The CLI did replace it.
    marker.unlink(missing_ok=True)
    collected = console_run.collect(
        str(worktree), {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert collected["ok"] is False and collected["files"] == []
    assert ".git" in collected["refused"][0]
    assert not marker.exists()
    assert (planted / "config").is_file()  # The symlink removed, never its target.
    # The pointer is La Fabrique's again (a file write), and the next prepare
    # puts the worktree back without ever running the payload.
    assert (worktree / ".git").is_file()
    again = console_run.prepare(str(repo), {"git": SPEC})
    assert again["ok"], again
    assert not (worktree / "fsm.sh").exists()
    assert (worktree / "calc.py").read_text().endswith("return 0\n")
    assert not marker.exists()
    # The daemon's alarm wins over every other outcome and is kept on the run.
    failure = console_clis.failure(
        {"timed_out": True, "timeout_seconds": 5}, collected, []
    )
    assert failure == ("failed", collected["refused"][0])


def test_a_dot_git_pointing_at_another_git_dir_is_refused(repo, cli_path, tmp_path):
    evil = tmp_path / "evil.git"
    git(tmp_path, "init", "-q", "--bare", str(evil))
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + f"rm -rf .git && printf 'gitdir: {evil}\\n' > .git\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    assert collected["ok"] is False
    assert "private .git was replaced" in collected["refused"][0]
    assert (
        (Path(prepared["worktree"]) / ".git")
        .read_text()
        .strip()
        .endswith(Path(console_run.admin_dir(Path(prepared["worktree"]))).name)
    )


def test_every_host_git_run_is_hardened_against_the_repositorys_config(
    repo, cli_path, tmp_path, monkeypatch
):
    """An fsmonitor configured in the repository itself never runs either:
    every command names the git dir and turns fsmonitor and hooks off."""
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsm.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    hook.chmod(0o755)
    fake_cli(cli_path, "claude", EDIT_CALC)
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    worktree = Path(prepared["worktree"])
    _run(str(worktree), console_clis.render_command("claude", "go"))
    git(repo, "config", "core.fsmonitor", str(hook))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere"))  # Never inherited.
    collected = console_run.collect(
        str(worktree), {"head": prepared["head"], "guard": prepared["guard"]}, _resolve
    )
    assert [f["path"] for f in collected["files"]] == ["calc.py"]
    assert not marker.exists()
    assert "core.fsmonitor=false" in console_run.SAFE_CONFIG
    assert "core.hooksPath=/dev/null" in console_run.SAFE_CONFIG


def test_a_deletion_or_a_rename_refutes_the_run(repo, cli_path):
    fake_cli(cli_path, "claude", EDIT_CALC + "mv README.md README.txt\n")
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path["README.md"]["change"] == "deleted"
    _, deliver = console_clis.observed_files(collected)
    kind, reason = console_clis.failure(
        {"success": True, "exit_code": 0}, collected, deliver
    )
    assert kind == "failed" and "README.md" in reason and "cannot apply" in reason
    # A listing cut at its bound is never delivered in part.
    cut = dict(collected, files=[by_path["calc.py"]], truncated=True)
    assert (
        "partial listing"
        in console_clis.failure({"success": True, "exit_code": 0}, cut, [1])[1]
    )


def test_the_reply_bound_counts_encoded_bytes_and_the_worktree_waits_for_it(
    repo, cli_path, monkeypatch
):
    monkeypatch.setattr(console_run, "MAX_REPLY_BYTES", 8 * 1024)
    # 2000 characters, 4000 UTF-8 bytes, 12000 bytes once json escapes them.
    fake_cli(
        cli_path,
        "claude",
        EDIT_CALC + "python3 -c \"open('wide.py','w').write('# ' + chr(233)*2000)\"\n",
    )
    prepared = console_run.prepare(str(repo), {"git": SPEC})
    _run(prepared["worktree"], console_clis.render_command("claude", "go"))
    collected = console_run.collect(
        prepared["worktree"],
        {"head": prepared["head"], "guard": prepared["guard"]},
        _resolve,
    )
    import json

    assert len(json.dumps(collected)) <= 8 * 1024
    by_path = {f["path"]: f for f in collected["files"]}
    assert by_path["wide.py"]["status"] == "past_bound"
    assert "content" not in by_path["wide.py"]
    assert git(prepared["worktree"], "status", "--porcelain") == ""
    _, deliver = console_clis.observed_files(collected)
    reason = console_clis.failure({"success": True, "exit_code": 0}, collected, deliver)
    assert reason and "reply's bound" in reason[1]


def test_changes_not_delivered_are_said_by_the_check():
    check = console_clis.check_console_run(
        {
            "console_run": {
                "cli": "claude",
                "label": "Claude Code",
                "outcome": "changed",
                "exit_code": 0,
                "files": [
                    {"path": "calc.py", "delivered": True},
                    {"path": "logo.png", "delivered": False, "status": "binary"},
                ],
            }
        }
    )
    assert check["outcome"] == "pass"
    assert "1 change(s) not delivered: logo.png (binary)" in check["observation"]
    assert check["not_delivered"] == ["logo.png (binary)"]


def test_a_missing_binary_is_never_read_from_the_output_of_a_run_that_exited_0():
    ok = {
        "success": True,
        "exit_code": 0,
        "stderr": "open x: No such file or directory",
    }
    assert console_clis.missing_binary(ok) is False
    assert console_clis.missing_binary({"exit_code": 127}) is True
    assert console_clis.missing_binary(
        {"success": False, "exit_code": None, "error": "No such file or directory"}
    )
    collected = {"ok": True, "files": [{"path": "a.py"}]}
    assert console_clis.failure(ok, collected, [{"path": "a.py"}]) is None


def test_a_prompt_ending_in_a_dollar_passes_the_policy():
    for prompt in ("costs 5$", "a$", "$", "x$\n"):
        command = console_clis.render_command("claude", prompt)
        assert "$'" not in command and '$"' not in command
        assert (
            command_policy.refusal(command, console_clis.command_policy("claude"))
            is None
        ), (prompt, command)


def test_the_command_line_stays_within_one_arguments_limit_in_bytes():
    """The sandbox passes the command as one ``sh -c`` argument (Linux:
    128 KiB, bytes). 100,000 characters of accents and apostrophes quote to
    far more; the prompt is cut, with a note, until the command fits."""
    prompt = "é'" * 50_000
    old = f"{console_clis.command_prefix('claude')} {shlex.quote(prompt)}"
    assert len(old.encode()) > 128 * 1024  # What the char cap alone allowed.
    for cli in console_clis.CLIS:
        command = console_clis.render_command(cli, prompt)
        assert len(command.encode()) <= console_clis.COMMAND_MAX_BYTES
        sent = console_clis.sent_prompt(cli, prompt)
        assert sent.endswith(console_clis.CUT_NOTE)
        assert shlex.split(command)[-1] == sent
        assert console_clis.sent_prompt(cli, sent) == sent  # A fixed point.
        assert command_policy.refusal(command, console_clis.command_policy(cli)) is None
    # The real exec takes it as one argument.
    done = subprocess.run(
        [
            "/bin/sh",
            "-c",
            console_clis.render_command("claude", prompt).replace(
                console_clis.command_prefix("claude"), "printf %s", 1
            ),
        ],
        capture_output=True,
        check=True,
    )
    assert done.stdout.decode() == console_clis.sent_prompt("claude", prompt)
    # A prompt that fits is sent whole.
    assert console_clis.sent_prompt("claude", "Fix add.") == "Fix add."


def test_the_recorded_prompt_hash_is_the_argument_the_cli_got(
    world, cli_path, tmp_path
):
    got = tmp_path / "argument"
    fake_cli(cli_path, "claude", f'printf %s "$4" > {got}\n' + EDIT_CALC)
    item = dict(world["item"], description="é'" * 50_000)
    asyncio.run(
        console_host.produce(_DaemonOverNATS(), item, world["agent"], world["sprint"])
    )
    run = world["agent"]["console_run"]
    assert run["prompt_cut"] is True
    assert run["prompt_sha256"] == hashlib.sha256(got.read_bytes()).hexdigest()
    assert run["prompt_chars"] == len(got.read_bytes().decode())


def test_the_providers_key_reaches_only_the_console_run(
    world, cli_path, sqlite_session
):
    from services import sandbox

    company = sqlite_session.get(Company, world["company"].id)
    company.sandbox_env = ["CI"]
    company.console_env = ["ANTHROPIC_API_KEY"]
    sqlite_session.add(company)
    sqlite_session.commit()
    fake_cli(cli_path, "claude", EDIT_CALC)
    nats = _DaemonOverNATS()
    asyncio.run(
        console_host.produce(nats, world["item"], world["agent"], world["sprint"])
    )
    execute = next(p for s, p in nats.requests if s == "workspace.execute")
    assert execute["sandbox"]["env"] == ["CI", "ANTHROPIC_API_KEY"]
    # The delivery tests, installs and application checks see sandbox_env alone.
    sqlite_session.refresh(company)
    assert sandbox.request_for(company)["env"] == ["CI"]


def test_a_manager_sets_the_console_env(
    sqlite_session, test_client, enforcement, monkeypatch
):
    AssistantRuntimeState.__table__.create(sqlite_session.get_bind(), checkfirst=True)
    company = Company(company_name=f"Env {secrets.token_hex(3)}", git_workflow=True)
    sqlite_session.add(company)
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    url = f"/v1/companies/{company.id}/console_producers"
    body = {"producers": {"Developer": "claude"}, "env": ["OPENAI_API_KEY", ""]}
    saved = test_client.put(url, json=body, headers=manager)
    assert saved.status_code == 200 and saved.json()["env"] == ["OPENAI_API_KEY"]
    # Omitted, it is kept; a bad name is refused.
    kept = test_client.put(url, json={"producers": {}}, headers=manager)
    assert kept.json()["env"] == ["OPENAI_API_KEY"]
    bad = test_client.put(url, json={"producers": {}, "env": ["A B"]}, headers=manager)
    assert bad.status_code == 422
    sqlite_session.refresh(company)
    assert company.sandbox_env is None
