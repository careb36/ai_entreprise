"""The repository map (issue #359, A.6): the daemon's ``workspace.map``
(``vscode-daemon/repo_map.py``) against real files in ``tmp_path``, and the
prompt section the backend builds from it (``services/repo_map.py``) for the
Developer, QA and the Architect.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
import repo_map as daemon_map  # the daemon's module, on sys.path via conftest
import workspace_read
from config import settings
from services import developer_tools
from services import repo_map as prompt_map
from services import task_worker as tw

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_map", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def map_state(tmp_path, monkeypatch):
    state = tmp_path / "map-state"
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(state))
    return state


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    for folder in ("src", "tests", "node_modules/lib", "web", ".git"):
        (root / folder).mkdir(parents=True)
    (root / "src" / "store.py").write_text(
        "class Store(dict):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n\n"
        "    def get_item(self, key: str) -> str:\n"
        "        return self[key]\n\n\n"
        "def rarely_used_helper(x, *, flag=False):\n"
        "    return x\n\n\n"
        "def _private():\n"
        "    return 1\n"
    )
    (root / "src" / "api.py").write_text(
        "from src.store import Store, rarely_used_helper\n\n\n"
        "def handler(request) -> dict:\n"
        "    store = Store()\n"
        "    return {'v': store.get_item(request)}\n"
    )
    (root / "src" / "cli.py").write_text(
        "from src.store import Store\nfrom src.api import handler\n\n\n"
        "def main():\n    return handler(Store())\n"
    )
    (root / "src" / "broken.py").write_text("def broken(:\n")
    (root / "src" / "secrets.py").write_text("def leaked_secret_holder():\n    pass\n")
    (root / "tests" / "test_store.py").write_text(
        "from src.store import Store\n\n\ndef make_store():\n    return Store()\n\n\n"
        "def test_it():\n    assert make_store() is not None\n"
    )
    (root / "node_modules" / "lib" / "index.js").write_text(
        "export function dependencyFunction() {}\n"
    )
    (root / "web" / "util.ts").write_text(
        "export interface Props { name: string }\n"
        "export type Id = string;\n"
        "export enum Color { Red }\n"
        "export async function fetchUser(id: Id, retries = 2): Promise<Props> {\n"
        "  return { name: id };\n}\n"
        "export const formatName = (props: Props) => props.name;\n"
        "export default class Cache extends Base {\n"
        "  lookup(key) {\n    return key;\n  }\n"
        "  if (x) {\n  }\n"
        "}\n"
    )
    (root / "web" / "app.tsx").write_text(
        "import { fetchUser, formatName, Props } from './util';\n"
        "export function App(props: Props) {\n"
        "  fetchUser('a'); return formatName(props);\n}\n"
    )
    (root / ".git" / "config").write_text("[core]\n")
    return root


# --- the daemon's map ---------------------------------------------------------


def test_the_map_ranks_the_definitions_other_files_use_first(project):
    result = daemon_map.build_map(str(project), 3000)
    text = result["map"]
    assert result["cached"] is False and result["partial"] is False
    # Store is used by api, cli and the test: ahead of the helper used once,
    # which is ahead of the private function nobody else names.
    assert text.index("class Store(dict)") < text.index("def rarely_used_helper")
    assert "    1| class Store(dict)  [used in 3 files]" in text
    assert "5|     def get_item(self, key: str) -> str" in text
    assert "def rarely_used_helper(x, *, flag=False)  [used in 1 file]" in text
    # Dunder methods say nothing; the test file's helper sits below the code's.
    assert "__init__" not in text
    assert text.index("def handler") < text.index("def make_store")
    # Files a listing skips, files that may hold secrets and VCS data are never read.
    assert "dependencyFunction" not in text and "leaked_secret_holder" not in text
    assert result["files_unparsed"] == ["src/broken.py"]
    # Files open with their path, definitions by line.
    store_block = text[text.index("src/store.py:") :].split("\n")[1:4]
    assert [row.split("|")[0].strip() for row in store_block][:2] == ["1", "5"]


def test_javascript_and_typescript_declarations_are_read_conservatively(project):
    parsed = daemon_map.parse_javascript((project / "web" / "util.ts").read_text())
    by_name = {name: (sig, line, depth) for name, sig, line, depth in parsed["defs"]}
    assert by_name["Props"][0] == "interface Props"
    assert by_name["Id"][0] == "type Id"
    assert by_name["Color"][0] == "enum Color"
    assert by_name["fetchUser"][0] == "function fetchUser(id: Id, retries = 2)"
    assert by_name["formatName"][0] == "function formatName(props: Props)"
    assert by_name["Cache"][0] == "class Cache extends Base"
    assert by_name["lookup"] == ("lookup(key)", 9, 1)
    assert "if" not in by_name
    # A declaration's own name is not a reference to it.
    assert "fetchUser" not in parsed["refs"]
    text = daemon_map.build_map(str(project), 3000)["map"]
    assert "function fetchUser(id: Id, retries = 2)  [used in 1 file]" in text


def test_the_map_keeps_to_its_budget_and_says_what_it_left_out(project):
    result = daemon_map.build_map(str(project), 200)
    assert len(result["map"]) <= 200
    assert result["truncated"] is True and result["shown"] < result["definitions"]
    assert "class Store" in result["map"]  # the best-ranked goes first


def test_the_map_is_cached_per_file_set_and_follows_a_change(project, monkeypatch):
    first = daemon_map.build_map(str(project), 3000)
    again = daemon_map.build_map(str(project), 3000)
    assert again["cached"] is True and again["fingerprint"] == first["fingerprint"]
    assert again["map"] == first["map"]
    parsed = []
    real = daemon_map.parse_file
    monkeypatch.setattr(
        daemon_map,
        "parse_file",
        lambda rel, text: parsed.append(rel) or real(rel, text),
    )
    (project / "src" / "api.py").write_text(
        (project / "src" / "api.py").read_text() + "\n\ndef new_endpoint():\n    pass\n"
    )
    changed = daemon_map.build_map(str(project), 3000)
    assert changed["cached"] is False and changed["fingerprint"] != first["fingerprint"]
    assert "def new_endpoint()" in changed["map"]
    # Only the changed file was parsed again.
    assert parsed == ["src/api.py"]


def test_the_map_is_the_same_for_the_same_files(project, tmp_path, monkeypatch):
    first = daemon_map.build_map(str(project), 3000)
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(tmp_path / "other-state"))
    second = daemon_map.build_map(str(project), 3000)
    assert second["cached"] is False and second["map"] == first["map"]


def test_the_map_never_leaves_the_project(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "far.py").write_text("def far_away_function():\n    pass\n")
    (project / "src" / "link.py").symlink_to(outside / "far.py")
    (project / "src" / "linked_dir").symlink_to(outside, target_is_directory=True)
    assert "far_away_function" not in daemon_map.build_map(str(project), 3000)["map"]
    with pytest.raises(workspace_read.ReadError) as missing:
        daemon_map.build_map(str(tmp_path / "nope"), 3000)
    assert missing.value.status == 404


def test_the_daemon_answers_workspace_map_over_nats(project):
    daemon = _load_daemon()
    assert ("workspace.map", daemon_map.handle_map) in daemon.repo_map.HANDLERS
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    async def run():
        for body in ({"workspace_root": str(project), "max_chars": 1000}, {}):
            await daemon_map.handle_map(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert "class Store" in replies[0]["map"] and replies[0]["max_chars"] == 1000
    assert replies[1]["error"]["status"] == 400


# --- the prompt section ---------------------------------------------------------


class _Requester:
    def __init__(self, daemon, with_map=True):
        self.daemon = daemon
        self.with_map = with_map
        self.subjects = []

    def __call__(self, subject, payload, timeout):
        self.subjects.append(subject)
        root = payload["workspace_root"]
        if subject == "workspace.list":
            return workspace_read.list_tree(
                root,
                payload.get("path"),
                payload.get("depth"),
                self.daemon._resolve_target_path,
            )
        if subject == "workspace.map" and self.with_map:
            return daemon_map.build_map(root, payload.get("max_chars"))
        raise developer_tools.DaemonReplyError("no responders")


def test_the_developer_and_the_architect_get_the_map_beside_the_tree(
    project, monkeypatch
):
    daemon = _load_daemon()
    requester = _Requester(daemon)
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", requester)
    try:
        dev = developer_tools.repository_section(1, "Developer")
        assert dev.startswith("\n\n## Repository\n") and "src/store.py" in dev
        assert "### Definitions, most used first" in dev and "class Store(dict)" in dev
        assert "workspace tools" in dev
        architect = developer_tools.repository_section(1, "Architect")
        assert "class Store(dict)" in architect and "workspace tools" not in architect
        assert developer_tools.repository_section(1, "ScrumMaster") == ""
        # The budget travels to the daemon; 0 leaves the definitions out.
        monkeypatch.setattr(settings, "REPO_MAP_MAX_CHARS", 300)
        cut = developer_tools.repository_section(1, "Developer")
        assert "definitions shown; search for the rest]" in cut
        monkeypatch.setattr(settings, "REPO_MAP_MAX_CHARS", 0)
        assert "### Definitions" not in developer_tools.repository_section(1, "QA")
        assert developer_tools.repository_section(1, "Architect") == ""
        # A daemon without workspace.map: the tree alone, as before.
        monkeypatch.setattr(settings, "REPO_MAP_MAX_CHARS", 3000)
        monkeypatch.setattr(
            developer_tools, "_request_from_thread", _Requester(daemon, with_map=False)
        )
        older = developer_tools.repository_section(1, "Developer")
        assert "src/store.py" in older and "### Definitions" not in older
    finally:
        developer_tools.unbind()


def test_the_architects_brief_carries_the_map(project, monkeypatch, sqlite_session):
    from models.company import Agent, Company
    from models.sprint import BacklogItem, Sprint

    daemon = _load_daemon()
    monkeypatch.setattr(tw, "engine", sqlite_session.get_bind())
    company = Company(company_name="Map Co", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    sqlite_session.add(
        Agent(company_id=company.id, name="Archie", role="Architect", model="m")
    )
    sprint = Sprint(
        company_id=company.id, sprint_name="S", scope_size="M", status="active"
    )
    sqlite_session.add(sprint)
    sqlite_session.commit()
    sqlite_session.refresh(sprint)
    item = BacklogItem(
        sprint_id=sprint.id, title="Add caching", description="d", status="todo"
    )
    sqlite_session.add(item)
    sqlite_session.commit()
    sqlite_session.refresh(item)
    prompts = []

    class _Client:
        def __init__(self, base_url=None):
            pass

        def chat(self, messages, model, role, bu_id=None):
            prompts.append(messages[1]["content"])
            return {"choices": [{"message": {"content": "Use Store."}}]}

    monkeypatch.setattr(tw, "LLMProxyClient", _Client)
    monkeypatch.setattr(tw, "record_agent_turn", lambda **kw: None)
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    monkeypatch.setattr(developer_tools, "_request_from_thread", _Requester(daemon))
    try:
        advice = tw._consult_architect(
            {"id": item.id, "title": item.title},
            {"role": "Developer"},
            {"id": sprint.id, "company_id": company.id, "name": "S"},
        )
    finally:
        developer_tools.unbind()
    assert advice == "Use Store."
    assert "## Repository" in prompts[0] and "class Store(dict)" in prompts[0]


def test_the_architecture_draft_reads_the_map_through_the_backends_nats(project):
    daemon = _load_daemon()
    requester = _Requester(daemon)

    class _Nats:
        async def request(self, subject, payload, timeout):
            return requester(subject, payload, timeout)

    section = asyncio.run(prompt_map.fetch_section(_Nats(), str(project)))
    assert "class Store(dict)" in section and "workspace tools" not in section
    assert asyncio.run(prompt_map.fetch_section(None, str(project))) == ""
    assert asyncio.run(prompt_map.fetch_section(_Nats(), None)) == ""
