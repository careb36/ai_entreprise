"""The ``grounded`` check's manifest parsers.

Exact versions from requirements, PEP 621 and Poetry, ``package.json``,
``Cargo.toml`` and ``go.mod``, ranges, URLs and local links left out, an edit's
fragment read by its sections, and versions compared in their normalized form.

The Verifier's ``grounded`` check (issue #444): the packages, versions and
imports a delivery names must exist. A registry's answer is an observation
recorded at delivery time (``services.tools.registry_lookup``); only a package
no registry has, an exact version that does not exist or a project import that
names no observed file refutes. The symbols are ``test_grounding_signatures.py``.
"""

from __future__ import annotations

import json

from services.evidence import manifests


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
