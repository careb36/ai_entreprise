"""The ``docs_snippet_names`` check: the project names a snippet uses exist (issue #766).

Documentation shows code: a fenced block that imports ``create_user`` from
the project, when the project has no ``create_user``, teaches the reader
something false. Documenting a whole project asks that "every code name a
snippet cites exists (navigation)" (``docs/design/agent-toolsets.md``, "Next
changes" item 10). Snippets are read, never run.

``wanted`` reads the delivered documents' fenced blocks (``markdown_refs.
fenced_blocks``), as the daemon read them back (``doc_links.read_back_texts``),
and takes the names they import or call from the project:

- Python (``python``, ``py``, ``pycon``…): ``from M import a, b as c`` when
  ``M`` is relative or its first part is a folder or module of the project's
  listing (``project_files``), and ``M.f(…)`` after ``import M`` of such a
  module;
- JavaScript and TypeScript (``js``, ``ts``, ``tsx``…): the named imports of
  ``import { a, b as c } from './x'``, a relative specifier only (a package's
  names are the ``grounded`` check's question, not the project's).

A name that is itself a module of the listing (``from pkg import sub`` with
``pkg/sub.py``) is found there. The others are looked up through the daemon
(``workspace.related`` with ``lookup.kind: "definition"``,
``loop/docs_checks.py``), each answer kept on ``evidence["docs_snippets"]``
with the files and sha256 the daemon read. At most ``MAX_NAMES`` names.

``check`` judges that record. A name with no definition is a **finding, never
a refutation** in this slice: the parse reads functions, classes and methods,
not a module's constants or a re-export, so "not found" may be the parse's
gap. A name the daemon could not settle (it did not announce the lookup, it
did not answer, or its reply was partial) makes the check ``unverifiable``;
every name found, ``pass``. No names, no check. Pure: no I/O.
"""

from __future__ import annotations

import re
from typing import Any

from services.contracts.verdicts import VERDICT_UNVERIFIABLE
from services.evidence import doc_links, markdown_refs

CHECK_NAME = "docs_snippet_names"
MAX_NAMES = 30
SHOWN = 20
PYTHON = frozenset({"python", "py", "python3", "pycon", "ipython"})
SCRIPT = frozenset({"js", "javascript", "jsx", "mjs", "cjs", "ts", "typescript", "tsx"})
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_PY_FROM = re.compile(
    r"^[ \t]*(?:>>>[ \t]*)?from[ \t]+(\.*[A-Za-z_][\w.]*|\.+)[ \t]+import[ \t]+(\([^)]*\)|[^\n#]+)",
    re.MULTILINE,
)
_PY_IMPORT = re.compile(
    r"^[ \t]*(?:>>>[ \t]*)?import[ \t]+([A-Za-z_][\w.]*)(?:[ \t]+as[ \t]+(\w+))?[ \t]*$",
    re.MULTILINE,
)
_JS_IMPORT = re.compile(
    r"""^[ \t]*import[ \t]+(?:type[ \t]+)?(?:[\w$]+[ \t]*,[ \t]*)?\{([^}]*)\}[ \t]*from[ \t]*['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def _names(clause: str) -> list[str]:
    """The names an import clause binds from its module (``a``, ``b as c`` → a, b)."""
    out = []
    for part in clause.strip("() \t\n").replace("\n", ",").split(","):
        words = part.split()
        if len(words) > 1 and words[0] == "type":  # TypeScript's ``type X``.
            words = words[1:]
        name = words[0] if words else ""
        if name and name != "*" and _IDENT.match(name):
            out.append(name)
    return out


def _project_modules(files: set[str]) -> set[str]:
    """Every folder name and Python module stem of the listing."""
    found: set[str] = set()
    for path in files:
        parts = path.split("/")
        found.update(parts[:-1])
        if parts[-1].endswith(".py"):
            found.add(parts[-1][:-3])
    return found


def _module_file(module: str, name: str, files: set[str]) -> str | None:
    """A listed file that is *module*'s submodule *name*, if any."""
    base = module.lstrip(".").replace(".", "/")
    tails = [f"{base}/{name}.py", f"{base}/{name}/__init__.py"] if base else []
    for path in sorted(files):
        if any(path == t or path.endswith("/" + t) for t in tails):
            return path
    return None


def _in_block(lang: str, lines: list[tuple[int, str]], modules: set[str]):
    """``(line, module, name, how)`` of each project name the block uses."""
    if not lines:
        return
    first = lines[0][0]
    body = "\n".join(text for _n, text in lines)

    def line_of(offset: int) -> int:
        return first + body.count("\n", 0, offset)

    if lang in PYTHON:
        for match in _PY_FROM.finditer(body):
            module = match.group(1)
            if module.startswith(".") or module.split(".")[0] in modules:
                for name in _names(match.group(2)):
                    yield line_of(match.start()), module, name, "import"
        for match in _PY_IMPORT.finditer(body):
            module, alias = match.group(1), match.group(2) or match.group(1)
            if module.split(".")[0] not in modules:
                continue
            for call in re.finditer(re.escape(alias) + r"\.([A-Za-z_]\w*)\s*\(", body):
                yield line_of(call.start()), module, call.group(1), "call"
    elif lang in SCRIPT:
        for match in _JS_IMPORT.finditer(body):
            if match.group(2).startswith("."):
                for name in _names(match.group(1)):
                    yield line_of(match.start()), match.group(2), name, "import"


def wanted(
    evidence: dict[str, Any] | None, declared: list[dict[str, Any]]
) -> dict[str, Any]:
    """``{"documents", "names", "capped"}``: the project names the delivered
    documents' snippets use, each ``found`` already when it is a listed
    module, else ``None`` until the daemon is asked."""
    evidence = evidence or {}
    texts, _unread = doc_links.read_back_texts(evidence, declared)
    documents = sorted(p for p in texts if doc_links.is_documentation(p))
    listing = doc_links._listing(evidence)
    files = listing[0] if listing else set()
    modules = _project_modules(files | set(texts))
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    capped = 0
    for document in documents:
        for lang, lines in markdown_refs.fenced_blocks(texts[document]):
            for line, module, name, how in _in_block(lang, lines, modules):
                if (module, name) in seen:
                    continue
                seen.add((module, name))
                if len(rows) >= MAX_NAMES:
                    capped += 1
                    continue
                row: dict[str, Any] = {
                    "document": document,
                    "line": line,
                    "lang": lang,
                    "module": module,
                    "name": name,
                    "how": how,
                    "found": None,
                }
                if lang in PYTHON and (submodule := _module_file(module, name, files)):
                    row.update(found=True, module_file=submodule)
                rows.append(row)
    return {"documents": documents, "names": rows, "capped": capped}


def _where(row: dict[str, Any]) -> str:
    return f"{row.get('document')}:{row.get('line')} `{row.get('name')}` from {row.get('module')}"


def _shown(items: list[str]) -> str:
    more = f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else ""
    return "; ".join(items[:SHOWN]) + more


def check(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """``docs_snippet_names`` from ``evidence["docs_snippets"]`` (module docstring)."""
    record = (evidence or {}).get("docs_snippets")
    if not isinstance(record, dict):
        return None
    rows = [r for r in record.get("names") or [] if isinstance(r, dict)]
    if not rows:
        return None
    missing = [_where(r) for r in rows if r.get("found") is False]
    unsettled = [_where(r) for r in rows if r.get("found") is None]
    result: dict[str, Any] = {"name": CHECK_NAME, "checked": len(rows)}
    if missing:
        result["findings"] = [
            f"{m} (no definition in the project's parse)" for m in missing
        ]
    if record.get("capped"):
        result["capped"] = record["capped"]
    if unsettled:
        why = record.get("reason") or "the daemon did not settle them"
        result.update(
            outcome=VERDICT_UNVERIFIABLE,
            unchecked=unsettled,
            observation=f"{len(unsettled)} snippet name(s) not checked ({why}): {_shown(unsettled)}",
        )
        return result
    found = len(rows) - len(missing)
    text = f"{found} of {len(rows)} project name(s) the snippets use are defined"
    if missing:
        text += f"; not found (findings, not a refutation): {_shown(missing)}"
    result.update(outcome="pass", observation=text)
    return result
