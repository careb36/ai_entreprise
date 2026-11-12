"""The imports a delivered file names, resolved against the files the daemon saw.

The Verifier's ``grounded`` check (``services.evidence.grounding``) reads the
imports of the files a delivery wrote or changed. An import of the project's
own modules — a relative one, an absolute one whose first component is a
module of the project, an ``@/`` alias, a Go import under the delivered
module path, a Rust ``mod`` — must name a file or folder the daemon reported;
one that names nothing refutes the delivery. A third-party import that no
manifest declares is a finding: the package may exist, but the project does
not depend on it on purpose.

The index is what the daemon reported: its project listing
(``workspace.generate``'s ``project_files``), the files it wrote, the files
the tools read, and the files the delivery itself declares. When the listing
was cut, an import that is not found is only a finding: the file may exist
past the cut. Resolution errs towards accepting — every suffix of a Python
path is tried, so a wrong ``PYTHONPATH`` never turns into a false refutation.
"""

from __future__ import annotations

import ast
import posixpath
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from services.contracts.tool_calls import WORKSPACE_SERVER
from services.evidence import manifests

PYTHON = "python"
PYTHON_RELATIVE = "python-relative"
ES = "es"
GO = "go"
RUST_MOD = "rust-mod"

JS_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".json",
    ".vue",
    ".svelte",
)
PYTHON_EXTENSIONS = (".py", ".pyi")
_NODE_BUILTINS = frozenset(
    [
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "diagnostics_channel",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "sys",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
        "test",
    ]
)

# Import names that do not spell the distribution that provides them. The
# check is a finding, never a refutation, so this list only quiets the common
# ones; a project may add its own by naming the distribution in a manifest.
IMPORT_ALIASES = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "magic": "python-magic",
    "pil": "pillow",
    "serial": "pyserial",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
}


@dataclass(frozen=True)
class ImportRef:
    """One import of a delivered file, as the file writes it."""

    path: str
    specifier: str
    kind: str
    level: int = 0
    line: int = 0


@dataclass
class ImportAudit:
    """What the imports of a delivery came to."""

    unresolved: list[ImportRef] = field(default_factory=list)
    undeclared: list[ImportRef] = field(default_factory=list)
    unchecked: list[ImportRef] = field(default_factory=list)
    resolved: int = 0
    skipped: str | None = None

    @property
    def complete(self) -> bool:
        return self.skipped is None


def _clean_path(path: Any) -> str | None:
    if not isinstance(path, str):
        return None
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith(("/", "../")) or path == "..":
        return None
    if any(part in ("", ".", "..") for part in path.split("/")):
        return None
    return path


def _python_imports(path: str, text: str) -> list[ImportRef]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(ImportRef(path, alias.name, PYTHON, line=node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:  # `from . import x` may name a symbol or a module.
                    found.append(
                        ImportRef(
                            path,
                            node.module,
                            PYTHON_RELATIVE,
                            level=node.level,
                            line=node.lineno,
                        )
                    )
            elif node.module:
                found.append(ImportRef(path, node.module, PYTHON, line=node.lineno))
    return found


_ES_FROM = re.compile(r"""\bfrom\s*["']([^"'\n]+)["']""")
_ES_SIDE_EFFECT = re.compile(r"""(?m)^\s*import\s*["']([^"'\n]+)["']""")
_ES_REQUIRE = re.compile(r"""\brequire\(\s*["']([^"'\n]+)["']\s*\)""")
_ES_DYNAMIC = re.compile(r"""\bimport\(\s*["']([^"'\n]+)["']\s*\)""")


def _es_imports(path: str, text: str) -> list[ImportRef]:
    found = []
    seen = set()
    for pattern in (_ES_FROM, _ES_SIDE_EFFECT, _ES_REQUIRE, _ES_DYNAMIC):
        for match in pattern.finditer(text):
            spec = match.group(1).strip()
            if spec and spec not in seen:
                seen.add(spec)
                found.append(
                    ImportRef(
                        path, spec, ES, line=text.count("\n", 0, match.start()) + 1
                    )
                )
    return found


_GO_BLOCK_ROW = re.compile(r'^\s*(?:([A-Za-z_.][\w.]*)\s+)?"([^"]+)"\s*$')
_GO_ONE = re.compile(r'^\s*import\s+(?:([A-Za-z_.][\w.]*)\s+)?"([^"]+)"')


def _go_imports(path: str, text: str) -> list[ImportRef]:
    found = []
    in_block = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not in_block and re.match(r"^import\s*\($", stripped):
            in_block = True
            continue
        if in_block and stripped == ")":
            in_block = False
            continue
        match = (_GO_BLOCK_ROW if in_block else _GO_ONE).match(line)
        if match:
            found.append(ImportRef(path, match.group(2), GO, line=number))
    return found


_RUST_MOD = re.compile(r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_][\w]*)\s*;")


def _rust_imports(path: str, text: str) -> list[ImportRef]:
    return [
        ImportRef(
            path,
            match.group(1),
            RUST_MOD,
            line=text.count("\n", 0, match.start()) + 1,
        )
        for match in _RUST_MOD.finditer(text)
    ]


def imports_in(files: Iterable[dict[str, Any]]) -> list[ImportRef]:
    """Every import the delivered code files name, once per file and specifier."""
    found: list[ImportRef] = []
    seen: set[tuple[str, str, str, int]] = set()
    for file in files:
        path = _clean_path(file.get("path"))
        if not path:
            continue
        text = str(file.get("content") or "")
        if not text:
            text = "\n".join(
                str(edit.get("replace") or "") for edit in file.get("edits") or []
            )
        lowered = path.lower()
        if lowered.endswith(PYTHON_EXTENSIONS):
            entries = _python_imports(path, text)
        elif lowered.endswith(JS_EXTENSIONS):
            entries = _es_imports(path, text)
        elif lowered.endswith(".go"):
            entries = _go_imports(path, text)
        elif lowered.endswith(".rs"):
            entries = _rust_imports(path, text)
        else:
            continue
        for entry in entries:
            mark = (entry.path, entry.specifier, entry.kind, entry.level)
            if mark not in seen:
                seen.add(mark)
                found.append(entry)
    return found


class ProjectIndex:
    """The files the daemon reported, and whether its listing was cut."""

    def __init__(self, files: Iterable[str], complete: bool) -> None:
        self.files = {
            cleaned for path in files if (cleaned := _clean_path(path)) is not None
        }
        self.complete = complete
        self.dirs: set[str] = set()
        for path in self.files:
            folder = posixpath.dirname(path)
            while folder and folder not in self.dirs:
                self.dirs.add(folder)
                folder = posixpath.dirname(folder)
        self.python_modules: set[str] = set()
        for path in self.files:
            if not path.lower().endswith(PYTHON_EXTENSIONS):
                continue
            parts = path.rsplit(".", 1)[0].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for start in range(len(parts)):
                self.python_modules.add(".".join(parts[start:]))
        self.python_roots = {module.split(".", 1)[0] for module in self.python_modules}

    @classmethod
    def from_evidence(
        cls, evidence: dict[str, Any] | None, delivered: Iterable[dict[str, Any]]
    ) -> ProjectIndex:
        evidence = evidence or {}
        paths: set[str] = {
            path
            for file in delivered
            if (path := _clean_path(file.get("path"))) is not None
        }
        for write in evidence.get("workspace_writes") or []:
            if isinstance(write, dict) and write.get("status") == "written":
                cleaned = _clean_path(write.get("path"))
                if cleaned:
                    paths.add(cleaned)
        for file in evidence.get("tool_edit_files") or []:
            if isinstance(file, dict):
                cleaned = _clean_path(file.get("path"))
                if cleaned:
                    paths.add(cleaned)
        for call in evidence.get("tool_calls") or []:
            if (
                isinstance(call, dict)
                and call.get("server") == WORKSPACE_SERVER
                and isinstance(call.get("path"), str)
            ):
                cleaned = _clean_path(call["path"])
                if cleaned:
                    paths.add(cleaned)
        listing = evidence.get("project_files")
        complete = False
        reported: list[Any] = []
        if isinstance(listing, dict) and isinstance(listing.get("files"), list):
            reported = listing["files"]
            complete = not listing.get("truncated")
        elif isinstance(listing, list):
            reported = listing
            complete = True
        for path in reported:
            cleaned = _clean_path(path)
            if cleaned:
                paths.add(cleaned)
        if not complete:
            complete = bool(evidence.get("project_files_complete"))
        return cls(paths, complete)

    def resolves_python(self, dotted: str) -> bool:
        if dotted in self.python_modules:
            return True
        if dotted.replace(".", "/") in self.dirs:
            return True
        return any(module.startswith(dotted + ".") for module in self.python_modules)

    def is_internal_python(self, dotted: str) -> bool:
        return dotted.split(".", 1)[0] in self.python_roots

    def resolves_python_relative(self, importer: str, level: int, module: str) -> bool:
        base = posixpath.dirname(importer)
        for _ in range(max(0, level - 1)):
            base = posixpath.dirname(base)
        parts = [part for part in base.split("/") if part]
        parts.extend(module.split("."))
        dotted = ".".join(parts)
        return self.resolves_python(dotted)

    def resolves_es(self, importer: str, specifier: str) -> bool:
        base = posixpath.normpath(
            posixpath.join(posixpath.dirname(importer), specifier)
        )
        if base.startswith(("..", "/")):
            return False
        candidates = [base]
        if not base.lower().endswith(JS_EXTENSIONS):
            candidates += [base + extension for extension in JS_EXTENSIONS]
            candidates += [base + "/index" + extension for extension in JS_EXTENSIONS]
        return any(candidate in self.files for candidate in candidates)

    def resolves_alias(self, specifier: str) -> bool | None:
        """Whether an ``@/`` or ``~/`` alias names a project file; ``None`` when it is not one."""
        if not specifier.startswith(("@/", "~/")):
            return None
        rest = specifier[2:]
        for prefix in ("", "src/", "app/", "lib/"):
            base = prefix + rest
            candidates = [base]
            if not base.lower().endswith(JS_EXTENSIONS):
                candidates += [base + extension for extension in JS_EXTENSIONS]
                candidates += [
                    base + "/index" + extension for extension in JS_EXTENSIONS
                ]
            if any(candidate in self.files for candidate in candidates):
                return True
        return False

    def resolves_go(self, specifier: str, module: str | None) -> bool:
        if not module or not (
            specifier == module or specifier.startswith(module + "/")
        ):
            return False
        relative = specifier[len(module) :].strip("/")
        if not relative:
            return any(path.endswith(".go") for path in self.files)
        return any(
            path.startswith(relative + "/") and path.endswith(".go")
            for path in self.files
        )

    def resolves_rust_mod(self, importer: str, name: str) -> bool:
        folder = posixpath.dirname(importer)
        stem = posixpath.basename(importer).rsplit(".", 1)[0]
        base = (
            folder if stem in ("lib", "main", "mod") else posixpath.join(folder, stem)
        )
        return (
            f"{base}/{name}.rs" in self.files or f"{base}/{name}/mod.rs" in self.files
        )


_STDLIB = frozenset(sys.stdlib_module_names) | {"__future__", "builtins"}


def _third_party_python(name: str, declared: set[str]) -> bool:
    """Whether an absolute Python import is a package the manifest should declare."""
    first = name.split(".", 1)[0]
    if first in _STDLIB:
        return False
    distribution = manifests.normalize_name(manifests.PYPI, first)
    if distribution in declared:
        return False
    alias = IMPORT_ALIASES.get(first.lower())
    return not (alias and manifests.normalize_name(manifests.PYPI, alias) in declared)


def audit(
    imports: Iterable[ImportRef],
    index: ProjectIndex,
    declared: dict[str, set[str]],
    *,
    go_module: str | None = None,
) -> ImportAudit:
    """Resolve every import; only a complete listing may turn one into a refutation."""
    result = ImportAudit()
    if not index.files:
        result.skipped = (
            "the daemon reported no project files; imports were not resolved"
        )
        return result
    for entry in imports:
        if entry.kind == PYTHON:
            if index.is_internal_python(entry.specifier):
                if index.resolves_python(entry.specifier):
                    result.resolved += 1
                elif index.complete:
                    result.unresolved.append(entry)
                else:
                    result.unchecked.append(entry)
                continue
            if _third_party_python(
                entry.specifier, declared.get(manifests.PYPI, set())
            ):
                result.undeclared.append(entry)
            continue
        if entry.kind == PYTHON_RELATIVE:
            if index.resolves_python_relative(entry.path, entry.level, entry.specifier):
                result.resolved += 1
            elif index.complete:
                result.unresolved.append(entry)
            else:
                result.unchecked.append(entry)
            continue
        if entry.kind == ES:
            if entry.specifier.startswith("."):
                if index.resolves_es(entry.path, entry.specifier):
                    result.resolved += 1
                elif index.complete:
                    result.unresolved.append(entry)
                else:
                    result.unchecked.append(entry)
                continue
            if _node_builtin(entry.specifier):
                continue
            alias = index.resolves_alias(entry.specifier)
            if alias is True:
                result.resolved += 1
            elif alias is False and index.complete:
                result.unresolved.append(entry)
            elif not _npm_declared(entry.specifier, declared.get(manifests.NPM, set())):
                result.undeclared.append(entry)
            continue
        if entry.kind == GO:
            if index.resolves_go(entry.specifier, go_module):
                result.resolved += 1
                continue
            first = entry.specifier.split("/", 1)[0]
            if "." not in first:
                continue  # standard library: `fmt`, `net/http`
            if go_module and entry.specifier.startswith(go_module + "/"):
                if index.complete:
                    result.unresolved.append(entry)
                else:
                    result.unchecked.append(entry)
            elif entry.specifier not in declared.get(manifests.GO, set()):
                result.undeclared.append(entry)
            continue
        if entry.kind == RUST_MOD:
            if index.resolves_rust_mod(entry.path, entry.specifier):
                result.resolved += 1
            elif index.complete:
                result.unresolved.append(entry)
            else:
                result.unchecked.append(entry)
    return result


def _node_builtin(specifier: str) -> bool:
    if specifier.startswith("node:"):
        return True
    return specifier.split("/", 1)[0] in _NODE_BUILTINS


def _npm_declared(specifier: str, declared: set[str]) -> bool:
    parts = specifier.split("/")
    name = "/".join(parts[:2]) if specifier.startswith("@") else parts[0]
    return manifests.normalize_name(manifests.NPM, name) in declared
