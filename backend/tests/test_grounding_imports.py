"""The ``grounded`` check's imports and their resolution.

Imports for Python, JavaScript/TypeScript, Go and Rust, resolved against the
files the daemon reported (a source root, relative imports, extensions and
index files, aliases, the Go module path, Rust's ``mod``); an unresolved
import from a test file is a finding; the daemon's project listing leaves out
dependencies and hidden folders and says when it was cut.

The Verifier's ``grounded`` check (issue #444): the packages, versions and
imports a delivery names must exist. A registry's answer is an observation
recorded at delivery time (``services.tools.registry_lookup``); only a package
no registry has, an exact version that does not exist or a project import that
names no observed file refutes. The symbols are ``test_grounding_signatures.py``.
"""

from __future__ import annotations

import workspace_read
from services.evidence import imports


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


def test_an_unresolved_import_from_a_test_file_is_a_finding_not_a_refutation():
    index = imports.ProjectIndex(["src/existing.py"], True)
    declared = {"pypi": set(), "npm": set(), "crates": set(), "go": set()}
    audit = imports.audit(
        [imports.ImportRef("tests/test_new.py", "src.new", "python")],
        index,
        declared,
    )
    assert not audit.unresolved
    assert [entry.specifier for entry in audit.test_first] == ["src.new"]
    # The same import from source code still refutes.
    code = imports.audit(
        [imports.ImportRef("src/existing.py", "src.new", "python")], index, declared
    )
    assert [entry.specifier for entry in code.unresolved] == ["src.new"]


def test_a_spec_file_alias_that_names_no_module_is_a_finding():
    index = imports.ProjectIndex(["src/App.tsx"], True)
    declared = {"pypi": set(), "npm": set(), "crates": set(), "go": set()}
    audit = imports.audit(
        [imports.ImportRef("e2e/greeting.spec.ts", "@/Greeting", "es")],
        index,
        declared,
    )
    assert not audit.unresolved
    assert [entry.specifier for entry in audit.test_first] == ["@/Greeting"]


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
