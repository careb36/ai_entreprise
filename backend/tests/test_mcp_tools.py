"""MCP tools in a task: discovery, the bounded loop, the record and the risk.

The server is a real MCP server in this process (``mcp.server.mcpserver``),
reached through the same client code production uses; the model is a fake
proxy that answers with tool calls and then with content.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import worker_stages
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from models.agent_run import AgentRun
from models.company import Company
from models.mcp import McpServer
from services.evidence import risk
from services.loop.worker import execute as worker_execute
from services.loop.worker import record as worker_record
from services.tools import mcp_config, mcp_tools
from services.verification import check_risk_tier, check_tools_used

SECRET = "s3cret-token-value"


def _server() -> MCPServer:
    srv = MCPServer("tracker")

    @srv.tool(annotations=ToolAnnotations(read_only_hint=True))
    def search_issues(query: str, limit: int = 5) -> str:
        """Search the tracker."""
        return f"found {limit} issues for {query} (token {SECRET})"

    @srv.tool()
    def create_issue(title: str) -> str:
        """Open an issue."""
        return "x" * 10_000

    @srv.tool()
    def boom(x: str) -> str:
        raise RuntimeError("nope")

    return srv


@pytest.fixture()
def toolbox():
    box = mcp_tools.Toolbox(
        [
            {
                "name": "tracker",
                "transport": "inproc",
                "server": _server(),
                "env": {"TOKEN": SECRET},
            }
        ],
        result_max_chars=500,
    )
    yield box
    box.close()


class _Model:
    """A fake proxy: answers in order, remembering what it was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def chat(self, messages, model, role, bu_id=None, tools=None):
        self.requests.append({"messages": list(messages), "tools": tools})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer


def _call(name, **arguments):
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _answer_with_calls(*calls):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(calls),
                }
            }
        ]
    }


def _answer(text):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "served_model": "m",
    }


def test_the_toolbox_lists_the_server_s_tools_in_openai_shape(toolbox):
    schemas = toolbox.schemas()
    names = [s["function"]["name"] for s in schemas]
    assert names == ["tracker__boom", "tracker__create_issue", "tracker__search_issues"]
    search = next(
        s for s in schemas if s["function"]["name"] == "tracker__search_issues"
    )
    assert search["type"] == "function"
    assert search["function"]["description"] == "Search the tracker."
    assert search["function"]["parameters"]["required"] == ["query"]
    assert toolbox.tools["tracker__search_issues"]["read_only"] is True
    assert toolbox.tools["tracker__create_issue"]["read_only"] is False
    assert toolbox.unavailable == {}


def test_the_loop_runs_the_calls_records_them_and_returns_the_final_answer(toolbox):
    model = _Model(
        [
            _answer_with_calls(_call("tracker__search_issues", query="auth")),
            _answer("done"),
        ]
    )
    rounds = []
    result, observations = mcp_tools.chat_with_tools(
        model,
        [{"role": "user", "content": "do it"}],
        "m",
        "Developer",
        toolbox,
        on_round=lambda r, calls: rounds.append(len(calls)),
    )
    assert result["choices"][0]["message"]["content"] == "done"
    assert rounds == [1]
    assert len(observations) == 1
    obs = observations[0]
    assert (obs["server"], obs["tool"], obs["arguments"]) == (
        "tracker",
        "search_issues",
        {"query": "auth"},
    )
    assert obs["is_error"] is False and obs["read_only"] is True and obs["round"] == 1
    assert (
        obs["result_sha256"] and obs["result_chars"] > 0 and obs["truncated"] is False
    )
    # The first request carried the tools, the second the call and its masked result.
    assert model.requests[0]["tools"] and model.requests[1]["tools"]
    tool_message = model.requests[1]["messages"][-1]
    assert (
        tool_message["role"] == "tool"
        and tool_message["tool_call_id"] == "call_tracker__search_issues"
    )
    assert "found 5 issues for auth" in tool_message["content"]
    assert SECRET not in tool_message["content"] and "***" in tool_message["content"]
    assert (
        model.requests[1]["messages"][-2]["tool_calls"][0]["id"]
        == "call_tracker__search_issues"
    )


def test_a_long_result_is_cut_for_the_model_and_the_record_says_so(toolbox):
    model = _Model(
        [_answer_with_calls(_call("tracker__create_issue", title="t")), _answer("ok")]
    )
    _result, observations = mcp_tools.chat_with_tools(
        model, [], "m", "Developer", toolbox
    )
    assert observations[0]["truncated"] is True
    assert observations[0]["result_chars"] == 10_000
    content = model.requests[1]["messages"][-1]["content"]
    assert len(content) < 600 and content.endswith("[result cut here]")


def test_a_failing_tool_an_unknown_tool_and_bad_arguments_are_error_results(toolbox):
    model = _Model(
        [
            _answer_with_calls(
                _call("tracker__boom", x="1"),
                _call("tracker__missing"),
                {
                    "id": "c3",
                    "function": {
                        "name": "tracker__search_issues",
                        "arguments": "{not json",
                    },
                },
            ),
            _answer("ok"),
        ]
    )
    _result, observations = mcp_tools.chat_with_tools(
        model, [], "m", "Developer", toolbox
    )
    assert [o["is_error"] for o in observations] == [True, True, True]
    assert observations[1]["reason"] == "unknown tool"
    assert observations[2]["reason"].startswith("malformed arguments")
    texts = [m["content"] for m in model.requests[1]["messages"] if m["role"] == "tool"]
    assert "Error executing tool boom" in texts[0]
    assert texts[1] == mcp_tools.NO_TOOL_NOTE
    assert texts[2].startswith("Error: malformed arguments")


def test_the_rounds_are_capped_and_the_last_request_carries_no_tools(toolbox):
    model = _Model([_answer_with_calls(_call("tracker__search_issues", query="q"))])
    result, observations = mcp_tools.chat_with_tools(
        model, [], "m", "Developer", toolbox, max_rounds=2
    )
    assert len(observations) == 2
    assert [r["tools"] is not None for r in model.requests] == [True, True, False]
    assert model.requests[-1]["messages"][-1] == {
        "role": "user",
        "content": mcp_tools.ROUNDS_EXHAUSTED_NOTE,
    }
    # The capped answer is whatever the model said last, tool calls included.
    assert mcp_tools.tool_calls_of(result)


def test_an_unreachable_server_is_named_and_the_task_goes_on_without_tools():
    box = mcp_tools.Toolbox(
        [
            {
                "name": "gone",
                "transport": "stdio",
                "command": "/nonexistent/mcp-server",
                "args": [],
            }
        ],
        connect_timeout=5,
    )
    try:
        assert box.schemas() == []
        assert list(box.unavailable) == ["gone"]
        model = _Model([_answer("plain")])
        result, observations = mcp_tools.chat_with_tools(
            model, [{"role": "user", "content": "x"}], "m", "Dev", box
        )
        assert result["choices"][0]["message"]["content"] == "plain"
        assert observations == [] and model.requests[0]["tools"] is None
    finally:
        box.close()


def test_without_a_toolbox_the_call_is_the_plain_one(monkeypatch):
    class _Plain:
        def chat(self, messages, model, role, bu_id=None):
            return _answer("plain")

    result, observations = mcp_tools.chat_with_tools(_Plain(), [], "m", "Dev", None)
    assert result["choices"][0]["message"]["content"] == "plain" and observations == []


def test_summary_counts_calls_per_tool_and_errors():
    calls = [
        {"server": "t", "tool": "a", "is_error": False},
        {"server": "t", "tool": "a", "is_error": True},
        {"server": "u", "tool": "b", "is_error": False},
    ]
    assert (
        mcp_tools.summarize(calls)
        == "3 MCP tool call(s): t.a ×2, u.b; 1 returned an error"
    )
    assert mcp_tools.summarize([]) == "no MCP tool calls"


def test_the_verifier_records_the_calls_and_a_writing_tool_raises_the_tier():
    run = AgentRun(
        company_id=1,
        external_ref="x",
        producer="p",
        artifact_digest="d",
        artifact_size=1,
        claims={"files": ["src/a.py"]},
    )
    calls = [
        {"server": "t", "tool": "search", "is_error": False, "read_only": True},
        {"server": "t", "tool": "create", "is_error": False, "read_only": False},
        {"server": "t", "tool": "delete", "is_error": True, "read_only": False},
    ]
    used = check_tools_used({"tool_calls": calls})
    assert used["outcome"] == "pass" and used["calls"] == calls
    assert used["observation"].startswith("3 MCP tool call(s)")
    tier = check_risk_tier(run, {"tool_calls": calls})
    assert tier["outcome"] == "pass" and tier["tier"] == "medium"
    assert tier["reasons"] == ["MCP tools without a read-only annotation ran: t.create"]
    read_only = check_risk_tier(run, {"tool_calls": calls[:1]})
    assert read_only["tier"] == "low" and read_only["reasons"] == []
    high = risk.raise_for_tool_calls(
        {"tier": "high", "reasons": ["authentication: app/auth.py"]}, calls
    )
    assert high["tier"] == "high" and len(high["reasons"]) == 2


def test_the_loader_takes_the_company_s_enabled_servers_for_the_role(sqlite_session):
    company = Company(company_name="MCP Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    rows = mcp_config.upsert_servers(
        sqlite_session,
        company.id,
        mcp_config.parse_mcp_servers(
            {
                "mcpServers": {
                    "docs": {"url": "https://docs.example/mcp"},
                    "qa-only": {"url": "https://qa.example/mcp", "roles": ["QA"]},
                    "off": {"url": "https://off.example/mcp", "enabled": False},
                }
            }
        ),
        "manager@example.com",
    )
    assert [r.name for r in rows] == ["docs", "qa-only", "off"]
    for_dev = mcp_config.servers_for_role(sqlite_session, company.id, "Developer")
    assert [r.name for r in for_dev] == ["docs"]
    for_qa = mcp_config.servers_for_role(sqlite_session, company.id, "qa")
    assert [r.name for r in for_qa] == ["docs", "qa-only"]
    box = mcp_tools.Toolbox.load(
        company.id, "Developer", engine=sqlite_session.get_bind()
    )
    assert box is not None and list(box.specs) == ["docs"]
    assert (
        mcp_tools.Toolbox.load(
            company.id + 999, "Developer", engine=sqlite_session.get_bind()
        )
        is None
    )
    assert mcp_tools.Toolbox.load(None, "Developer") is None
    assert isinstance(rows[0], McpServer)


def test_the_worker_records_the_calls_as_evidence_beside_the_claims(
    monkeypatch, sqlite_session
):
    """``_execute_task`` keeps the observations on the agent, and the run's
    verification carries them as ``tools_used`` apart from the claims."""
    box = mcp_tools.Toolbox(
        [{"name": "tracker", "transport": "inproc", "server": _server()}]
    )
    monkeypatch.setattr(
        mcp_tools.Toolbox, "load", classmethod(lambda cls, *a, **k: box)
    )
    model = _Model(
        [
            _answer_with_calls(_call("tracker__search_issues", query="login")),
            _answer("```python path=src/login.py\nprint('hi')\n```\nDone."),
        ]
    )
    monkeypatch.setattr(worker_execute, "LLMProxyClient", lambda base_url=None: model)
    agent = {"role": "Developer", "model": "m"}
    content = worker_execute._execute_task(
        {"id": 7, "title": "Login", "description": "x"}, agent, "S1"
    )
    assert "Done." in content
    assert agent["tool_calls"][0]["tool"] == "search_issues"
    assert (
        model.requests[0]["tools"]
        and "Login" in model.requests[0]["messages"][1]["content"]
    )

    company = Company(company_name="Evidence Co")
    sqlite_session.add(company)
    sqlite_session.commit()
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    recorded = worker_record._record_task_run(
        {"id": 7, "title": "Login"},
        agent,
        {"id": 1, "company_id": company.id},
        content,
        [{"path": "src/login.py", "content": "print('hi')\n"}],
        None,
    )
    assert recorded is not None
    verification = worker_record._latest_task_verification(company.id, 7)
    names = [c["name"] for c in verification.checks]
    assert "tools_used" in names
    used = next(c for c in verification.checks if c["name"] == "tools_used")
    assert (
        used["calls"][0]["tool"] == "search_issues"
        and used["calls"][0]["read_only"] is True
    )
    assert verification.evidence["tool_calls"] == agent["tool_calls"]
    run = sqlite_session.get(AgentRun, recorded[0])
    assert "tool_calls" not in (run.claims or {})
