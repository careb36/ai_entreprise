"""Each context's document lists the modules of its folder.

``scripts/check_context_docs.py`` compares ``docs/contexts/<context>.md``'s
Modules line with ``backend/app/services/<context>/``. These tests run it on
the repository (it must pass) and on small trees of their own: a matching
document, a module missing from the line, a listed module that does not
exist, a module in a package, a module with no entry, a folder with no
document and a document with no Modules line.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_context_docs.py"
_spec = importlib.util.spec_from_file_location("check_context_docs", SCRIPT)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _tree(tmp_path: Path, files: list[str], doc: str | None) -> tuple[Path, Path]:
    services = tmp_path / "services"
    docs = tmp_path / "docs" / "contexts"
    docs.mkdir(parents=True)
    for name in ["__init__.py", *files]:
        path = services / "alpha" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (services / "__pycache__").mkdir()
    if doc is not None:
        (docs / "alpha.md").write_text(doc, encoding="utf-8")
    return services, docs


def _doc(modules: str, body: str) -> str:
    return f"# Alpha\n\n- **Modules:** {modules}.\n\n## Components\n\n{body}\n"


def test_the_repository_documents_every_module():
    assert check.check() == []


def test_the_script_exits_zero_on_the_repository():
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


def test_a_matching_document_passes(tmp_path):
    services, docs = _tree(
        tmp_path,
        ["risk.py", "pack/c4.py"],
        _doc("`risk`, `pack/c4`", "**`risk.py`** assesses. `c4.draw` draws."),
    )
    assert check.check(services, docs) == []


def test_a_module_missing_from_the_line_fails(tmp_path):
    services, docs = _tree(
        tmp_path, ["risk.py", "touched.py"], _doc("`risk`", "`risk.py`, `touched.py`")
    )
    assert check.check(services, docs) == [
        "alpha: module touched is missing from the Modules line"
    ]


def test_a_listed_module_that_does_not_exist_fails(tmp_path):
    services, docs = _tree(
        tmp_path, ["risk.py"], _doc("`risk`, `gone`", "`risk.py` and `gone.py`")
    )
    assert check.check(services, docs) == [
        "alpha: the Modules line lists gone, which does not exist"
    ]


def test_a_package_module_is_listed_by_its_path(tmp_path):
    services, docs = _tree(tmp_path, ["pack/c4.py"], _doc("`c4`", "`c4.py`"))
    assert check.check(services, docs) == [
        "alpha: module pack/c4 is missing from the Modules line",
        "alpha: the Modules line lists c4, which does not exist",
    ]


def test_a_module_with_no_entry_fails(tmp_path):
    services, docs = _tree(
        tmp_path, ["risk.py"], _doc("`risk`", "Something about risky_things.")
    )
    assert check.check(services, docs) == [
        "alpha: module risk has no entry in alpha.md"
    ]


def test_the_modules_line_alone_is_no_entry_but_a_code_span_is(tmp_path):
    services, docs = _tree(tmp_path, ["risk.py"], _doc("`risk`", ""))
    assert check.check(services, docs) == [
        "alpha: module risk has no entry in alpha.md"
    ]
    (docs / "alpha.md").write_text(
        _doc("`risk`", "`risk.assess` decides."), encoding="utf-8"
    )
    assert check.check(services, docs) == []


def test_a_folder_without_a_document_fails(tmp_path):
    services, docs = _tree(tmp_path, ["risk.py"], None)
    assert check.check(services, docs) == [
        "alpha: no document at docs/contexts/alpha.md"
    ]


def test_a_document_without_a_modules_line_fails(tmp_path):
    services, docs = _tree(tmp_path, ["risk.py"], "# Alpha\n\n`risk.py`\n")
    assert check.check(services, docs) == [
        "alpha: alpha.md has no '- **Modules:**' line"
    ]
