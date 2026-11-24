"""What a project declares, read statically (``project_facts``, PROTOCOL.md §5.9).

The API surface (committed OpenAPI documents and FastAPI routes), the
environment variables the code reads, and the manifests' direct
dependencies: each row names its file and the sha256 of the bytes read, no
server is started, and no ``.env`` value ever leaves the daemon.
"""

from __future__ import annotations

import hashlib
import json

import pytest

import abilities
import code_graph
import manifest_deps
import repo_map
import workspace_read

SECRET = "s3cr3t-value-from-dotenv"


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_map, "_state_dir", lambda: tmp_path / "state")


def _write(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _ask(root, kind, **extra):
    reply = code_graph.related(str(root), {"workspace_root": str(root), "seeds": [], "lookup": {"kind": kind, **extra}})
    return reply["lookup"]


def _sha(root, name):
    return hashlib.sha256((root / name).read_bytes()).hexdigest()


def test_each_kind_is_announced_apart():
    assert {"lookup", "api_surface", "config_vars", "dependencies"} <= set(abilities.FEATURES["workspace.related"])


def test_api_surface_lists_fastapi_routes_statically_with_prefix(tmp_path):
    root = tmp_path / "proj"
    _write(root, {
        "app/main.py": (
            "from fastapi import FastAPI\nfrom app.users import router\n\n"
            "app = FastAPI()\napp.include_router(router, prefix='/v1')\n\n"
            "@app.get('/health')\ndef health():\n    return {}\n"
        ),
        "app/users.py": (
            "from fastapi import APIRouter\nrouter = APIRouter(prefix='/users')\n\n"
            "@router.post('/')\nasync def create_user():\n    raise SystemExit('never run')\n\n"
            "@router.api_route('/{uid}', methods=['GET', 'PUT'])\ndef one(uid: int):\n    ...\n"
        ),
    })
    found = _ask(root, "api_surface")
    routes = [(r["method"], r["path"], r["function"], r["file"], r["line"]) for r in found["routes"]]
    assert ("GET", "/health", "health", "app/main.py", 7) in routes
    assert ("POST", "/users/", "create_user", "app/users.py", 4) in routes
    assert ("GET,PUT", "/users/{uid}", "one", "app/users.py", 8) in routes
    assert found["mounts"] == [{"line": 5, "router": "router", "prefix": "/v1", "file": "app/main.py"}]
    files = {f["path"]: f["sha256"] for f in found["files"]}
    assert files == {"app/main.py": _sha(root, "app/main.py"), "app/users.py": _sha(root, "app/users.py")}
    assert found["files_scanned"] == 2 and len(found["scanned_sha256"]) == 64
    # Cached by sha256: a second call reads the same extract.
    assert _ask(root, "api_surface")["routes"] == found["routes"]


def test_api_surface_reads_a_committed_openapi_document(tmp_path):
    root = tmp_path / "proj"
    doc = {
        "openapi": "3.1.0", "info": {"title": "Shop", "version": "2.0"},
        "paths": {"/orders": {"get": {"operationId": "listOrders", "summary": "List"}, "post": {}}},
    }
    _write(root, {"docs/openapi.json": json.dumps(doc), "api/swagger.yaml": (
        "swagger: '2.0'\ninfo:\n  title: Old\n  version: '1'\npaths:\n"
        "  /items/{id}:\n    get:\n      summary: One\n    delete:\n      summary: Gone\n"
    )})
    found = _ask(root, "api_surface")
    by_path = {d["path"]: d for d in found["openapi"]}
    shop = by_path["docs/openapi.json"]
    assert shop["sha256"] == _sha(root, "docs/openapi.json") and shop["title"] == "Shop"
    assert [(o["method"], o["path"], o["operation_id"]) for o in shop["operations"]] == [
        ("GET", "/orders", "listOrders"), ("POST", "/orders", None)]
    old = by_path["api/swagger.yaml"]
    assert old["spec"] == "2.0" and old["title"] == "Old"
    assert [(o["method"], o["path"]) for o in old["operations"]] == [("GET", "/items/{id}"), ("DELETE", "/items/{id}")]
    assert {f["path"] for f in found["files"]} == {"docs/openapi.json", "api/swagger.yaml"}


def test_config_vars_reads_the_code_never_a_dotenv(tmp_path):
    root = tmp_path / "proj"
    _write(root, {
        ".env": f"API_TOKEN={SECRET}\nDATABASE_URL=postgres://u:{SECRET}@db/x\n",
        "config/.env.production": f"API_TOKEN={SECRET}\n",
        "app/settings.py": (
            "import os\nfrom pydantic_settings import BaseSettings, SettingsConfigDict\n\n"
            "DEBUG = os.getenv('DEBUG', 'false')\nTOKEN = os.environ['API_TOKEN']\n"
            "PORT = int(os.environ.get('PORT', 8000))\nKEY = os.getenv('SIGNING_KEY', 'dev-key-123')\n"
            "DB = os.getenv('DB_URL', 'postgres://app:pw-in-url@db/x')\n\n"
            "class Settings(BaseSettings):\n    model_config = SettingsConfigDict(env_prefix='app_')\n"
            "    database_url: str\n    workers: int = 4\n"
        ),
        "web/client.ts": "export const base = process.env.API_BASE ?? 'http://localhost'\nconst m = import.meta.env.VITE_MODE\n",
    })
    found = _ask(root, "config_vars")
    rows = {(v["name"], v["file"]): v for v in found["variables"]}
    assert rows[("DEBUG", "app/settings.py")]["default"] == "false"
    assert rows[("API_TOKEN", "app/settings.py")]["required"] is True
    assert rows[("PORT", "app/settings.py")]["default"] == "8000"
    assert rows[("APP_DATABASE_URL", "app/settings.py")]["required"] is True
    assert rows[("APP_WORKERS", "app/settings.py")]["default"] == "4"
    assert rows[("API_BASE", "web/client.ts")]["default"] == "http://localhost"
    assert ("VITE_MODE", "web/client.ts") in rows
    signing = rows[("SIGNING_KEY", "app/settings.py")]
    assert "default" not in signing and signing["default_withheld"] is True
    text = json.dumps(found)
    assert rows[("DB_URL", "app/settings.py")]["default_withheld"] is True
    assert SECRET not in text and "dev-key-123" not in text and "pw-in-url" not in text
    read = {v["file"] for v in found["variables"]} | {f["path"] for f in found["files"]}
    assert read == {"app/settings.py", "web/client.ts"}  # No .env file is ever read.


def test_dependencies_from_each_manifest_kind(tmp_path):
    root = tmp_path / "proj"
    _write(root, {
        "requirements.txt": "fastapi==0.110.0  # web\nuvicorn[standard]>=0.29\n-r dev.txt\nprivate @ https://tok:x@host/p.whl\n",
        "requirements/dev.txt": "pytest\n",
        "pyproject.toml": (
            "[project]\nname='x'\ndependencies=['httpx>=0.27', 'pydantic<3']\n"
            "[project.optional-dependencies]\ndocs=['mkdocs==1.6.0']\n"
            "[tool.poetry.dependencies]\npython='^3.11'\nrich='^13'\nlib={git='https://x/y.git'}\n"
            "[tool.poetry.group.test.dependencies]\nhypothesis={version='^6'}\n"
        ),
        "web/package.json": json.dumps({"dependencies": {"react": "^18.3.1", "own": "github:me/own"}, "devDependencies": {"vite": "5.4.0"}}),
    })
    found = _ask(root, "dependencies")
    rows = {(d["manifest"], d["name"]): (d["version"], d["group"], d["ecosystem"]) for d in found["dependencies"]}
    assert rows[("requirements.txt", "fastapi")] == ("==0.110.0", "main", "pypi")
    assert rows[("requirements.txt", "uvicorn")] == (">=0.29", "main", "pypi")
    assert rows[("requirements.txt", "private")][0] == manifest_deps.DIRECT
    assert rows[("requirements/dev.txt", "pytest")] == ("", "main", "pypi")
    assert rows[("web/package.json", "react")] == ("^18.3.1", "main", "npm")
    assert rows[("web/package.json", "vite")] == ("5.4.0", "dev", "npm")
    assert rows[("web/package.json", "own")][0] == manifest_deps.DIRECT
    if manifest_deps._toml is not None:
        assert rows[("pyproject.toml", "httpx")] == (">=0.27", "main", "pypi")
        assert rows[("pyproject.toml", "mkdocs")] == ("==1.6.0", "optional:docs", "pypi")
        assert rows[("pyproject.toml", "rich")] == ("^13", "main", "pypi")
        assert rows[("pyproject.toml", "lib")][0] == manifest_deps.DIRECT
        assert rows[("pyproject.toml", "hypothesis")] == ("^6", "group:test", "pypi")
        assert ("pyproject.toml", "python") not in rows
    assert "tok:x" not in json.dumps(found)
    assert {m["path"]: m["sha256"] for m in found["manifests"]}["web/package.json"] == _sha(root, "web/package.json")


def test_path_narrows_and_outside_paths_are_refused(tmp_path):
    root = tmp_path / "proj"
    _write(root, {"a/package.json": json.dumps({"dependencies": {"x": "1"}}), "b/package.json": json.dumps({"dependencies": {"y": "1"}})})
    assert [d["name"] for d in _ask(root, "dependencies", path="a")["dependencies"]] == ["x"]
    for bad in ("../etc", "/etc", "a/../../x"):
        with pytest.raises(workspace_read.ReadError) as err:
            _ask(root, "dependencies", path=bad)
        assert err.value.status == 400
