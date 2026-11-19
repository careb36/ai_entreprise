"""Console producers through the task worker.

The observed file is committed on the task's branch and verified with the
``console_run`` check; a sandbox a CLI cannot use leaves the task to the
model and says why; a CLI that only claims success, times out, is missing or
stops on its quota never passes, the model produces meanwhile; the producer
reaches the loop only through its host, records the prompt it sent, and gets
the provider's key alone.

Console producers: a terminal coding agent produces a role's task, and the
delivery is what the daemon observes (``services/producers/console_producer.py``,
``services/producers/console_clis.py``, ``vscode-daemon/console_run.py``).
Real git in ``tmp_path`` and the real daemon, with a fake CLI on ``PATH`` (as
``test_console_assistants`` and the e2e's ``e2e-copilot`` do); the shared
fixtures are in ``console_producer_helpers``.
"""

import asyncio
import hashlib
import sys
from pathlib import Path

import pytest
from config import settings
from models import Company
from services.loop import console_host, task_worker
from services.loop.worker import deliver as worker_deliver
from services.loop.worker import quality_gate
from services.producers import console_assistants, console_clis, console_producer
from sqlmodel import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import console_producer_helpers
from console_producer_helpers import (
    EDIT_CALC,
    _board,
    _check,
    _DaemonOverNATS,
    _runs,
    fake_cli,
    git,
)

# The shared fixtures.
repo = console_producer_helpers.repo
cli_path = console_producer_helpers.cli_path
world = console_producer_helpers.world


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


@pytest.mark.parametrize("forge", ["fetch", "none"])
@pytest.mark.parametrize("delivers", [True, False], ids=["changed", "failed"])
def test_the_base_the_branch_started_from_is_kept_on_the_console_run(
    world, cli_path, repo, tmp_path, delivers, forge
):
    main = git(repo, "rev-parse", "main")
    git(repo, "remote", "add", "origin", (tmp_path / "nowhere.git").as_uri())
    with Session(world["engine"]) as session:
        company = session.get(Company, world["sprint"]["company_id"])
        company.forge_policy = forge
        session.add(company)
        session.commit()
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
    assert evidence["forge_policy"] == forge
    if forge == "fetch":
        assert evidence["base_remote"] == "origin/main"
        assert evidence["remote_unreachable"] and evidence["remote_contacted"] is True
    else:
        # The forge policy keeps git local (issue #727): no fetch was tried.
        assert evidence["base_remote"] is None and "remote_unreachable" not in evidence
        assert evidence["base_remote_not_contacted"] == "origin/main"
        assert evidence["remote_contacted"] is False


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
    from services.workspace import sandbox

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
