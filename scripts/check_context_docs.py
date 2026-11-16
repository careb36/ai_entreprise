#!/usr/bin/env python3
"""
Each context's document names every module of its folder, and only those.

``backend/app/services/<context>/`` is described by
``docs/contexts/<context>.md`` (the index is ``CLAUDE.md``). The document's
``- **Modules:**`` line lists the modules in backticks: a module directly in
the folder by its stem (``risk``), a module in a package inside it by the
package and the stem (``worker/close``, ``project_docs/c4``). An
``__init__`` is never listed. Text outside backticks on that line is ignored.

The check fails when:

- a context folder has no document, or the document has no Modules line;
- a module of the folder is missing from the Modules line;
- the Modules line lists a module that does not exist;
- a module has no entry in the document's body: neither ``<stem>.py`` nor a
  code span naming the module or its stem (``adr_docs.adr_documents``,
  ``worker/execute._execute_task``, ``collect.snapshot`` in the package's
  paragraph) appears outside the Modules line.

Exit status 0 when every document matches its folder, 1 otherwise. Run from
anywhere: paths are resolved from the repository root. Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "backend" / "app" / "services"
DOCS = ROOT / "docs" / "contexts"
MODULES_PREFIX = "- **Modules:**"
TOKEN = re.compile(r"`([a-z0-9_]+(?:/[a-z0-9_]+)?)`")


def folder_modules(folder: Path) -> set[str]:
    """The modules of one context folder, keyed as the Modules line names them."""
    found: set[str] = set()
    for path in folder.rglob("*.py"):
        rel = path.relative_to(folder)
        if "__pycache__" in rel.parts or path.stem == "__init__":
            continue
        found.add("/".join((*rel.parts[:-1], path.stem)))
    return found


def listed_modules(text: str) -> tuple[set[str] | None, str]:
    """The names on the Modules line, and the document without that line."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(MODULES_PREFIX):
            body = "\n".join(lines[:index] + lines[index + 1 :])
            return set(TOKEN.findall(line[len(MODULES_PREFIX) :])), body
    return None, text


def has_entry(module: str, body: str) -> bool:
    """``<stem>.py`` in the body, or a code span naming the module (``risk.assess``)."""
    stem = module.rsplit("/", 1)[-1]
    if f"{stem}.py" in body:
        return True
    named = re.compile(rf"(?<![\w/])(?:{re.escape(module)}|{re.escape(stem)})(?![\w/])")
    return any(named.search(span) for span in re.findall(r"`([^`\n]+)`", body))


def check(services: Path = SERVICES, docs: Path = DOCS) -> list[str]:
    """Every mismatch between the context folders and their documents."""
    problems: list[str] = []
    for folder in sorted(p for p in services.iterdir() if p.is_dir()):
        if folder.name.startswith("__"):
            continue
        context = folder.name
        doc = docs / f"{context}.md"
        if not doc.is_file():
            problems.append(
                f"{context}: no document at {doc.relative_to(docs.parent.parent)}"
            )
            continue
        listed, body = listed_modules(doc.read_text(encoding="utf-8"))
        if listed is None:
            problems.append(f"{context}: {doc.name} has no '{MODULES_PREFIX}' line")
            continue
        actual = folder_modules(folder)
        for module in sorted(actual - listed):
            problems.append(
                f"{context}: module {module} is missing from the Modules line"
            )
        for module in sorted(listed - actual):
            problems.append(
                f"{context}: the Modules line lists {module}, which does not exist"
            )
        for module in sorted(actual & listed):
            if not has_entry(module, body):
                problems.append(
                    f"{context}: module {module} has no entry in {doc.name}"
                )
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    if problems:
        print(
            f"{len(problems)} problem(s); fix docs/contexts/ to match backend/app/services/."
        )
        return 1
    print("Every context document lists its modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
