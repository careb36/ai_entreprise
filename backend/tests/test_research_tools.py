"""The research tools (``services/research_tools.py``): the roles that decide
read official sources through the backend, every source is an observation
(url, when, status, sha256, bytes), a host off the company's list is refused
and the refusal recorded, a Manager adds hosts, the PO's and the Architect's
drafts keep the sources they consulted, and the Verifier's ``sources`` check
names the dependencies a delivery named without a registry lookup."""

from __future__ import annotations

import hashlib
import json

import pytest
from config import settings
from models.company import Company
from models.specification import Specification
from services import developer_tools, mcp_config, research_tools, spec_author
from test_developer_tools import _answer, _answer_with_calls
from test_git_workflow import _member, enforcement  # noqa: F401
from test_specifications import GOOD, _company

PAGE = b"<html><head><style>p{}</style><script>x()</script></head><body><h1>Pytest 9.0</h1><p>Released &amp; stable.</p></body></html>"


def _tc(tool, **arguments):
    return {
        "id": f"call_{tool}",
        "type": "function",
        "function": {"name": tool, "arguments": json.dumps(arguments)},
    }


class _Http:
    """A fake of ``default_http``: canned answers by URL, every request kept."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, method, url, body=None, timeout=20.0):
        self.requests.append((method, url, body))
        status, headers, data = self.answers.get(url, (404, {}, b"not here"))
        if isinstance(data, dict | list):
            data = json.dumps(data).encode()
            headers = {**headers, "content-type": "application/json"}
        return status, headers, data


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", True)


def _box(answers, hosts=None):
    return research_tools.ResearchToolbox(
        hosts=research_tools.allowed_hosts(hosts or []), http=_Http(answers)
    )


def test_a_page_is_read_as_text_and_recorded_with_its_hash(enabled):
    url = "https://docs.pytest.org/en/stable/changelog.html"
    box = _box({url: (200, {"content-type": "text/html; charset=utf-8"}, PAGE)})
    observation, text = box.call(_tc("research__fetch", url=url), 1)
    assert text == "Pytest 9.0\n\nReleased & stable."
    assert observation["server"] == "research" and observation["tool"] == "fetch"
    assert observation["url"] == url and observation["host"] == "docs.pytest.org"
    assert observation["allowed"] is True and observation["status"] == 200
    assert observation["sha256"] == hashlib.sha256(PAGE).hexdigest()
    assert observation["bytes"] == len(PAGE) and observation["read_only"] is True
    assert observation["fetched_at"].endswith("+00:00") and not observation["is_error"]
    assert observation["content_type"] == "text/html"


def test_a_host_off_the_list_is_refused_and_the_refusal_recorded(enabled):
    box = _box({})
    observation, text = box.call(
        _tc("research__fetch", url="https://pastebin.example/secret"), 1
    )
    assert observation["is_error"] and observation["allowed"] is False
    assert observation["host"] == "pastebin.example" and observation["status"] is None
    assert "not on the company's list" in text and "ask a Manager" in text
    assert box.http.requests == []
    # A redirect that leaves the list is refused where it lands.
    start = "https://docs.python.org/3/library/secrets.html"
    box = _box({start: (302, {"location": "https://evil.example/x"}, b"")})
    observation, text = box.call(_tc("research__fetch", url=start), 1)
    assert observation["allowed"] is False and observation["host"] == "evil.example"
    assert len(box.http.requests) == 1
    # Plain http only for a host the company or the deployment named itself.
    hosts = research_tools.allowed_hosts(["mirror.local"])
    assert research_tools.host_allowed("http://mirror.local/x", hosts) == (
        True,
        "mirror.local",
    )
    assert research_tools.host_allowed("http://pypi.org/x", hosts) == (
        False,
        "pypi.org",
    )
    assert research_tools.host_allowed("https://sub.pypi.org/x", hosts)[0] is True
    assert research_tools.host_allowed("ftp://pypi.org/x", hosts)[0] is False
    assert research_tools.host_allowed("https://notpypi.org/x", hosts)[0] is False


def test_an_error_page_and_a_binary_are_errors_the_model_sees(enabled):
    gone = "https://docs.python.org/gone"
    binary = "https://docs.python.org/a.pdf"
    box = _box(
        {
            gone: (404, {"content-type": "text/html"}, b"no"),
            binary: (200, {"content-type": "application/pdf"}, b"%PDF"),
        }
    )
    observation, text = box.call(_tc("research__fetch", url=gone), 1)
    assert observation["is_error"] and observation["status"] == 404
    assert text == "Error: The page answered 404."
    observation, text = box.call(_tc("research__fetch", url=binary), 1)
    assert observation["is_error"] and "not text" in text
    observation, text = box.call(_tc("research__fetch", url=""), 1)
    assert observation["is_error"] and "URL is required" in text
    observation, text = box.call(
        {"function": {"name": "research__fetch", "arguments": "{not json"}}, 1
    )
    assert observation["is_error"] and observation["reason"].startswith("malformed")


def test_a_package_is_looked_up_at_its_registry(enabled):
    pypi = {
        "info": {
            "name": "fastapi",
            "version": "0.120.0",
            "summary": "Web",
            "requires_python": ">=3.9",
        },
        "releases": {
            "0.119.0": [{"upload_time_iso_8601": "2026-09-01T00:00:00Z"}],
            "0.120.0": [
                {"upload_time_iso_8601": "2026-10-20T00:00:00Z", "yanked": False}
            ],
            "0.1.0": [],
        },
    }
    npm = {
        "name": "vite",
        "dist-tags": {"latest": "7.1.0"},
        "time": {
            "created": "2020",
            "modified": "2026",
            "7.0.0": "2026-06-01T00:00:00Z",
            "7.1.0": "2026-10-01T00:00:00Z",
        },
        "description": "Build tool",
        "versions": {"7.1.0": {"deprecated": False}},
    }
    crates = {
        "crate": {
            "name": "serde",
            "max_stable_version": "1.0.220",
            "description": "Ser",
        },
        "versions": [
            {"num": "1.0.220", "created_at": "2026-10-02T00:00:00Z"},
            {"num": "1.0.219", "created_at": "2026-09-02T00:00:00Z"},
        ],
    }
    go = {"Version": "v1.9.1", "Time": "2026-08-01T00:00:00Z"}
    box = _box(
        {
            "https://pypi.org/pypi/fastapi/json": (200, {}, pypi),
            "https://registry.npmjs.org/vite": (200, {}, npm),
            "https://crates.io/api/v1/crates/serde": (200, {}, crates),
            "https://proxy.golang.org/github.com/gin-gonic/gin/@latest": (200, {}, go),
        }
    )
    observation, text = box.call(
        _tc("research__package", name="fastapi", ecosystem="pypi"), 1
    )
    record = json.loads(text)
    assert record["latest"] == "0.120.0" and record["released"].startswith("2026-10-20")
    assert record["versions"] == ["0.120.0", "0.119.0"]
    assert observation["package"] == "fastapi" and observation["ecosystem"] == "pypi"
    assert observation["url"] == "https://pypi.org/pypi/fastapi/json"
    assert observation["sha256"] and not observation["is_error"]
    record = json.loads(
        box.call(_tc("research__package", name="vite", ecosystem="npm"), 1)[1]
    )
    assert record["latest"] == "7.1.0" and record["versions"] == ["7.1.0", "7.0.0"]
    record = json.loads(
        box.call(_tc("research__package", name="serde", ecosystem="crates"), 1)[1]
    )
    assert record["latest"] == "1.0.220" and record["released"].startswith("2026-10-02")
    record = json.loads(
        box.call(
            _tc("research__package", name="github.com/gin-gonic/gin", ecosystem="go"),
            1,
        )[1]
    )
    assert record["latest"] == "v1.9.1"
    observation, text = box.call(
        _tc("research__package", name="nope", ecosystem="pypi"), 1
    )
    assert observation["is_error"] and "no package named 'nope'" in text
    observation, text = box.call(_tc("research__package", name="x", ecosystem="gem"), 1)
    assert observation["is_error"] and "ecosystem must be one of" in text


def test_advisories_come_from_osv_with_the_version_asked(enabled):
    osv = {
        "vulns": [
            {
                "id": "GHSA-1",
                "summary": "Header injection",
                "published": "2026-03-01T00:00:00Z",
                "database_specific": {"severity": "HIGH"},
                "affected": [
                    {
                        "package": {"name": "requests"},
                        "ranges": [
                            {"events": [{"introduced": "0"}, {"fixed": "2.32.4"}]}
                        ],
                    }
                ],
            }
        ]
    }
    box = _box({"https://api.osv.dev/v1/query": (200, {}, osv)})
    observation, text = box.call(
        _tc(
            "research__advisories", name="requests", ecosystem="pypi", version="2.31.0"
        ),
        1,
    )
    assert box.http.requests == [
        (
            "POST",
            "https://api.osv.dev/v1/query",
            {"package": {"name": "requests", "ecosystem": "PyPI"}, "version": "2.31.0"},
        )
    ]
    record = json.loads(text)
    assert record["count"] == 1 and record["advisories"][0] == {
        "id": "GHSA-1",
        "summary": "Header injection",
        "severity": "HIGH",
        "fixed": ["2.32.4"],
        "published": "2026-03-01T00:00:00Z",
    }
    assert observation["advisories"] == 1 and observation["version"] == "2.31.0"


def test_the_tools_go_to_the_roles_that_decide_and_the_prompt_says_why(
    enabled, monkeypatch
):
    assert research_tools.offered_to("Architect") and research_tools.offered_to("po")
    assert research_tools.offered_to("SecOps") and research_tools.offered_to(
        "TechWriter"
    )
    assert not research_tools.offered_to("Developer") and not research_tools.offered_to(
        None
    )
    monkeypatch.setattr(settings, "RESEARCH_ROLES", "Developer")
    assert research_tools.offered_to("Developer") and not research_tools.offered_to(
        "PO"
    )
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", False)
    assert not research_tools.offered_to("Developer")
    assert research_tools.prompt_section("Developer") == ""
    monkeypatch.setattr(settings, "RESEARCH_TOOLS_ENABLED", True)
    section = research_tools.prompt_section(
        "Developer", {"python": "3.12", "fastapi": "0.120.0"}
    )
    assert "## Sources" in section and "cut-off date" in section
    assert "- fastapi: 0.120.0" in section and "- python: 3.12" in section
    # ``research`` is a reserved MCP server name, like ``workspace``.
    with pytest.raises(mcp_config.McpConfigError, match="reserved"):
        mcp_config.normalize_server("research", {"url": "https://x.example/mcp"})
    # The combined toolbox offers them without a workspace root.
    box = developer_tools.toolbox_for(None, "Developer")
    assert [s["function"]["name"] for s in box.schemas()] == [
        "research__fetch",
        "research__package",
        "research__advisories",
    ]
    assert box.max_rounds == settings.RESEARCH_TOOL_ROUNDS and box.unavailable == {}
    box.close()
    assert developer_tools.toolbox_for(None, "Architect") is None


def test_the_sources_check_names_the_dependencies_nobody_looked_up(enabled):
    files = [
        {
            "path": "requirements.txt",
            "content": "fastapi==0.120.0\nhttpx>=0.27 # client\n-e .\n",
        },
        {
            "path": "web/package.json",
            "content": json.dumps(
                {"dependencies": {"vite": "^7"}, "devDependencies": {"vitest": "^3"}}
            ),
        },
        {
            "path": "pyproject.toml",
            "content": 'dependencies = [\n  "pydantic>=2",\n]\n',
        },
    ]
    assert research_tools.check_sources(None, files) is None
    assert research_tools.check_sources({"workspace_writes": []}, files) is None
    calls = [
        {
            "server": "research",
            "tool": "package",
            "url": "https://pypi.org/pypi/fastapi/json",
            "host": "pypi.org",
            "allowed": True,
            "fetched_at": "t",
            "status": 200,
            "sha256": "h",
            "bytes": 3,
            "package": "fastapi",
            "ecosystem": "pypi",
            "is_error": False,
        },
        {
            "server": "research",
            "tool": "fetch",
            "url": "https://evil.example/x",
            "host": "evil.example",
            "allowed": False,
            "fetched_at": "t",
            "status": None,
            "is_error": True,
        },
        {"server": "workspace", "tool": "read", "path": "README.md"},
    ]
    check = research_tools.check_sources({"tool_calls": calls}, files)
    assert check["name"] == "sources" and check["outcome"] == "pass"
    assert check["unchecked_dependencies"] == [
        "npm:vite",
        "npm:vitest",
        "pypi:httpx",
        "pypi:pydantic",
    ]
    assert check["observation"] == (
        "1 source(s) consulted; 1 refused (host not allowed); "
        "dependencies named without a registry lookup: npm:vite, npm:vitest, pypi:httpx, pypi:pydantic"
    )
    assert [s["url"] for s in check["sources"]] == [
        "https://pypi.org/pypi/fastapi/json",
        "https://evil.example/x",
    ]
    assert "path" not in json.dumps(check["sources"])
    assert research_tools.check_sources({"tool_calls": []}, []) is None
    assert research_tools.check_sources({"tool_calls": []}, files)[
        "observation"
    ].startswith("0 source(s) consulted; dependencies named")


@pytest.mark.usefixtures("enforcement")
def test_a_manager_adds_hosts_and_a_viewer_reads_them(sqlite_session, test_client):
    company, other = _company(sqlite_session), _company(sqlite_session)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Manager")
    url = f"/v1/companies/{company.id}/research"
    read = test_client.get(url, headers=viewer)
    assert read.status_code == 200, read.text
    assert read.json()["hosts"] == [] and "pypi.org" in read.json()["default_hosts"]
    assert read.json()["roles"] == ["architect", "po", "secops", "techwriter"]
    assert test_client.get(url, headers=outsider).status_code == 403
    assert (
        test_client.put(
            url, json={"hosts": ["docs.acme.example"]}, headers=viewer
        ).status_code
        == 403
    )
    bad = test_client.put(
        url, json={"hosts": ["https://docs.acme.example/x"]}, headers=manager
    )
    assert bad.status_code == 422 and "not a host name" in bad.text
    saved = test_client.put(
        url,
        json={"hosts": [" Docs.Acme.example ", "docs.acme.example", "mirror.local"]},
        headers=manager,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["hosts"] == ["docs.acme.example", "mirror.local"]
    sqlite_session.expire_all()
    assert sqlite_session.get(Company, company.id).research_hosts == [
        "docs.acme.example",
        "mirror.local",
    ]
    assert research_tools.company_hosts(sqlite_session, company.id) == [
        "docs.acme.example",
        "mirror.local",
    ]
    assert "docs.acme.example" in research_tools.allowed_hosts(
        research_tools.company_hosts(sqlite_session, company.id)
    )


@pytest.mark.usefixtures("enforcement", "enabled")
def test_the_po_consults_a_source_and_the_draft_keeps_it(
    sqlite_session, test_client, monkeypatch
):
    company = _company(sqlite_session)
    company.research_hosts = ["docs.acme.example"]
    sqlite_session.add(company)
    sqlite_session.commit()
    manager = _member(sqlite_session, company.id, "Manager")
    page = "https://docs.acme.example/api.html"
    http = _Http(
        {page: (200, {"content-type": "text/html"}, b"<p>Rate limit: 100/min</p>")}
    )
    monkeypatch.setattr(research_tools, "default_http", http)

    class _Client:
        def __init__(self):
            self.rounds = []

        def chat(self, messages, model, role, bu_id=None, tools=None):
            self.rounds.append((list(messages), tools, bu_id))
            if len(self.rounds) == 1:
                return _answer_with_calls(_tc("research__fetch", url=page))
            return _answer(json.dumps({**GOOD, "questions": []}))

    client = _Client()
    monkeypatch.setattr(
        spec_author, "_client", lambda timeout, max_tokens, company_id=None: client
    )
    drafted = test_client.post(
        "/v1/specifications/draft",
        json={"company_id": company.id, "brief": "A rate-limited API for the e2e."},
        headers=manager,
    )
    assert drafted.status_code == 200, drafted.text
    spec = drafted.json()
    assert len(client.rounds) == 2
    first_prompt = client.rounds[0][0][-1]["content"]
    assert "## Sources" in first_prompt and "cut-off date" in first_prompt
    assert [t["function"]["name"] for t in client.rounds[0][1]] == [
        "research__fetch",
        "research__package",
        "research__advisories",
    ]
    assert (
        client.rounds[1][0][-1]["role"] == "tool"
        and "Rate limit: 100/min" in client.rounds[1][0][-1]["content"]
    )
    assert len(spec["sources"]) == 1
    source = spec["sources"][0]
    assert (
        source["url"] == page and source["allowed"] is True and source["status"] == 200
    )
    assert source["sha256"] == hashlib.sha256(b"<p>Rate limit: 100/min</p>").hexdigest()
    assert "arguments" not in source and "result_sha256" not in source
    stored = sqlite_session.get(Specification, spec["id"])
    assert stored.sources[0]["url"] == page and stored.drafted_by == "PO"
    # A person's own draft keeps no sources; the next agent draft records anew.
    assert (
        test_client.get(f"/v1/specifications/{spec['id']}", headers=manager).json()[
            "sources"
        ]
        == spec["sources"]
    )
