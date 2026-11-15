"""
services/producers/console_assistants.py: the terminal assistants the daemon can run.

- Which assistants are asked: Copilot by default, Claude only for high-value
  work (role, priority or strategic wording).
- The prompt is shell-quoted into the command, so a task's text cannot inject
  a command; the fallback wrapper swaps only the binary.
- An assistant out of quota or missing on the host enters a per-company
  cooldown that is persisted and cleared once it has passed.
- The full path: a failed Claude run with a quota message starts the
  cooldown, Copilot's advice is used, and every decision reaches the
  Glassroom as ``assistant.telemetry``.

Run with:
    cd backend && PYTHONPATH=.:app python3 -m pytest tests/test_console_assistants.py -q
"""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from models import AssistantRuntimeState, Company
from services.producers import console_assistants, console_capabilities
from sqlmodel import select

COPILOT = "copilot -p {prompt} --output-format json"
CLAUDE = "claude --print --add-dir {workspace_root} {prompt}"
DEV = {"role": "Developer", "name": "Dev"}
ARCHITECT = {"role": "Architect", "name": "Ada"}


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(console_assistants, "CONSOLE_ASSISTANTS_ENABLED", True)
    monkeypatch.setattr(
        console_assistants, "CONSOLE_ASSISTANT_STRATEGY", "copilot_first"
    )
    monkeypatch.setattr(console_assistants, "COPILOT_CLI_COMMAND_TEMPLATE", COPILOT)
    monkeypatch.setattr(console_assistants, "CLAUDE_CLI_COMMAND_TEMPLATE", CLAUDE)
    monkeypatch.setattr(
        console_assistants, "CLAUDE_CLI_HIGH_VALUE_ROLES", {"Architect", "QA"}
    )
    monkeypatch.setattr(console_assistants, "CLAUDE_CLI_DEFAULT_COOLDOWN_MINUTES", 10)
    monkeypatch.setattr(
        console_assistants, "ASSISTANT_UNAVAILABLE_COOLDOWN_MINUTES", 30
    )


@pytest.fixture()
def db(monkeypatch, sqlite_session):
    bind = sqlite_session.get_bind()
    AssistantRuntimeState.__table__.create(bind, checkfirst=True)
    monkeypatch.setattr(console_assistants, "engine", bind)
    company = Company(company_name=f"Assist {secrets.token_hex(3)}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    return sqlite_session, company


def _state(session, company_id, key):
    session.expire_all()
    return session.exec(
        select(AssistantRuntimeState).where(
            AssistantRuntimeState.company_id == company_id,
            AssistantRuntimeState.assistant_key == key,
        )
    ).first()


# ---------------------------------------------------------------------------
# Which assistants, and what for
# ---------------------------------------------------------------------------


def test_copilot_is_the_default_and_claude_joins_for_high_value_work(configured):
    routine = {"title": "Rename a field", "description": "Cosmetic."}
    strategic = {"title": "Refactor the storage layer", "description": "Plan it."}

    assert [a[0] for a in console_assistants.select_assistants(routine, DEV)] == [
        "GitHub Copilot CLI"
    ]
    assert [a[0] for a in console_assistants.select_assistants(routine, ARCHITECT)] == [
        "GitHub Copilot CLI",
        "Claude CLI",
    ]
    assert [a[0] for a in console_assistants.select_assistants(strategic, DEV)] == [
        "GitHub Copilot CLI",
        "Claude CLI",
    ]
    assert console_assistants.assistant_purpose(routine, ARCHITECT, "Claude CLI") == (
        "planning and architecture advice"
    )
    assert console_assistants.assistant_purpose(
        {"title": "x", "priority": "high"}, DEV, "GitHub Copilot CLI"
    ) == ("high-priority implementation guidance")


def test_without_a_copilot_template_only_high_value_work_gets_an_assistant(
    configured, monkeypatch
):
    monkeypatch.setattr(console_assistants, "COPILOT_CLI_COMMAND_TEMPLATE", "")

    assert console_assistants.select_assistants({"title": "Rename"}, DEV) == []
    assert [
        a[0]
        for a in console_assistants.select_assistants({"title": "Rename"}, ARCHITECT)
    ] == ["Claude CLI"]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_the_prompt_is_quoted_into_the_command(configured):
    prompt = "Task: fix it'; rm -rf / #"

    command = console_assistants.render_command(COPILOT, prompt, "/home/ana/app")
    assert (
        command == "copilot -p 'Task: fix it'\"'\"'; rm -rf / #' --output-format json"
    )

    plain = console_assistants.render_command("aider --yes", prompt, "/home/ana/app")
    assert plain == "aider --yes 'Task: fix it'\"'\"'; rm -rf / #'"
    assert console_assistants.render_command(CLAUDE, "p", "/dir with space") == (
        "claude --print --add-dir '/dir with space' p"
    )
    assert console_assistants.render_command("  ", prompt, "/x") is None


def test_the_fallback_wrapper_swaps_only_the_binary(configured):
    command = "copilot -p 'x' --output-format json"

    wrapped = console_assistants.wrap_command_with_fallback(
        "GitHub Copilot CLI", command
    )

    fallback = '"$HOME/Library/Application Support/Code/User/globalStorage/github.copilot-chat/copilotCli/copilot"'
    assert wrapped.startswith(f"if [ -x {fallback} ]; then {fallback} -p 'x'")
    assert wrapped.endswith(f"else {command}; fi")
    assert (
        console_assistants.replace_command_binary("claude", "claude", "/bin/claude")
        == "/bin/claude"
    )
    assert (
        console_assistants.replace_command_binary(
            "claudette -p", "claude", "/bin/claude"
        )
        == "claudette -p"
    )
    assert console_assistants.wrap_command_with_fallback("Other", command) == command


# ---------------------------------------------------------------------------
# Cooldowns
# ---------------------------------------------------------------------------


def test_a_quota_message_starts_a_persisted_claude_cooldown(configured, db):
    session, company = db

    assert console_assistants.activate_claude_cooldown(company.id, "all good") is None
    assert _state(session, company.id, "claude") is None

    before = datetime.now(UTC)
    deadline = console_assistants.activate_claude_cooldown(
        company.id, "Claude usage limit reached. Try again later."
    )

    assert deadline is not None
    assert before + timedelta(minutes=9) < deadline <= before + timedelta(minutes=11)
    row = _state(session, company.id, "claude")
    assert row.last_error == "Claude usage limit reached. Try again later."
    assert console_assistants.cooldown_until(company.id, "claude") == deadline


def test_a_reset_time_in_the_message_sets_the_deadline(configured):
    deadline = console_assistants.parse_claude_reset_time(
        "You hit the rate limit; it resets at 3:30pm."
    )

    assert deadline is not None
    assert deadline > datetime.now(UTC)
    assert deadline.astimezone().strftime("%H:%M") == "15:30"
    assert console_assistants.parse_claude_reset_time("no time here") is None


def test_a_passed_cooldown_is_cleared_when_read(configured, db):
    session, company = db
    console_assistants.set_runtime_state(
        company.id,
        "copilot",
        cooldown_until=datetime.now(UTC) - timedelta(minutes=1),
        last_error="command not found",
    )

    assert console_assistants.cooldown_until(company.id, "copilot") is None

    row = _state(session, company.id, "copilot")
    assert row.cooldown_until is None
    assert row.last_error is None


def test_a_missing_binary_cools_the_assistant_down_for_half_an_hour(configured, db):
    _session, company = db
    before = datetime.now(UTC)

    assert (
        console_assistants.activate_unavailable_cooldown(
            company.id, "GitHub Copilot CLI", "exit 1"
        )
        is None
    )
    deadline = console_assistants.activate_unavailable_cooldown(
        company.id, "GitHub Copilot CLI", "bash: copilot: command not found"
    )

    assert before + timedelta(minutes=29) < deadline <= before + timedelta(minutes=31)
    assert console_assistants.cooldown_until(company.id, "copilot") == deadline
    assert console_assistants.assistant_key_for_label("My Tool 2") == "my_tool_2"


# ---------------------------------------------------------------------------
# Asking them
# ---------------------------------------------------------------------------


class _Daemon:
    """A NATS client whose daemon answers ``workspace.execute`` per binary."""

    def __init__(self, answers):
        self.answers = answers
        self.executed: list[dict] = []
        self.published: list[tuple[str, dict]] = []

    async def request(self, subject, payload, timeout=5.0):
        assert subject == "workspace.execute"
        self.executed.append(payload)
        command = payload["command"]
        assert command.startswith("if "), "commands reach the daemon with a fallback"
        for binary, answer in self.answers.items():
            if f" {binary} " in command:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(command)

    async def publish(self, subject, payload, msg_id=None):
        self.published.append((subject, payload))


def _collect(nats, item, agent, company_id, root="/home/ana/app"):
    sprint = {"id": 5, "company_id": company_id, "name": "Sprint 5"}
    return asyncio.run(
        console_assistants.collect_advice(nats, item, agent, sprint, lambda cid: root)
    )


@pytest.fixture()
def daemon_with_both(monkeypatch):
    console_capabilities.reset()
    monkeypatch.setattr(console_capabilities, "_available", ["copilot", "claude"])
    monkeypatch.setattr(console_capabilities, "_probed", True)
    yield
    console_capabilities.reset()


def test_claude_out_of_quota_enters_cooldown_and_copilot_advice_is_used(
    configured, db, daemon_with_both
):
    session, company = db
    nats = _Daemon(
        {
            "copilot": {"success": True, "stdout": "Split the module in two.\n"},
            "claude": {
                "success": False,
                "exit_code": 1,
                "stderr": "Claude usage limit reached, resets at 11:00pm",
            },
        }
    )
    item = {"id": 11, "title": "Design the storage layer", "description": "Schema."}

    result = _collect(nats, item, ARCHITECT, company.id)

    assert result["combined"] == "[GitHub Copilot CLI]\nSplit the module in two."
    assert result["sources"] == [
        {
            "label": "GitHub Copilot CLI",
            "purpose": "incremental implementation guidance",
            "excerpt": "Split the module in two.",
        }
    ]
    assert [p["workspace_root"] for p in nats.executed] == ["/home/ana/app"] * 2
    assert _state(session, company.id, "claude").cooldown_until is not None

    events = [p for s, p in nats.published if s == "glassroom.5"]
    assert all(e["semantic_type"] == "assistant.telemetry" for e in events)
    assert [(e["assistant_label"], e["assistant_decision"]) for e in events] == [
        ("GitHub Copilot CLI", "requested"),
        ("GitHub Copilot CLI", "used"),
        ("Claude CLI", "requested"),
        ("Claude CLI", "quota_cooldown"),
    ]
    assert events[0]["task_id"] == 11

    # The next task skips Claude without asking the daemon.
    again = _Daemon({"copilot": {"success": True, "stdout": "Same advice."}})
    _collect(again, item, ARCHITECT, company.id)
    assert len(again.executed) == 1
    decisions = [p["assistant_decision"] for s, p in again.published]
    assert "skipped_quota_cooldown" in decisions

    comment = console_assistants.usage_comment(result["sources"])
    assert comment == (
        "External assistant context used for this task:\n"
        "- GitHub Copilot CLI (incremental implementation guidance): Split the module in two."
    )
    assert console_assistants.usage_comment([]) == ""


def test_a_rework_attempt_reuses_the_first_attempt_s_advice(
    configured, db, daemon_with_both
):
    """The assistants are asked once per task: a second run would answer the
    same prompt again, so the rework quotes the board note instead."""
    _session, company = db
    nats = _Daemon(
        {"copilot": {"success": True, "stdout": "Split the module in two.\n"}}
    )
    item = {"id": 12, "title": "Fix typo", "description": "A letter."}

    first = _collect(nats, item, DEV, company.id)
    assert first["combined"] == "[GitHub Copilot CLI]\nSplit the module in two."
    note = console_assistants.usage_comment(first["sources"])
    item["comments"] = [
        {"author": "System", "text": note},
        {"author": "Atlas", "text": "Here it is."},
        {"author": "QA", "text": "NEEDS WORK: the letter is still wrong"},
    ]

    again = _Daemon({"copilot": {"success": True, "stdout": "Same advice.\n"}})
    second = _collect(again, item, DEV, company.id)

    assert again.executed == [], "no CLI runs on the rework"
    assert again.published == []
    assert second == {
        "combined": "- GitHub Copilot CLI (incremental implementation guidance): Split the module in two.",
        "sources": [],
    }
    assert console_assistants.usage_comment(second["sources"]) == "", (
        "no second note: the task counts as advised once"
    )
    # Only the worker's own note is advice; a deliverable that starts the same
    # way is not, so the assistants still run for an unadvised task.
    item["comments"] = [{"author": "Atlas", "text": note}]
    third = _collect(
        _Daemon({"copilot": {"success": True, "stdout": "x"}}), item, DEV, company.id
    )
    assert third["sources"] != []


def test_a_failed_request_and_a_missing_binary_do_not_stop_the_task(
    configured, db, daemon_with_both
):
    session, company = db
    nats = _Daemon(
        {
            "copilot": {
                "success": False,
                "exit_code": 127,
                "stderr": "copilot: command not found",
            },
            "claude": TimeoutError("daemon busy"),
        }
    )

    result = _collect(
        nats, {"id": 12, "title": "Review the auth flow"}, DEV, company.id
    )

    assert result == {"combined": "", "sources": []}
    assert _state(session, company.id, "copilot").cooldown_until is not None
    assert _state(session, company.id, "claude") is None
    decisions = [p["assistant_decision"] for s, p in nats.published]
    assert decisions == ["requested", "unavailable_cooldown", "requested", "failed"]


def test_nothing_is_asked_when_disabled_or_without_a_project(
    configured, monkeypatch, daemon_with_both
):
    calls: list[int] = []

    def _root(company_id):
        calls.append(company_id)

    nats = _Daemon({})
    sprint = {"id": 5, "company_id": 3, "name": "Sprint 5"}
    item = {"id": 1, "title": "Anything"}

    assert asyncio.run(
        console_assistants.collect_advice(nats, item, DEV, sprint, _root)
    ) == {"combined": "", "sources": []}
    assert calls == [3], "the project folder is looked up once an assistant will run"

    monkeypatch.setattr(console_assistants, "CONSOLE_ASSISTANTS_ENABLED", False)
    asyncio.run(console_assistants.collect_advice(nats, item, DEV, sprint, _root))
    monkeypatch.setattr(console_assistants, "CONSOLE_ASSISTANTS_ENABLED", True)
    asyncio.run(
        console_assistants.collect_advice(
            nats, item, {"role": "TechWriter"}, sprint, _root
        )
    )
    assert calls == [3], (
        "disabled, or a role not allowed: the folder is not even looked up"
    )
    assert nats.executed == []


# ---------------------------------------------------------------------------
# Where they run: the company's sandbox, purpose ``assistant`` (issue #525)
# ---------------------------------------------------------------------------

FORCED_CONTAINER = {
    "mode": "container",
    "forced_by_daemon": True,
    "isolated": True,
    "network": "off",
    "env": ["ASSISTANT_PROVIDER_KEY"],
}


def _with_sandbox(session, company, mode, sandbox_env=None, console_env=None):
    company.delivery_sandbox = mode
    company.sandbox_env = sandbox_env
    company.console_env = console_env
    session.add(company)
    session.commit()


def _decisions(nats):
    return [
        (p["assistant_label"], p["assistant_decision"], p["assistant_detail"])
        for s, p in nats.published
        if s == "glassroom.5"
    ]


def test_a_host_company_sends_its_sandbox_and_runs_as_before(
    configured, db, daemon_with_both, monkeypatch
):
    from config import settings

    session, company = db
    _with_sandbox(session, company, "host", ["CI_FLAG"], ["ASSISTANT_PROVIDER_KEY"])
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", False)
    applied = {"mode": "host", "forced_by_daemon": False, "isolated": False}
    nats = _Daemon(
        {"copilot": {"success": True, "stdout": "Advice.", "sandbox": applied}}
    )

    result = _collect(nats, {"id": 21, "title": "Rename a field"}, DEV, company.id)

    assert result["combined"] == "[GitHub Copilot CLI]\nAdvice."
    box = nats.executed[0]["sandbox"]
    assert box["mode"] == "host" and box["purpose"] == "assistant"
    assert box["cache"] is False and box["network"] is False
    # Names only, never values: the daemon reads them from its own environment.
    assert box["env"] == ["CI_FLAG", "ASSISTANT_PROVIDER_KEY"]
    assert "command_policy" not in nats.executed[0]
    used = [p for s, p in nats.published if p["assistant_decision"] == "used"]
    assert used[0]["assistant_sandbox"] == applied


def test_a_contained_company_without_network_skips_with_the_reason(
    configured, db, daemon_with_both, monkeypatch
):
    from config import settings

    session, company = db
    _with_sandbox(session, company, "container")
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", False)
    nats = _Daemon({})

    item = {"id": 22, "title": "Design the storage layer"}
    result = _collect(nats, item, ARCHITECT, company.id)

    assert result == {"combined": "", "sources": []}, "the task goes on"
    assert nats.executed == [], "nothing is sent, nothing runs on the host"
    decisions = _decisions(nats)
    assert [d[:2] for d in decisions] == [
        ("GitHub Copilot CLI", "skipped_sandbox"),
        ("Claude CLI", "skipped_sandbox"),
    ]
    assert "DELIVERY_SANDBOX_NETWORK" in decisions[0][2]
    assert _state(session, company.id, "copilot") is None, "no cooldown: a setting"


def test_a_contained_company_with_network_sends_the_container(
    configured, db, daemon_with_both, monkeypatch
):
    from config import settings

    session, company = db
    _with_sandbox(session, company, "container", None, ["ASSISTANT_PROVIDER_KEY"])
    monkeypatch.setattr(settings, "DELIVERY_SANDBOX_NETWORK", True)
    applied = {**FORCED_CONTAINER, "forced_by_daemon": False, "network": "on"}
    nats = _Daemon(
        {"copilot": {"success": True, "stdout": "Advice.", "sandbox": applied}}
    )

    result = _collect(nats, {"id": 23, "title": "Rename a field"}, DEV, company.id)

    assert result["combined"] == "[GitHub Copilot CLI]\nAdvice."
    box = nats.executed[0]["sandbox"]
    assert box["mode"] == "container" and box["network"] is True
    assert box["purpose"] == "assistant"
    assert box["env"] == ["ASSISTANT_PROVIDER_KEY"]


@pytest.mark.parametrize(
    "answer,reason",
    [
        (
            {
                "success": False,
                "exit_code": 127,
                "stderr": "copilot: not found",
                "sandbox": {**FORCED_CONTAINER, "network": "on"},
            },
            "the CLI is not installed in the container sandbox the daemon requires",
        ),
        (
            {
                "success": False,
                "exit_code": 1,
                "stderr": "connection refused",
                "sandbox": FORCED_CONTAINER,
            },
            "the container sandbox the daemon requires has the network off",
        ),
        (
            {
                "success": False,
                "error": "sandbox unavailable: container mode needs Docker or Podman",
            },
            "the daemon could not apply its sandbox: container mode needs Docker",
        ),
    ],
)
def test_what_the_daemon_s_sandbox_stopped_is_skipped_with_the_reason(
    configured, db, daemon_with_both, answer, reason
):
    session, company = db
    _with_sandbox(session, company, "host")
    nats = _Daemon({"copilot": answer})

    result = _collect(nats, {"id": 24, "title": "Rename a field"}, DEV, company.id)

    assert result == {"combined": "", "sources": []}
    decisions = _decisions(nats)
    assert [d[1] for d in decisions] == ["requested", "skipped_sandbox"]
    assert decisions[1][2].startswith(reason)
    skipped = [
        p for s, p in nats.published if p["assistant_decision"] == "skipped_sandbox"
    ]
    assert skipped[0].get("assistant_sandbox") == answer.get("sandbox")
    state = _state(session, company.id, "copilot")
    assert state.cooldown_until is not None and state.last_error.startswith(reason)


def test_a_company_whose_sandbox_cannot_be_read_asks_no_assistant(
    configured, db, daemon_with_both, monkeypatch
):
    from services.workspace.sandbox import PolicyUnavailable

    _session, company = db

    def _unreadable(sprint, engine=None):
        raise PolicyUnavailable("the company's sandbox could not be read (Error)")

    monkeypatch.setattr(
        console_assistants.assistant_sandbox, "request_for", _unreadable
    )
    nats = _Daemon({})

    result = _collect(nats, {"id": 25, "title": "Rename a field"}, DEV, company.id)

    assert result == {"combined": "", "sources": []} and nats.executed == []
    assert [d[1] for d in _decisions(nats)] == ["skipped_sandbox"]
    assert "known sandbox" in _decisions(nats)[0][2]


def test_the_protocol_fixture_s_assistant_reply_is_read_as_advice(
    configured, db, daemon_with_both
):
    import json
    from pathlib import Path

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "vscode-daemon/protocol/fixtures/v1/workspace.execute"
            / "assistant-on-the-host.json"
        ).read_text()
    )
    session, company = db
    _with_sandbox(session, company, "host")
    nats = _Daemon({"copilot": fixture["reply"]})

    result = _collect(nats, {"id": 26, "title": "Rename a field"}, DEV, company.id)

    assert result["combined"] == "[GitHub Copilot CLI]\nadvice"
