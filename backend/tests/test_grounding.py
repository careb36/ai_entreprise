"""The ``grounded`` check's outcomes, and verification recording it.

Nothing to ground leaves no check; a package no registry has or an exact
version it lacks refutes; a registry that could not be reached, an exact
version no answer addressed, an unresolved test-first import, a cut listing
and an undeclared third-party import are findings; verification over HTTP
records the check and the refuted verdict.

The Verifier's ``grounded`` check (issue #444): the packages, versions and
imports a delivery names must exist. A registry's answer is an observation
recorded at delivery time (``services.tools.registry_lookup``); only a package
no registry has, an exact version that does not exist or a project import that
names no observed file refutes. The symbols are ``test_grounding_signatures.py``.
"""

from __future__ import annotations

import json

from grounding_helpers import _files
from models.company import Company
from services.evidence import grounding, sources
from services.tools import research_tools


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


def test_a_test_first_import_that_names_no_module_yet_passes_as_a_finding():
    # QA's acceptance test is written before the Developer's module; ``src/``
    # already exists from an earlier task, so ``src.add_greeting`` looks like a
    # project import that names no file. It must not refute (issue #444 in the
    # e2e's test-first sprint).
    evidence = {
        "project_files": {
            "files": ["src/add_parser.py", "docs/ARCHITECTURE.md"],
            "truncated": False,
        }
    }
    check = grounding.check_grounded(
        evidence,
        _files(
            (
                "tests/test_req_1.py",
                (
                    "from src.add_greeting import add_greeting\n\n\n"
                    "def test_req_1_1_holds():\n    assert add_greeting()\n"
                ),
            )
        ),
    )
    assert check["outcome"] == "pass"
    assert check["missing_imports"] == []
    assert [entry["specifier"] for entry in check["test_first_imports"]] == [
        "src.add_greeting"
    ]
    assert "name no module yet" in check["observation"]


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
