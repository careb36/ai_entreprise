#!/usr/bin/env python3
"""
The frontend's dependency rule between layers, checked from the imports.

``docs/architecture/frontend.json`` divides ``frontend/src`` into layers,
listed from the bottom up, each owning some folders or files by path
(``lib``, ``services``, ``pages``, ``App.tsx``). A module may import from its
own layer or a layer below it, never from one above: ``components`` may not
import ``pages``, ``services`` may not import ``components``. The frontend
counterpart of ``scripts/check_architecture.py`` (issue #469) for the
backend; this one is issue #519.

The imports are read from the text, with comments and the contents of
strings blanked first, so a URL or a commented-out import is never one:
``import … from '…'``, ``import '…'``, ``export … from '…'`` and dynamic
``import('…')``. A specifier is resolved as TypeScript's bundler resolution
does for this project: relative to the file, or through an alias of the data
file's ``aliases`` (``{"@/": ""}`` would map ``@/lib/x`` to ``src/lib/x``),
trying the extensions and ``index`` files; a bare specifier (``react``) is a
package and no edge. A regular-expression literal is not told from
code; one that holds a quote can hide the rest of its line, never more,
since a string ends at the end of its line. ``import type`` counts: a type is a dependency of the
design even though the build erases it.

The network belongs to the services layer: a module that names ``fetch``
(called), ``XMLHttpRequest``, ``EventSource``, ``WebSocket`` or ``axios``,
or imports ``axios``, must be listed in ``network.modules``. The frontend
counterpart of the backend's "HTTP only in adapters" rule (#511), itself
after AixleHQ/flow's design rules (Apache-2.0, design only): the views call
functions of ``services`` that return data, and only the listed modules
speak to the backend.

Tests (``*.test.ts(x)``, a ``__tests__`` folder, and the test support under
``src/test/``) are exempt from the layering and the network rule: a page's
test renders the page and stubs ``fetch``. They are not exempt from
everything: no module outside the tests may import a test file or the test
support, so nothing written for the tests ships in the bundle; and their
relative imports must resolve, like every other module's.

The check fails when:

- a source file under ``src`` is in no layer, or in two, or a layer names a
  path that does not exist;
- a relative or aliased import resolves to no file;
- a module imports a module of a layer above its own, or a non-test module
  imports a test, and that edge is not in the ``allowlist``;
- a module uses the network and is neither in ``network.modules`` nor in
  the ``allowlist`` (as ``"file: fetch"``), or ``network.modules`` names a
  file that does not exist or does not use the network.

The allowlist holds what broke the rule when it was adopted, file by file
(``"services/api/client.ts -> store/useAuthStore.ts"``). It may only
shrink: an entry that no longer occurs is reported, and ``--update`` drops
it (a listed network module that stopped using the network too);
``--update`` never adds one. Exit status 0 when the code keeps to the rule,
1 otherwise. Run from anywhere: paths are resolved from the repository root.
Standard library only.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "architecture" / "frontend.json"
SRC = ROOT / "frontend" / "src"

SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")
# Tried in order when a specifier names no file as written.
RESOLVE_SUFFIXES = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".json")
INDEX_FILES = ("index.ts", "index.tsx", "index.js", "index.jsx")
NETWORK_GLOBALS = ("XMLHttpRequest", "EventSource", "WebSocket", "axios")


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── Reading a module ──────────────────────────────────────────────────────────


def _blank(text: str) -> tuple[str, str]:
    """The text with comments removed, twice over.

    The first copy keeps the strings (to read the specifiers from); the
    second also blanks every string's contents, so a word inside a string is
    never code. Newlines are kept in both, so offsets stay on their line. A
    template literal's ``${…}`` is code and is kept as such.
    """
    keep: list[str] = []
    bare: list[str] = []
    i, n = 0, len(text)
    # A stack of open template literals' brace depths.
    templates: list[int] = []
    depth = 0

    def emit(chunk: str, blank: bool) -> None:
        keep.append(chunk)
        bare.append(re.sub(r"[^\n]", " ", chunk) if blank else chunk)

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            emit(re.sub(r"[^\n]", " ", text[i:j]), False)
            i = j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            emit(re.sub(r"[^\n]", " ", text[i:j]), False)
            i = j
        elif c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            emit(c, False)
            emit(text[i + 1 : j - 1], True)
            emit(text[j - 1 : j], False)
            i = j
        elif c == "`" or (c == "}" and templates and templates[-1] == depth):
            # Opening a template, or closing a `${…}` back into one.
            if c == "}":
                templates.pop()
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "`":
                    break
                if text[j] == "$" and j + 1 < n and text[j + 1] == "{":
                    break
                j += 1
            emit(c, False)
            emit(text[i + 1 : j], True)
            if j < n and text[j] == "$":
                emit("${", False)
                templates.append(depth)
                i = j + 2
            else:
                emit(text[j : j + 1], False)
                i = j + 1
        else:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            emit(c, False)
            i += 1
    return "".join(keep), "".join(bare)


_STATIC = re.compile(
    r"(?<![\w$.])(?:import|export)\b(?:[^'\";]*?\bfrom)?\s*(['\"])([^'\"\n]+)\1",
    re.DOTALL,
)
_DYNAMIC = re.compile(r"(?<![\w$.])import\s*\(\s*(['\"])([^'\"\n]+)\1\s*\)")
_FETCH = re.compile(r"(?<![\w$.])fetch\s*\(")
_FETCH_QUALIFIED = re.compile(r"(?<![\w$.])(?:window|globalThis|self)\s*\.\s*fetch\b")
_GLOBAL = re.compile(r"(?<![\w$.])(" + "|".join(NETWORK_GLOBALS) + r")(?![\w$])")


def read_module(path: Path) -> tuple[list[str], list[str]]:
    """The import specifiers *path* names, and the network globals it uses."""
    keep, bare = _blank(path.read_text(encoding="utf-8"))
    # A match must start in code: the keyword is not inside a string or comment.
    specifiers = [
        m.group(2)
        for pattern in (_STATIC, _DYNAMIC)
        for m in pattern.finditer(keep)
        if bare[m.start()] != " "
    ]
    network = set(_GLOBAL.findall(bare))
    if _FETCH.search(bare) or _FETCH_QUALIFIED.search(bare):
        network.add("fetch")
    if "axios" in specifiers:
        network.add("axios")
    return specifiers, sorted(network)


# ── Resolving and placing ─────────────────────────────────────────────────────


def resolve(
    specifier: str, importer: Path, src: Path, aliases: dict
) -> Path | None | str:
    """The file a specifier names, ``None`` when it names none, or ``"package"``."""
    spec = specifier.split("?", 1)[0]
    if spec.startswith("."):
        base = importer.parent / spec
    else:
        for prefix, target in aliases.items():
            if spec.startswith(prefix):
                base = src / target / spec[len(prefix) :]
                break
        else:
            return "package"
    candidates = [base]
    candidates += [base.with_name(base.name + s) for s in RESOLVE_SUFFIXES]
    if base.suffix in (".js", ".jsx"):
        candidates += [base.with_suffix(s) for s in (".ts", ".tsx")]
    candidates += [base / index for index in INDEX_FILES]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def rel(path: Path, src: Path) -> str:
    return path.resolve().relative_to(src.resolve()).as_posix()


def _under(relpath: str, owned: str) -> bool:
    return relpath == owned or relpath.startswith(owned.rstrip("/") + "/")


def is_test(relpath: str, data: dict) -> bool:
    tests = data.get("tests", {})
    if any(_under(relpath, p) for p in tests.get("paths", [])):
        return True
    parts = relpath.split("/")
    for pattern in tests.get("patterns", []):
        if pattern in parts[:-1] or fnmatch.fnmatch(parts[-1], pattern):
            return True
    return False


def layers_of(relpath: str, data: dict) -> list[str]:
    return [
        layer["name"]
        for layer in data["layers"]
        if any(_under(relpath, p) for p in layer["paths"])
    ]


# ── The check ─────────────────────────────────────────────────────────────────


def analyse(data: dict, src: Path = SRC) -> dict:
    """Everything the check reports, as data (the test reads it too)."""
    problems: list[str] = []
    aliases = data.get("aliases", {})
    rank = {layer["name"]: i for i, layer in enumerate(data["layers"])}
    for layer in data["layers"]:
        for owned in layer["paths"]:
            if not (src / owned).exists():
                problems.append(f"layer {layer['name']}: {owned} does not exist")

    modules = sorted(
        p
        for p in src.rglob("*")
        if p.is_file() and p.suffix in SOURCE_SUFFIXES and "node_modules" not in p.parts
    )
    layer: dict[str, str] = {}
    tests: set[str] = set()
    for path in modules:
        key = rel(path, src)
        if is_test(key, data):
            tests.add(key)
            continue
        found = layers_of(key, data)
        if not found:
            problems.append(f"{key}: in no layer; add its folder to {DATA.name}")
        elif len(found) > 1:
            problems.append(f"{key}: in more than one layer ({', '.join(found)})")
        else:
            layer[key] = found[0]

    network_modules = set(data.get("network", {}).get("modules", []))
    edges: list[tuple[str, str]] = []
    violations: list[str] = []
    reasons: dict[str, str] = {}
    network: dict[str, list[str]] = {}
    for path in modules:
        key = rel(path, src)
        specifiers, used = read_module(path)
        for spec in specifiers:
            target = resolve(spec, path, src, aliases)
            if target == "package":
                continue
            if target is None:
                problems.append(f"{key}: imports {spec!r}, which resolves to no file")
                continue
            try:
                dst = rel(target, src)
            except ValueError:
                continue  # outside src (vite.config, package.json)
            if dst == key:
                continue
            edges.append((key, dst))
            if key in tests:
                continue
            edge = f"{key} -> {dst}"
            if is_test(dst, data):
                violations.append(edge)
                reasons[edge] = "imports a test (tests do not ship in the bundle)"
                continue
            src_layer = layer.get(key)
            dst_layers = layers_of(dst, data)
            if (
                src_layer
                and len(dst_layers) == 1
                and rank[dst_layers[0]] > rank[src_layer]
            ):
                violations.append(edge)
                below = [n for n, r in rank.items() if r < rank[src_layer]]
                reasons[edge] = (
                    f"{src_layer} may not import {dst_layers[0]} "
                    f"(allowed: {', '.join([src_layer, *below])})"
                )
        if used and key not in tests:
            network[key] = used
            if key not in network_modules:
                for name in used:
                    entry = f"{key}: {name}"
                    violations.append(entry)
                    reasons[entry] = (
                        f"uses {name}; the network belongs in a module of services "
                        f"listed in network.modules ({DATA.name}), which the "
                        "views call for data"
                    )

    for key in sorted(network_modules):
        if not (src / key).is_file():
            problems.append(f"{key}: listed in network.modules, but no such file")
    network_stale = sorted(
        k for k in network_modules if (src / k).is_file() and k not in network
    )
    allowlist = set(data.get("allowlist", []))
    new = sorted({v for v in violations if v not in allowlist})
    stale = sorted(allowlist - set(violations))
    problems += [f"{v}: {reasons[v]}" for v in new]
    return {
        "modules": [rel(p, src) for p in modules],
        "layer": layer,
        "tests": sorted(tests),
        "edges": edges,
        "network": network,
        "network_stale": network_stale,
        "violations": sorted(set(violations)),
        "new": new,
        "stale": stale,
        "problems": problems,
    }


def main(argv: list[str], data_path: Path = DATA, src: Path = SRC) -> int:
    update = "--update" in argv
    data = load(data_path)
    result = analyse(data, src)
    command = "scripts/check_frontend_architecture.py --update"
    if update:
        if result["problems"]:
            print("Not updating while the code breaks the rule:", file=sys.stderr)
        else:
            stale = set(result["stale"])
            data["allowlist"] = [e for e in data.get("allowlist", []) if e not in stale]
            gone = set(result["network_stale"])
            network = data.setdefault("network", {})
            network["modules"] = [
                m for m in network.get("modules", []) if m not in gone
            ]
            data_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            print(
                f"Allowlist written: {len(data['allowlist'])} entry(ies), "
                f"{len(network['modules'])} network module(s)."
            )
    else:
        if result["stale"]:
            print(
                "The allowlist can shrink (these no longer occur): "
                + ", ".join(result["stale"])
                + f". Run {command} and commit it."
            )
        if result["network_stale"]:
            print(
                "The network modules can shrink (these no longer use the "
                "network): "
                + ", ".join(result["network_stale"])
                + f". Run {command} and commit it."
            )
    for problem in result["problems"]:
        print(problem, file=sys.stderr)
    if not result["problems"]:
        print(
            f"{len(result['layer'])} modules in {len(data['layers'])} layers "
            f"(and {len(result['tests'])} test files), {len(result['edges'])} "
            f"imports, {len(result['violations'])} allowlisted violation(s) "
            f"against the rule, the network in {len(result['network'])} listed "
            "module(s) only."
        )
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
