"""``workspace__definition`` and ``workspace__references`` (issue #766).

The daemon's ``workspace.related`` with ``lookup`` (``vscode-daemon/
code_lookup.py``) runs for real against a project in ``tmp_path``, through a
synchronous NATS fake that validates each request and reply against the
subject's schema. The tools are offered only when every daemon announces the
``lookup`` feature; a path outside the project is refused before the daemon
is asked, and again by the daemon.
"""

from __future__ import annotations

import json
from pathlib import Path

import abilities  # the daemon's modules, on sys.path via conftest
import code_graph as daemon_graph
import pytest
from jsonschema import Draft202012Validator
from services.tools import developer_tools, review_tools, workspace_navigation
from services.workspace import daemon_liveness
from workspace_read import ReadError

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "vscode-daemon/protocol/schemas/v1/workspace.related.json"
    ).read_text()
)
MARKER = "contents-that-must-never-leave-the-machine"


def _validator(part: str) -> Draft202012Validator:
    return Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{part}"})


@pytest.fixture(autouse=True)
def map_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(tmp_path / "map-state"))


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "pkg" / "db.py").write_text(
        f"# {MARKER}\n\n\ndef connect_database(url, *, timeout=5):\n    return url\n"
    )
    (root / "pkg" / "store.py").write_text(
        "from pkg.db import connect_database\n\n\n"
        "class Store:\n"
        "    def __init__(self, url):\n"
        "        self.conn = connect_database(url)\n\n"
        "    def get_item(self, key):\n"
        "        return key\n"
    )
    (root / "pkg" / "api.py").write_text(
        "from pkg.store import Store\n\n\n"
        "def handle_request(key):\n"
        "    store = Store('db://')\n"
        "    return store.get_item(key)\n"
    )
    # A file that may hold secrets is never read, nor named.
    (root / "pkg" / "secrets.py").write_text("from pkg.store import Store\n")
    (root / "web" / "util.ts").write_text(
        "export function formatName(name: string) {\n  return name;\n}\n"
    )
    (root / "web" / "app.tsx").write_text(
        "import { formatName } from './util';\n"
        "export function App() {\n  return formatName('a');\n}\n"
    )
    return root


class _Daemon:
    """NATS, synchronously: the daemon's ``workspace.related`` for real."""

    def __init__(self, old: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.old = old

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        assert subject == "workspace.related"
        _validator("request").validate(payload)
        data = {k: v for k, v in payload.items() if not (self.old and k == "lookup")}
        try:
            reply = daemon_graph.related(payload["workspace_root"], data)
        except ReadError as exc:
            raise developer_tools.DaemonReplyError(exc.message) from exc
        _validator("reply").validate(reply)
        return reply


@pytest.fixture()
def announced():
    daemon_liveness.reset()
    daemon_liveness.observe_heartbeat(
        {"daemon_id": "d1", "abilities": abilities.announcement()}
    )
    yield
    daemon_liveness.reset()


def _box(project, daemon=None):
    return developer_tools.WorkspaceToolbox(daemon or _Daemon(), str(project))


def _ask(box, tool, **arguments):
    call = {
        "id": f"c_{tool}",
        "type": "function",
        "function": {"name": f"workspace__{tool}", "arguments": json.dumps(arguments)},
    }
    return box.call(call, 1)


# --- the tools against the real daemon ----------------------------------------


def test_a_definition_names_the_file_line_signature_and_sha256(project, announced):
    box = _box(project)
    assert box.tools["workspace__definition"]["read_only"] is True
    obs, text = _ask(box, "definition", name="connect_database")
    assert not obs["is_error"] and obs["read_only"] is True
    assert obs["lookup"] == {"kind": "definition", "name": "connect_database"}
    [row] = obs["files"]
    assert row["path"] == "pkg/db.py" and row["line"] == 4
    assert len(row["sha256"]) == 64
    assert "pkg/db.py:4" in text and "def connect_database(url, *, timeout=5)" in text
    assert "parameters: (url, timeout=…)" in text
    assert len(obs["result_sha256"]) == 64
    # A method, and a TypeScript function, are found too.
    _, text = _ask(box, "definition", name="get_item")
    assert "pkg/store.py:8 (a method)" in text
    _, text = _ask(box, "definition", name="formatName")
    assert "web/util.ts:1" in text
    # Never the contents of a file, nor a file that may hold secrets.
    for subject, payload in box.requester.calls:
        assert payload["seeds"] == [] and payload["lookup"]["kind"]
    _, text = _ask(box, "definition", name="nowhere_defined")
    assert text.startswith("No definition of `nowhere_defined`")
    assert MARKER not in text


def test_references_of_a_name_and_of_a_module_name_the_lines(project, announced):
    box = _box(project)
    obs, text = _ask(box, "references", name="connect_database")
    assert [f["path"] for f in obs["files"]] == ["pkg/store.py"]
    assert "pkg/store.py lines 1, 6: connect_database" in text
    assert "secrets.py" not in text
    obs, text = _ask(box, "references", path="pkg/store.py")
    assert [f["path"] for f in obs["files"]] == ["pkg/api.py"]
    assert "pkg/api.py lines 1, 5, 6: Store, get_item" in text
    obs, text = _ask(box, "references", path="pkg/store.py", name="get_item")
    assert "pkg/api.py lines 6: get_item" in text
    _, text = _ask(box, "references", path="web/util.ts")
    assert "web/app.tsx lines 1, 3: formatName" in text
    _, text = _ask(box, "references", path="pkg/nothing.py")
    assert "is not a source file" in text


def test_the_reply_is_bounded(project, announced, monkeypatch):
    for n in range(5):
        (project / "pkg" / f"user{n}.py").write_text(
            "from pkg.db import connect_database\n" + "connect_database('x')\n" * 15
        )
    monkeypatch.setattr(workspace_navigation, "MAX_FILES", 3)
    obs, text = _ask(_box(project), "references", name="connect_database")
    assert len(obs["files"]) == 3 and obs["more"] == 3
    assert "[3 more files not shown]" in text
    assert "(+6 more lines)" in text  # ten lines a file at most


@pytest.mark.parametrize(
    "path", ["../outside.py", "/etc/passwd", ".git/config", ".env", "pkg/../../x.py"]
)
def test_a_path_outside_the_project_is_refused(project, announced, path):
    daemon = _Daemon()
    box = _box(project, daemon)
    for tool in ("definition", "references"):
        obs, text = _ask(box, tool, name="Store", path=path)
        assert obs["is_error"] and text.startswith("Error:")
    assert daemon.calls == []  # refused before the daemon was asked
    # The daemon refuses it too.
    if not path.startswith(".") or ".." in path:
        with pytest.raises(ReadError):
            daemon_graph.related(
                str(project),
                {"seeds": [], "lookup": {"kind": "references", "path": path}},
            )


def test_a_name_that_is_not_an_identifier_is_refused(project, announced):
    daemon = _Daemon()
    box = _box(project, daemon)
    for name in ("a b", "x; rm -rf /", "", "1abc"):
        obs, _ = _ask(box, "definition", name=name)
        assert obs["is_error"]
    obs, _ = _ask(box, "references")
    assert obs["is_error"]
    assert daemon.calls == []


def test_a_secret_in_a_signature_is_redacted(project, announced):
    (project / "pkg" / "conf.py").write_text(
        "def configure(password='hunter2hunter2hunter2'):\n    return password\n"
    )
    _, text = _ask(_box(project), "definition", name="configure")
    assert "hunter2" not in text


# --- offered only when announced ---------------------------------------------


def test_nothing_is_offered_until_every_daemon_announces_it(project):
    daemon_liveness.reset()
    try:
        box = _box(project)  # Nothing heard yet (right after a restart).
        assert "workspace__definition" not in box.tools
        assert "workspace__references" not in box.tools
        obs, _ = _ask(box, "definition", name="Store")
        assert obs["is_error"] and obs["reason"] == "unknown tool"
        older = {
            **abilities.announcement(),
            "features": {
                k: v
                for k, v in abilities.announcement()["features"].items()
                if k != "workspace.related"
            },
        }
        daemon_liveness.observe_heartbeat({"daemon_id": "d1", "abilities": older})
        assert "workspace__definition" not in _box(project).tools
        # One of two daemons announces it: still not offered (the queue picks).
        daemon_liveness.observe_heartbeat(
            {"daemon_id": "d2", "abilities": abilities.announcement()}
        )
        assert "workspace__definition" not in _box(project).tools
    finally:
        daemon_liveness.reset()


def test_an_older_daemon_s_empty_walk_is_never_read_as_nothing_found(
    project, announced
):
    box = _box(project, _Daemon(old=True))
    obs, text = _ask(box, "definition", name="Store")
    assert obs["is_error"] and "does not serve code navigation" in text


def test_the_read_only_toolboxes_offer_navigation_but_not_the_lint(
    project, announced, monkeypatch
):
    monkeypatch.setattr(review_tools, "delivery_root", lambda *a, **k: str(project))
    developer_tools.bind(object(), object(), lambda company_id: str(project))
    try:
        for box in (
            developer_tools.read_tools(1),
            review_tools.read_only_toolbox(1, 2, engine=None, rounds=3),
        ):
            assert {"workspace__definition", "workspace__references"} <= set(box.tools)
            assert "workspace__lint" not in box.tools
            assert "workspace__edit" not in box.tools
    finally:
        developer_tools.unbind()


def test_the_daemon_announces_the_lookup_feature():
    assert "lookup" in abilities.FEATURES["workspace.related"]
    assert workspace_navigation.SUBJECT == "workspace.related"
    assert workspace_navigation.FEATURE == "lookup"
