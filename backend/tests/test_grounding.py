"""The Verifier's ``grounded`` check (issue #444): the packages, versions and
imports a delivery names must exist.

The manifests are read from the delivered files (and an edit's fragment),
imports are resolved against the files the daemon reported, a registry's
answer is an observation recorded at delivery time (``services.tools.
registry_lookup``), and only a package no registry has, an exact version
that does not exist or a project import that names no observed file refutes.
The map-based symbol check stays a finding in the issue's design and is not
part of this module's tests.
"""

from __future__ import annotations

import json

import pytest
import workspace_read
from config import settings
from models.company import Company
from services.evidence import grounding, imports, manifests, sources
from services.tools import registry_lookup, research_tools

# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------


def _deps(*files):
    return {
        (dep.ecosystem, dep.name, dep.version)
        for dep in manifests.dependencies_in(list(files))
    }


def test_requirements_name_exact_versions_and_skip_ranges_urls_and_options():
    content = (
        "requests==2.31.0  # pinned\n"
        "uvicorn>=0.1\n"
        "fastapi[standard]==0.115.0\n"
        "-r other.txt\n"
        "--index-url https://example.invalid/simple\n"
        "pkg @ https://example.invalid/pkg.whl\n"
        "local-file\n"
    )
    assert _deps({"path": "requirements.txt", "content": content}) == {
        ("pypi", "requests", "2.31.0"),
        ("pypi", "uvicorn", None),
        ("pypi", "fastapi", "0.115.0"),
        ("pypi", "local-file", None),
    }


def test_pyproject_reads_pep621_and_poetry_and_leaves_ranges_alone():
    content = """
[project]
name = "sample"
dependencies = ["requests==2.31.0", "uvicorn>=0.1 ; python_version >= '3.11'"]

[tool.poetry.dependencies]
python = "^3.11"
flask = "^3.0"
pytest = "=8.0.0"
local = { path = "../local" }
"""
    assert _deps({"path": "pyproject.toml", "content": content}) == {
        ("pypi", "requests", "2.31.0"),
        ("pypi", "uvicorn", None),
        ("pypi", "flask", None),
        ("pypi", "pytest", "8.0.0"),
    }


def test_pyproject_fragment_is_read_by_its_sections():
    content = (
        "[project]\n"
        'dependencies = [\n  "httpx==0.27.0",\n]\n'
        "[tool.poetry.group.dev.dependencies]\n"
        'ruff = "0.17.1"\n'
    )
    assert _deps({"path": "pyproject.toml", "edits": [{"replace": content}]}) == {
        ("pypi", "httpx", "0.27.0"),
        ("pypi", "ruff", "0.17.1"),
    }


def test_package_json_exact_versions_and_skips_ranges_and_local_links():
    content = json.dumps(
        {
            "dependencies": {
                "react": "18.3.1",
                "lodash": "^4.17.21",
                "local": "file:../local",
                "linked": "workspace:*",
            },
            "devDependencies": {"vitest": "3.0.0-beta.1"},
        }
    )
    assert _deps({"path": "package.json", "content": content}) == {
        ("npm", "react", "18.3.1"),
        ("npm", "lodash", None),
        ("npm", "vitest", "3.0.0-beta.1"),
    }


def test_package_json_fragment_keeps_the_versioned_pairs():
    content = '"react": "18.3.1",\n"vitest": "^3.0.0"'
    assert _deps({"path": "package.json", "edits": [{"replace": content}]}) == {
        ("npm", "react", "18.3.1"),
        ("npm", "vitest", None),
    }


def test_cargo_only_an_equals_sign_pins_a_version():
    content = """
[dependencies]
serde = "=1.0.0"
tokio = { version = "1.0", features = ["full"] }
local = { path = "../local" }
"""
    assert _deps({"path": "Cargo.toml", "content": content}) == {
        ("crates", "serde", "1.0.0"),
        ("crates", "tokio", None),
    }


def test_go_mod_reads_the_require_block():
    content = """
module example.com/app

go 1.22

require (
\tgithub.com/stretchr/testify v1.9.0
\tgolang.org/x/net v0.1.0 // indirect
)
"""
    assert _deps({"path": "go.mod", "content": content}) == {
        ("go", "github.com/stretchr/testify", "v1.9.0"),
        ("go", "golang.org/x/net", "v0.1.0"),
    }
    assert (
        manifests.go_module([{"path": "go.mod", "content": content}])
        == "example.com/app"
    )


def test_versions_match_their_normalized_python_form():
    assert manifests.version_matches("pypi", "1.2", "1.2.0")
    assert manifests.version_matches("pypi", "v2.0.0", "2.0.0")
    assert not manifests.version_matches("pypi", "2.0", "2.0.1")
    assert manifests.version_matches("npm", "1.2.3", "1.2.3")


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _imports(*files):
    return {
        (entry.kind, entry.path, entry.specifier, entry.level)
        for entry in imports.imports_in(list(files))
    }


def test_python_imports_absolute_relative_and_skipped_symbol_import():
    content = (
        "import os\n"
        "import services.evidence.grounding\n"
        "from models.sprint import BacklogItem\n"
        "from .tail import helper\n"
        "from . import sibling\n"
    )
    assert _imports({"path": "backend/app/a.py", "content": content}) == {
        ("python", "backend/app/a.py", "os", 0),
        ("python", "backend/app/a.py", "services.evidence.grounding", 0),
        ("python", "backend/app/a.py", "models.sprint", 0),
        ("python-relative", "backend/app/a.py", "tail", 1),
    }


def test_es_go_and_rust_imports_are_read():
    assert _imports(
        {
            "path": "src/App.tsx",
            "content": 'import React from "react";\nimport {\n  x,\n} from "./lib/x";\nrequire("fs");\n',
        }
    ) == {
        ("es", "src/App.tsx", "react", 0),
        ("es", "src/App.tsx", "./lib/x", 0),
        ("es", "src/App.tsx", "fs", 0),
    }
    assert _imports(
        {
            "path": "main.go",
            "content": 'package main\nimport (\n\t"fmt"\n\t"example.com/app/store"\n)\n',
        }
    ) == {
        ("go", "main.go", "fmt", 0),
        ("go", "main.go", "example.com/app/store", 0),
    }
    assert _imports(
        {"path": "src/lib.rs", "content": "mod store;\nuse crate::store::x;\n"}
    ) == {("rust-mod", "src/lib.rs", "store", 0)}


def test_python_absolute_import_resolves_through_a_source_root():
    index = imports.ProjectIndex(
        ["src/app/main.py", "src/app/util.py", "backend/app/models/sprint.py"], True
    )
    audit = imports.audit(
        [imports.ImportRef("src/app/main.py", "app.util", "python")],
        index,
        {"pypi": set(), "npm": set(), "crates": set(), "go": set()},
    )
    assert not audit.unresolved and audit.resolved == 1

    missing = imports.audit(
        [imports.ImportRef("src/app/main.py", "app.missing", "python")],
        index,
        {"pypi": set(), "npm": set(), "crates": set(), "go": set()},
    )
    assert [entry.specifier for entry in missing.unresolved] == ["app.missing"]


def test_python_relative_import_resolves_and_refutes():
    files = ["pkg/__init__.py", "pkg/a.py", "pkg/b.py"]
    index = imports.ProjectIndex(files, True)
    declared = {"pypi": set(), "npm": set(), "crates": set(), "go": set()}
    assert not imports.audit(
        [imports.ImportRef("pkg/a.py", "b", "python-relative", level=1)],
        index,
        declared,
    ).unresolved
    missing = imports.audit(
        [imports.ImportRef("pkg/a.py", "nope", "python-relative", level=1)],
        index,
        declared,
    )
    assert [entry.specifier for entry in missing.unresolved] == ["nope"]


def test_es_relative_import_tries_extensions_and_index():
    index = imports.ProjectIndex(
        ["src/App.tsx", "src/lib/x.tsx", "src/components/index.ts"], True
    )
    declared = {"pypi": set(), "npm": set(), "crates": set(), "go": set()}
    audit = imports.audit(
        [
            imports.ImportRef("src/App.tsx", "./lib/x", "es"),
            imports.ImportRef("src/App.tsx", "./components", "es"),
        ],
        index,
        declared,
    )
    assert not audit.unresolved and audit.resolved == 2

    missing = imports.audit(
        [imports.ImportRef("src/App.tsx", "./lib/nope", "es")],
        index,
        declared,
    )
    assert [entry.specifier for entry in missing.unresolved] == ["./lib/nope"]


def test_es_alias_and_builtin_and_declared_package():
    index = imports.ProjectIndex(["src/App.tsx", "src/lib/x.ts"], True)
    declared = {"pypi": set(), "npm": {"react"}, "crates": set(), "go": set()}
    audit = imports.audit(
        [
            imports.ImportRef("src/App.tsx", "@/lib/x", "es"),
            imports.ImportRef("src/App.tsx", "node:fs", "es"),
            imports.ImportRef("src/App.tsx", "react", "es"),
            imports.ImportRef("src/App.tsx", "lodash", "es"),
        ],
        index,
        declared,
    )
    assert not audit.unresolved
    assert [entry.specifier for entry in audit.undeclared] == ["lodash"]


def test_go_internal_import_resolves_under_the_delivered_module_path():
    index = imports.ProjectIndex(["main.go", "store/db.go"], True)
    declared = {
        "pypi": set(),
        "npm": set(),
        "crates": set(),
        "go": {"github.com/stretchr/testify"},
    }
    audit = imports.audit(
        [
            imports.ImportRef("main.go", "example.com/app/store", "go"),
            imports.ImportRef("main.go", "example.com/app/nope", "go"),
            imports.ImportRef("main.go", "github.com/stretchr/testify", "go"),
            imports.ImportRef("main.go", "github.com/undeclared/x", "go"),
        ],
        index,
        declared,
        go_module="example.com/app",
    )
    assert [entry.specifier for entry in audit.unresolved] == ["example.com/app/nope"]
    assert [entry.specifier for entry in audit.undeclared] == [
        "github.com/undeclared/x"
    ]


def test_rust_mod_resolves_beside_the_file_or_in_its_folder():
    index = imports.ProjectIndex(
        ["src/lib.rs", "src/store.rs", "src/api/mod.rs", "src/api/handlers.rs"], True
    )
    declared = {"pypi": set(), "npm": set(), "crates": set(), "go": set()}
    assert not imports.audit(
        [imports.ImportRef("src/lib.rs", "store", "rust-mod")], index, declared
    ).unresolved
    assert not imports.audit(
        [imports.ImportRef("src/api/mod.rs", "handlers", "rust-mod")], index, declared
    ).unresolved
    missing = imports.audit(
        [imports.ImportRef("src/lib.rs", "nope", "rust-mod")], index, declared
    )
    assert [entry.specifier for entry in missing.unresolved] == ["nope"]


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def _files(*pairs):
    return [{"path": path, "content": content} for path, content in pairs]


def test_nothing_to_ground_leaves_no_check():
    assert (
        grounding.check_grounded({}, [{"path": "README.md", "content": "# hi"}]) is None
    )


def test_a_package_no_registry_has_refutes_the_delivery():
    evidence = {
        "registry_lookups": [
            {"ecosystem": "pypi", "package": "left-pad", "exists": False}
        ]
    }
    check = grounding.check_grounded(
        evidence,
        _files(("requirements.txt", "left-pad==1.0.0\n")),
    )
    assert check["outcome"] == "refuted"
    assert check["missing_packages"] == ["pypi:left-pad==1.0.0"]
    assert "no registry has pypi:left-pad==1.0.0" in check["observation"]


def test_a_package_the_runs_own_registry_lookup_refuted_also_refutes():
    evidence = {
        "tool_calls": [
            {
                "server": research_tools.SERVER,
                "tool": research_tools.PACKAGE,
                "package": "left-pad",
                "ecosystem": "npm",
                "status": 404,
                "is_error": True,
            }
        ]
    }
    check = grounding.check_grounded(
        evidence, _files(("package.json", '{"dependencies": {"left-pad": "1.0.0"}}'))
    )
    assert check["outcome"] == "refuted"
    assert check["missing_packages"] == ["npm:left-pad==1.0.0"]


def test_an_exact_version_the_registry_does_not_have_refutes():
    evidence = {
        "registry_lookups": [
            {
                "ecosystem": "pypi",
                "package": "requests",
                "version": "9.9.9",
                "exists": True,
                "requested_exists": False,
                "versions_complete": True,
            }
        ]
    }
    check = grounding.check_grounded(
        evidence, _files(("requirements.txt", "requests==9.9.9\n"))
    )
    assert check["outcome"] == "refuted"
    assert check["missing_versions"] == ["pypi:requests==9.9.9 (asked 9.9.9)"]


def test_a_version_answer_can_also_come_from_the_published_versions():
    evidence = {
        "registry_lookups": [
            {
                "ecosystem": "pypi",
                "package": "requests",
                "version": "2.31.0",
                "exists": True,
                "requested_exists": None,
                "versions": ["2.31.0"],
                "versions_complete": True,
            }
        ]
    }
    check = grounding.check_grounded(
        evidence, _files(("requirements.txt", "requests==2.31\n"))
    )
    assert check["outcome"] == "pass"
    assert check["missing_versions"] == []


def test_a_registry_that_could_not_be_reached_is_a_finding_not_a_refutation():
    evidence = {
        "registry_lookups": [
            {"ecosystem": "pypi", "package": "requests", "exists": None}
        ]
    }
    check = grounding.check_grounded(
        evidence, _files(("requirements.txt", "requests==2.31.0\n"))
    )
    assert check["outcome"] == "pass"
    assert check["unchecked_dependencies"] == ["pypi:requests==2.31.0"]


def test_an_exact_version_no_answer_addressed_is_a_finding_not_a_pass():
    evidence = {
        "registry_lookups": [
            {
                "ecosystem": "pypi",
                "package": "requests",
                "exists": True,
                "requested_exists": None,
                "versions_complete": False,
            }
        ]
    }
    check = grounding.check_grounded(
        evidence, _files(("requirements.txt", "requests==2.31.0\n"))
    )
    assert check["outcome"] == "pass"
    assert check["unchecked_dependencies"] == ["pypi:requests==2.31.0"]


def test_the_go_proxy_is_not_asked_what_it_cannot_answer():
    evidence = {
        "registry_lookups": [
            {
                "ecosystem": "go",
                "package": "github.com/x/y",
                "exists": True,
                "requested_exists": None,
                "versions_complete": False,
            }
        ]
    }
    check = grounding.check_grounded(
        evidence,
        _files(("go.mod", "module example.com/app\nrequire github.com/x/y v1.2.2\n")),
    )
    assert check["outcome"] == "pass"
    assert check["unchecked_dependencies"] == []


def test_a_project_import_that_names_no_reported_file_refutes():
    evidence = {
        "project_files": {"files": ["pkg/__init__.py", "pkg/a.py"], "truncated": False}
    }
    check = grounding.check_grounded(
        evidence,
        _files(("pkg/a.py", "from .nope import x\n")),
    )
    assert check["outcome"] == "refuted"
    assert check["missing_imports"][0]["specifier"] == "nope"


def test_a_cut_project_listing_keeps_a_missing_import_a_finding():
    evidence = {"project_files": {"files": ["pkg/a.py"], "truncated": True}}
    check = grounding.check_grounded(
        evidence,
        _files(("pkg/a.py", "from .nope import x\n")),
    )
    assert check["outcome"] == "pass"
    assert check["missing_imports"] == []
    assert check["unchecked_imports"] == 1
    assert "not found in a cut project listing" in check["observation"]


def test_an_undeclared_third_party_import_is_a_finding_when_the_manifest_is_whole():
    evidence = {
        "project_files": {"files": ["app.tsx", "package.json"], "truncated": False}
    }
    check = grounding.check_grounded(
        evidence,
        _files(
            (
                "package.json",
                json.dumps({"dependencies": {"react": "18.3.1"}}),
            ),
            ("app.tsx", 'import React from "react";\nimport x from "lodash";\n'),
        ),
    )
    assert check["outcome"] == "pass"
    assert [entry["specifier"] for entry in check["undeclared_imports"]] == ["lodash"]


def test_an_edit_fragment_does_not_make_the_projects_other_dependencies_undeclared():
    evidence = {
        "project_files": {"files": ["app.tsx", "package.json"], "truncated": False}
    }
    check = grounding.check_grounded(
        evidence,
        [
            {
                "path": "package.json",
                "edits": [{"replace": '"react": "18.3.1"'}],
            },
            {"path": "app.tsx", "content": 'import x from "lodash";\n'},
        ],
    )
    assert check["outcome"] == "pass"
    assert check["undeclared_imports"] == []


def test_the_sources_check_counts_the_backends_own_registry_lookups():
    check = sources.check_sources(
        {
            "tool_calls": [],
            "registry_lookups": [
                {"ecosystem": "pypi", "package": "requests", "exists": True}
            ],
        },
        _files(("requirements.txt", "requests==2.31.0\n")),
    )
    assert check["unchecked_dependencies"] == []


# ---------------------------------------------------------------------------
# The delivery-time lookup
# ---------------------------------------------------------------------------


@pytest.fixture
def lookups_on(monkeypatch):
    """``conftest.no_research_tools`` turns the lookups off; these fake the HTTP."""
    monkeypatch.setattr(settings, "GROUNDING_LOOKUPS_ENABLED", True)


class _Http:
    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, method, url, body=None, timeout=20.0):
        self.requests.append((method, url, body))
        status, headers, data = self.answers.get(url, (404, {}, b""))
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers = {**headers, "content-type": "application/json"}
        return status, headers, data


def _pypi(name, versions, latest):
    return {
        "info": {"name": name, "version": latest, "summary": ""},
        "releases": {version: [{"yanked": False}] for version in versions},
    }


def test_pypi_lookup_records_existence_and_the_exact_version(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "requests==2.31.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["server"] == "registry" and row["by"] == "verifier"
    assert row["exists"] is True and row["requested_exists"] is True
    assert row["versions_complete"] is True
    assert row["status"] == 200 and row["sha256"]


def test_pypi_404_marks_the_package_as_missing(lookups_on):
    http = _Http({})
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "left-pad==1.0.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert rows[0]["exists"] is False
    assert "no package named" in rows[0]["reason"]
    assert rows[0]["status"] == 404


def test_pypi_missing_exact_version_is_recorded(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "requests==9.9.9\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert rows[0]["exists"] is True and rows[0]["requested_exists"] is False


def test_lookups_are_deduplicated_bounded_and_skipped_when_answered(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    evidence = {
        "registry_lookups": [
            {
                "ecosystem": "pypi",
                "package": "requests",
                "version": "2.31.0",
                "exists": True,
            }
        ]
    }
    rows = registry_lookup.observe(
        evidence,
        _files(("requirements.txt", "requests==2.31.0\nuvicorn==1.0.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert [row["package"] for row in rows] == ["uvicorn"]
    assert [url for _method, url, _body in http.requests] == [
        f"{settings.RESEARCH_PYPI_URL}/uvicorn/json"
    ]


def test_lookups_are_bounded(lookups_on, monkeypatch):
    monkeypatch.setattr(settings, "GROUNDING_MAX_LOOKUPS", 1)
    http = _Http({})
    rows = registry_lookup.observe(
        {},
        _files(("requirements.txt", "requests==2.31.0\nuvicorn==1.0.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert len(rows) == 1
    assert len(http.requests) == 1


def test_a_registry_that_could_not_be_asked_is_tried_again(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_PYPI_URL}/requests/json": (
                200,
                {},
                _pypi("requests", ["2.31.0"], "2.31.0"),
            )
        }
    )
    evidence = {
        "registry_lookups": [
            {"ecosystem": "pypi", "package": "requests", "exists": None}
        ]
    }
    rows = registry_lookup.observe(
        evidence,
        _files(("requirements.txt", "requests==2.31.0\n")),
        http=http,
        hosts={"pypi.org"},
    )
    assert len(rows) == 1 and rows[0]["exists"] is True


def test_lookups_are_off_when_the_setting_is(monkeypatch):
    monkeypatch.setattr(settings, "GROUNDING_LOOKUPS_ENABLED", False)
    evidence = {}
    assert (
        registry_lookup.observe(
            evidence, _files(("requirements.txt", "requests==2.31.0\n"))
        )
        == []
    )
    assert "registry_lookups" not in evidence


def test_go_proxy_does_not_claim_the_exact_version(lookups_on):
    http = _Http(
        {
            f"{settings.RESEARCH_GO_URL}/github.com/x/y/@latest": (
                200,
                {},
                {"Version": "v1.2.3", "Time": "2026-01-01T00:00:00Z"},
            )
        }
    )
    rows = registry_lookup.observe(
        {},
        _files(("go.mod", "module example.com/app\nrequire github.com/x/y v1.2.2\n")),
        http=http,
        hosts={"proxy.golang.org"},
    )
    row = rows[0]
    assert row["exists"] is True
    assert row["requested_exists"] is None
    assert row["versions_complete"] is False


# ---------------------------------------------------------------------------
# The daemon's project listing
# ---------------------------------------------------------------------------


def test_daemon_reports_the_projects_paths_without_dependencies_or_hidden_folders(
    tmp_path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "left-pad.js").write_text("")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("")
    listing = workspace_read.project_files(str(tmp_path))
    assert listing == {"files": ["src/app.py"], "truncated": False}


def test_daemon_project_listing_says_when_it_was_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_read, "PROJECT_INDEX_MAX", 1)
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    listing = workspace_read.project_files(str(tmp_path))
    assert listing["truncated"] is True


# ---------------------------------------------------------------------------
# Verification and the gate
# ---------------------------------------------------------------------------


def test_verify_records_the_grounded_check_and_refutes_a_missing_import(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = Company(company_name="Grounding Corp")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    artifact = "```python path=app.py\nfrom .missing import x\n```\n"
    run = test_client.post(
        "/v1/agent_runs",
        json={
            "company_id": company.id,
            "external_ref": "task-grounding",
            "producer": "le-fabrique-task-worker",
            "artifact": artifact,
            "claims": {"summary": "delivered app.py"},
        },
    ).json()
    response = test_client.post(
        f"/v1/agent_runs/{run['id']}/verify",
        json={
            "company_id": company.id,
            "evidence": {"project_files": {"files": ["app.py"], "truncated": False}},
        },
    ).json()
    checks = {check["name"]: check for check in response["checks"]}
    assert checks["grounded"]["outcome"] == "refuted"
    assert response["verdict"] == "refuted"
