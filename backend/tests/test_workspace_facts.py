"""What the project declares, as tools; and its docs build on demand (issue #766).

``workspace__api_surface``, ``workspace__config_vars`` and
``workspace__dependencies`` ask the daemon's ``workspace.related`` with
``lookup.kind`` set to their name (``vscode-daemon/project_facts.py``), run
for real against a project in ``tmp_path`` through a NATS fake that validates
each request and reply against the subject's schema. They are family R
(every toolbox that reads offers them) and are offered only when every
daemon announces their kind. ``workspace__docs_build`` is family X: offered
beside the lint where runs are allowed, from the stack profile's
``docs_build``, never with a free argument.
"""

from __future__ import annotations

import json
from pathlib import Path

import abilities  # the daemon's modules, on sys.path via conftest
import code_graph as daemon_graph
import pytest
from jsonschema import Draft202012Validator
from services.tools import developer_tools, workspace_facts, workspace_reads
from services.workspace import daemon_liveness
from workspace_read import ReadError

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "vscode-daemon/protocol/schemas/v1/workspace.related.json"
    ).read_text()
)
SECRET = "dotenv-secret-value-0000"


def _validator(part: str) -> Draft202012Validator:
    return Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{part}"})


@pytest.fixture(autouse=True)
def map_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIQUE_MAP_STATE", str(tmp_path / "map-state"))


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    files = {
        "app/main.py": (
            "import os\nfrom fastapi import FastAPI, APIRouter\n\n"
            "app = FastAPI()\nrouter = APIRouter(prefix='/orders')\n"
            "DB = os.getenv('DATABASE_URL', 'sqlite:///local.db')\n"
            "TOKEN = os.environ['API_TOKEN']\n\n"
            "@router.get('/{oid}')\ndef get_order(oid: int):\n    return oid\n\n"
            "@app.post('/login')\nasync def login():\n    return None\n"
        ),
        "docs/openapi.json": json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Orders", "version": "1"},
                "paths": {"/orders/{oid}": {"get": {"operationId": "getOrder"}}},
            }
        ),
        "requirements.txt": "fastapi==0.110.0\n",
        "web/package.json": json.dumps({"devDependencies": {"vite": "^5.4.0"}}),
        ".env": f"API_TOKEN={SECRET}\nDATABASE_URL=postgres://u:{SECRET}@h/db\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


class _Daemon:
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


def _heartbeat(features: list[str] | None = None):
    daemon_liveness.reset()
    announcement = abilities.announcement()
    if features is not None:
        announcement["features"]["workspace.related"] = features
    daemon_liveness.observe_heartbeat({"daemon_id": "d1", "abilities": announcement})


@pytest.fixture()
def announced():
    _heartbeat()
    yield
    daemon_liveness.reset()


def _box(project, daemon=None):
    box = developer_tools.WorkspaceToolbox(daemon or _Daemon(), str(project))
    workspace_reads.offer(box)
    return box


def _ask(box, tool, **arguments):
    call = {
        "id": f"c_{tool}",
        "type": "function",
        "function": {"name": f"workspace__{tool}", "arguments": json.dumps(arguments)},
    }
    return box.call(call, 1)


def test_api_surface_from_fastapi_code_and_a_committed_openapi(project, announced):
    box = _box(project)
    assert box.tools["workspace__api_surface"]["read_only"] is True
    obs, text = _ask(box, "api_surface")
    assert not obs["is_error"] and obs["read_only"] is True
    assert "- GET /orders/{oid}  get_order  app/main.py:9" in text
    assert "- POST /login  login  app/main.py:13" in text
    assert "OpenAPI document docs/openapi.json (3.0.3, Orders 1)" in text
    assert "- GET /orders/{oid}  getOrder" in text
    files = {f["path"]: f["sha256"] for f in obs["files"]}
    assert set(files) == {"app/main.py", "docs/openapi.json"}
    assert all(len(d) == 64 for d in files.values())
    assert obs["files_scanned"] >= 2 and len(obs["scanned_sha256"]) == 64


def test_config_vars_never_return_a_dotenv_value(project, announced):
    obs, text = _ask(_box(project), "config_vars")
    assert (
        "- DATABASE_URL  app/main.py:6  (os.getenv)  default: 'sqlite:///local.db'"
        in text
    )
    assert "- API_TOKEN  app/main.py:7  (os.environ[])  required" in text
    assert SECRET not in text and SECRET not in json.dumps(obs)
    assert [f["path"] for f in obs["files"]] == ["app/main.py"]


def test_dependencies_name_each_manifest_and_point_licences_to_research(
    project, announced
):
    obs, text = _ask(_box(project), "dependencies")
    assert "- fastapi ==0.110.0  [main, pypi]  requirements.txt" in text
    assert "- vite ^5.4.0  [dev, npm]  web/package.json" in text
    assert "a claim of the registry" in text
    assert {f["path"] for f in obs["files"]} == {"requirements.txt", "web/package.json"}


def test_not_offered_unless_every_daemon_announces_the_kind(project):
    _heartbeat(["lookup"])
    try:
        box = _box(project)
        names = set(box.tools)
        assert (
            not {
                "workspace__api_surface",
                "workspace__config_vars",
                "workspace__dependencies",
            }
            & names
        )
        obs, _text = _ask(box, "api_surface")
        assert obs["is_error"] and obs["reason"] == "unknown tool"
        _heartbeat(["lookup", "dependencies"])
        assert {n for n in _box(project).tools if n.startswith("workspace__dep")} == {
            "workspace__dependencies"
        }
        daemon_liveness.reset()  # Nothing heard: nothing offered.
        assert "workspace__dependencies" not in _box(project).tools
    finally:
        daemon_liveness.reset()


def test_an_older_daemon_is_an_error_never_nothing_found(project, announced):
    obs, text = _ask(_box(project, _Daemon(old=True)), "config_vars")
    assert obs["is_error"] and "does not serve config_vars" in text


@pytest.mark.parametrize(
    "path", ["../outside", "/etc", ".git/config", ".env", "app/../../x"]
)
def test_a_path_outside_the_project_is_refused(project, announced, path):
    daemon = _Daemon()
    box = _box(project, daemon)
    for tool in workspace_facts.TOOLS:
        obs, text = _ask(box, tool, path=path)
        assert obs["is_error"] and text.startswith("Error:")
    assert daemon.calls == []
    if not path.startswith("."):
        with pytest.raises(ReadError):
            daemon_graph.related(
                str(project),
                {"seeds": [], "lookup": {"kind": "dependencies", "path": path}},
            )


def test_path_narrows_the_extraction(project, announced):
    obs, text = _ask(_box(project), "dependencies", path="web")
    assert (
        "vite" in text
        and "fastapi" not in text
        and obs["lookup"] == {"kind": "dependencies", "path": "web"}
    )


# --- the docs build, family X -------------------------------------------------------


class _Execute:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        assert subject == "workspace.execute"
        return self.reply


def _run_box(project, reply, profile):
    box = developer_tools.WorkspaceToolbox(
        _Execute(reply), str(project), sandbox={"mode": "container"}
    )
    workspace_reads.offer_runs(box, lambda: profile)
    return box


def test_docs_build_is_offered_only_with_a_build_command(project, monkeypatch):
    box = _run_box(project, {}, {"docs_build": "python -m mkdocs build", "lint": None})
    schema = next(
        s for s in box.schemas() if s["function"]["name"] == "workspace__docs_build"
    )
    assert schema["function"]["parameters"]["properties"] == {}  # no free text
    assert "workspace__docs_build" not in _run_box(project, {}, {"lint": None}).tools
    # A read-only toolbox never gets it.
    assert "workspace__docs_build" not in _box(project).tools


def test_docs_build_runs_the_profiles_command_in_the_sandbox(project):
    reply = {
        "exit_code": 1,
        "stdout": "INFO building\n",
        "stderr": "ERROR - nav: missing.md\n",
    }
    box = _run_box(project, reply, {"docs_build": "python -m mkdocs build"})
    obs, text = _ask(box, "docs_build", command="rm -rf /")
    [(_subject, payload)] = box.requester.calls
    assert payload["command"] == "python -m mkdocs build" and payload["sandbox"] == {
        "mode": "container"
    }
    record = obs["docs_build"]
    assert record["exit_code"] == 1 and record["errors"] == ["ERROR - nav: missing.md"]
    assert len(record["output_sha256"]) == 64 and record["available"] is True
    assert "exited 1" in text and "ERROR - nav: missing.md" in text
    box = _run_box(
        project,
        {"exit_code": 0, "stdout": "ok"},
        {"docs_build": "python -m mkdocs build"},
    )
    _obs, text = _ask(box, "docs_build")
    assert "built, exit 0" in text
    box = _run_box(
        project,
        {"exit_code": 1, "stderr": "No module named mkdocs"},
        {"docs_build": "x"},
    )
    obs, text = _ask(box, "docs_build")
    assert "unavailable" in text and obs["docs_build"]["available"] is False


def test_a_licence_is_the_registrys_claim_labelled_so():
    from services.tools import research_registries

    doc = {
        "info": {"name": "fastapi", "version": "1.0", "license": "MIT"},
        "releases": {"1.0": [{"upload_time": "2024"}]},
    }
    record = research_registries.package_record(
        lambda url, extra: (200, {}, json.dumps(doc).encode()), "fastapi", "pypi", {}
    )
    assert record[research_registries.LICENSE] == "MIT"
    assert research_registries.LICENSE == "license_by_registry"
    doc["info"]["license"] = ""
    doc["info"]["classifiers"] = ["License :: OSI Approved :: BSD License"]
    record = research_registries.package_record(
        lambda url, extra: (200, {}, json.dumps(doc).encode()), "fastapi", "pypi", {}
    )
    assert record["license_by_registry"] == "BSD License"
