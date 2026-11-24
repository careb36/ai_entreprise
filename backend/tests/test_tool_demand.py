"""Tool demand per sprint (``contracts/tool_demand.py``, issue #766).

What a sprint's agents asked of their tools and did not get, counted from the
platform's own records: unknown tool names, final answers that were a tool
call written as text, refused calls and runs that spent their rounds. The
retrospective shows the counts beside its other numbers, as observations.
"""

from __future__ import annotations

from models import AgentRun, Verification
from models.agent_run import TASK_WORKER_PRODUCER
from services.contracts import tool_demand
from services.learning import retrospective
from services.platform.orchestration_metrics import record_agent_turn
from services.tasks import task_events
from services.tools import developer_tools, mcp_tools, research_tools
from sprint_board_helpers import _setup

UNKNOWN = {"server": "", "tool": "", "reason": "unknown tool", "requested": "shell"}
FETCH = {"server": "research", "tool": "fetch", "refused": research_tools.HOST_REFUSED}
SCRIPT = {"server": "workspace", "tool": "run_tests", "check_script": {"refused": "x"}}
READ = {"server": "workspace", "tool": "read", "is_error": False}
TYPED = f"No deliverable from m: {mcp_tools.TOOL_CALL_AS_TEXT}"


def test_the_words_match_the_tool_loops():
    assert tool_demand.TOOL_CALL_AS_TEXT == mcp_tools.TOOL_CALL_AS_TEXT


def test_demand_is_counted_from_fixture_observations():
    demand = tool_demand.count(
        [UNKNOWN, dict(UNKNOWN), FETCH, SCRIPT, READ, "not a dict"],
        [{"rounds_exhausted": True}, {"rounds_exhausted": False}, {}],
        [TYPED, "Blocked: something else", None],
    )
    assert demand == {
        "unknown_tools": {"shell": 2},
        "unknown_tool_calls": 2,
        "typed_tool_calls": 1,
        "refused": {"research__fetch": 1, "workspace__run_tests": 1},
        "refused_calls": 2,
        "rounds_exhausted": 1,
    }
    # Deterministic: the same records in another order, the same answer.
    again = tool_demand.count(
        [READ, SCRIPT, FETCH, UNKNOWN, dict(UNKNOWN)],
        [{}, {"rounds_exhausted": True}],
        [None, TYPED],
    )
    assert again == demand


def test_the_line_says_the_counts_are_observations():
    line = tool_demand.summary_line(tool_demand.count([UNKNOWN, FETCH]))
    assert line.startswith("- Tool demand (observed by the platform): ")
    assert "1 unknown tool name(s) asked (shell x1)" in line
    assert "1 refused call(s) (research__fetch x1)" in line
    assert "0 run(s) that spent their tool rounds" in line


def test_a_run_tests_command_not_run_as_asked_is_recorded_as_refused():
    plans = {"backend": {"test": "pytest -q", "install": None}}
    ok = {"exit_code": 0, "stdout": "1 passed\n", "stderr": ""}
    box = developer_tools.WorkspaceToolbox(
        lambda subject, payload, timeout: ok, "/p", folder_tests=plans
    )

    def run(command):
        call = {
            "id": "c",
            "function": {
                "name": "workspace__run_tests",
                "arguments": {"command": command, "folder": "backend"},
            },
        }
        return box.call(call, 1)[0]

    assert run("npm test")["refused"] == developer_tools.NOT_RUN
    assert "shell operator" in run("pytest -q; rm -rf /")["refused"]
    assert "refused" not in run("pytest -q tests/test_x.py")


def _sprint_records(session):
    company, sprint, (first, second) = _setup(session)
    run = AgentRun(
        company_id=company.id,
        external_ref=f"task-{first.id}",
        version=1,
        producer=TASK_WORKER_PRODUCER,
        artifact_digest="0" * 64,
        artifact_size=0,
    )
    session.add(run)
    session.commit()
    evidence = {"tool_calls": [UNKNOWN, FETCH, READ], "rounds_exhausted": True}
    # An older verification of the same run is not counted twice.
    for verdict in ("unverifiable", "verified"):
        session.add(
            Verification(
                company_id=company.id,
                agent_run_id=run.id,
                verdict=verdict,
                evidence=evidence,
            )
        )
        session.commit()
    # The QA review's reads, on its audit turn.
    record_agent_turn(
        company_id=company.id,
        sprint_id=sprint.id,
        backlog_item_id=second.id,
        agent={"role": "QA", "name": "Quinn", "model": "m"},
        action="qa_review",
        llm_result=None,
        session=session,
        tool_calls=[SCRIPT, dict(UNKNOWN, requested="workspace__shell")],
    )
    # The worker's block after a tool call written as text.
    task_events.record(
        session, second, "blocked", task_events.SYSTEM, text=TYPED, status="blocked"
    )
    session.commit()
    return sprint


def test_the_retrospective_counts_the_sprints_demand(sqlite_session):
    sprint = _sprint_records(sqlite_session)
    metrics = retrospective.build(sqlite_session.get_bind(), sprint.id)
    assert metrics["tool_demand"] == {
        "unknown_tools": {"shell": 1, "workspace__shell": 1},
        "unknown_tool_calls": 2,
        "typed_tool_calls": 1,
        "refused": {"research__fetch": 1, "workspace__run_tests": 1},
        "refused_calls": 2,
        "rounds_exhausted": 1,
    }


def test_the_retrospective_shows_the_demand_beside_its_numbers(sqlite_session):
    sprint = _sprint_records(sqlite_session)
    summary = retrospective.summary(
        retrospective.build(sqlite_session.get_bind(), sprint.id)
    )
    lines = summary.splitlines()
    demand = next(line for line in lines if line.startswith("- Tool demand"))
    assert lines.index(demand) > lines.index(
        next(line for line in lines if line.startswith("- QA failures"))
    )
    assert "(observed by the platform)" in demand
    assert "2 unknown tool name(s) asked (shell x1, workspace__shell x1)" in demand
    assert "1 final answer(s) that were a tool call written as text" in demand
    assert "1 run(s) that spent their tool rounds" in demand
