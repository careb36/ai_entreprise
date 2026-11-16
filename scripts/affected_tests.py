#!/usr/bin/env python3
"""
The backend tests a change can break: by the import graph (``--by module``,
the default) or by whole contexts (``--by context``).

By module (``scripts/affected_graph.py``, ``scripts/affected_modules.py``),
a changed Python file of ``backend/app``, ``vscode-daemon``, ``bench`` or
``backend/tests`` selects the tests that import it, directly or through other
modules, from the lines that changed (``git diff -U0``): a change inside
function bodies reaches what uses the module; a change at its top level, or
one without line information, reaches every test that loads it, as a raise at
import would. Any other path, a deleted module, or a graph holding an import
by a computed name goes to the context rules below, with the reason printed;
the whole-suite rules apply in both modes. ``--explain`` prints, for each
selected test, the chain of imports, routes or fixtures that reaches the
change; ``--assume body|top`` reads a file without a diff (``--files``).

By context, ``docs/architecture/contexts.json`` gives every ``backend/tests/test_*.py`` a
context (``tests.files``: the contexts it exercises) or puts it in the few that
always run (``tests.always``, each with its reason). A changed file is read as:

- a module of ``backend/app/services/<context>/``: that context;
- any other path, by the longest prefix in ``tests.sources``: a context, or
  ``imports`` (the contexts the module imports, ``platform`` when none: the
  routers and the app's own modules), ``mentions`` (the tests whose text names
  the file's path, name or a folder of it: scripts, docs, workflows, the
  frontend), ``all``, or a test file by name;
- a file of ``backend/tests``: itself and the tests that import it, directly
  or through another test or helper; ``conftest.py`` and ``fixtures/`` are
  ``all``.

A context's change selects the tests of that context and of every context that
may import it (the reverse of ``depends_on``, transitively): a change in
platform can break loop. The always-run tests are added. The whole suite runs
when the script cannot decide: a rule says ``all`` (models, migrations,
conftest, the lock files, ``contexts.json`` itself) or no rule matches the
path. The reason is printed.

    python3 scripts/affected_tests.py                 # origin/main...HEAD + working tree
    python3 scripts/affected_tests.py --base main
    python3 scripts/affected_tests.py --files backend/app/services/evidence/risk.py
    python3 scripts/affected_tests.py --by context --files backend/app/config.py
    python3 scripts/affected_tests.py --explain
    python3 scripts/affected_tests.py --run -- -x -n auto
    python3 scripts/affected_tests.py --suggest backend/tests/test_new.py

The test files go to stdout, one per line (``backend/tests`` for the whole
suite), the reasons to stderr. ``--run`` runs pytest on them with the
interpreter running this script, from ``backend/`` with ``PYTHONPATH=.:app``,
and passes what follows ``--`` through. ``--suggest`` prints the contexts a
test file's imports and URLs point at (and those of the helper modules it
imports, such as ``factories.py``), to start its entry in the map; the
entry is still read and fixed by hand. The script only reads the repository
(and asks git which files changed). Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "architecture" / "contexts.json"
BACKEND = ROOT / "backend"
APP = BACKEND / "app"
TESTS = BACKEND / "tests"
DAEMON = ROOT / "vscode-daemon"
TESTS_REL = "backend/tests/"
SERVICES_REL = "backend/app/services/"


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sibling(name: str):
    """A module of this folder, by path: ``python3 -I`` puts no script folder
    on ``sys.path``."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(f"{name}.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- the contexts ------------------------------------------------------------


def closure(contexts: dict) -> dict[str, set[str]]:
    """Each context and every context it may import, transitively."""
    out: dict[str, set[str]] = {}

    def visit(name: str, seen: frozenset[str]) -> set[str]:
        if name in out:
            return out[name]
        deps: set[str] = set()
        for dep in contexts.get(name, {}).get("depends_on", []):
            if dep in contexts and dep not in seen:
                deps |= {dep} | visit(dep, seen | {dep})
        out[name] = deps
        return deps

    for name in contexts:
        visit(name, frozenset({name}))
    return out


def dependants(contexts: dict, names: set[str]) -> set[str]:
    """*names* and every context that may import one of them."""
    below = closure(contexts)
    return {c for c in contexts if c in names or below[c] & names}


def top(contexts: dict, names: set[str]) -> list[str]:
    """*names* without those another of them may import: the same selection."""
    below = closure(contexts)
    return sorted(n for n in names if not any(n in below[m] for m in names - {n}))


# --- imports -----------------------------------------------------------------


def imported_names(path: Path) -> set[str]:
    """The absolute dotted names *path* imports, a function's body too."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _stems(folder: Path) -> set[str]:
    return {p.stem for p in folder.glob("*.py")} if folder.is_dir() else set()


def contexts_of_names(names: set[str], contexts: dict, depth: int = 0) -> set[str]:
    """The contexts a set of imported names reaches: ``services.<context>``,
    a router or an app module through its own imports, a daemon module."""
    app_stems, daemon_stems = _stems(APP), _stems(DAEMON)
    found: set[str] = set()
    for dotted in names:
        parts = dotted.split(".")
        if parts[0] == "app" and len(parts) > 1:
            parts = parts[1:]
        head = parts[0]
        if head == "services" and len(parts) > 1 and parts[1] in contexts:
            found.add(parts[1])
        elif head == "routers" and len(parts) > 1 and depth < 2:
            router = APP / "routers" / f"{parts[1]}.py"
            if router.is_file():
                found |= module_contexts(router, contexts, depth + 1)
        elif head in app_stems and head != "main" and depth < 2:
            found |= module_contexts(APP / f"{head}.py", contexts, depth + 1)
        elif head in daemon_stems and head not in app_stems:
            found.add("workspace")
    return found


def module_contexts(path: Path, contexts: dict, depth: int = 0) -> set[str]:
    """The contexts a module outside ``services`` stands for: those it imports,
    or ``platform`` (the app's infrastructure) when it imports none."""
    found = contexts_of_names(imported_names(path), contexts, depth)
    return found or {"platform"}


# --- the routes a test calls (for --suggest) ---------------------------------

_DECORATOR = re.compile(r"@router\.(?:get|post|put|patch|delete)\(\s*\"([^\"]*)\"")
_PREFIX = re.compile(r"APIRouter\([^)]*prefix=\"([^\"]*)\"")
_PATCHED = re.compile(r"[\"']((?:app\.)?(?:services|routers)\.[\w.]+)[\"']")


def routes() -> list[tuple[re.Pattern[str], str]]:
    """Each route's pattern and the router module that serves it."""
    out = []
    for router in sorted((APP / "routers").glob("*.py")):
        text = router.read_text(encoding="utf-8")
        prefix = m.group(1) if (m := _PREFIX.search(text)) else ""
        for path in _DECORATOR.findall(text):
            full = re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(prefix + path))
            out.append((re.compile(full.rstrip("/") + "/?"), router.stem))
    return out


def urls_in(path: Path) -> set[str]:
    """The literal paths in *path* (``"/v1/sprints/{id}"`` and f-strings)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return set()
    found = set()
    # The pieces of an f-string are read with it, never alone: the ``"/v1/x/"``
    # of ``f"/v1/x/{id}/y"`` is not a call to ``/v1/x/``.
    pieces = {
        id(v)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for v in node.values
    }
    for node in ast.walk(tree):
        if id(node) in pieces:
            continue
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                v.value if isinstance(v, ast.Constant) else "X" for v in node.values
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        else:
            continue
        if text.startswith(("/v1/", "/health")):
            found.add(text.split("?")[0])
    return found


def _with_helpers(path: Path) -> list[Path]:
    """*path* and the helper modules of its folder it imports, transitively
    (``factories``, ``chat_fakes``...): a helper's imports are the test's too.
    Another test file it imports is not followed: what it borrows from one is
    a helper, and the map says what that test file exercises."""
    out, todo = [path], [path]
    while todo:
        for name in imported_names(todo.pop()):
            helper = path.parent / f"{name.split('.')[0]}.py"
            if helper.stem.startswith(("test_", "conftest")):
                continue
            if helper.is_file() and helper not in out:
                out.append(helper)
                todo.append(helper)
    return out


def suggest(path: Path, data: dict) -> list[str]:
    """The contexts a test file's imports and URLs point at, its helpers'
    too, reduced to the top."""
    contexts = data["contexts"]
    names: set[str] = set()
    sources = _with_helpers(path)
    for source in sources:
        names |= imported_names(source)
        # A patch by name (``mock.patch("services.loop.x.y")``) reaches it too.
        names |= set(_PATCHED.findall(source.read_text(encoding="utf-8")))
    found = contexts_of_names(names, contexts)
    table = routes()
    for url in {u for source in sources for u in urls_in(source)}:
        for pattern, router in table:
            if pattern.fullmatch(url):
                found |= contexts_of_names({f"routers.{router}"}, contexts)
                break
    return top(contexts, found)


# --- the tests ---------------------------------------------------------------


def test_files(tests: Path = TESTS) -> list[str]:
    return sorted(p.name for p in tests.glob("test_*.py"))


def importers(tests: Path = TESTS) -> dict[str, set[str]]:
    """For each module of *tests* (by stem), the test files that import it,
    directly or through another module of *tests*."""
    stems = {p.stem for p in tests.glob("*.py")}
    direct: dict[str, set[str]] = {s: set() for s in stems}
    for path in tests.glob("*.py"):
        for name in imported_names(path):
            head = name.split(".")[0]
            if head in stems and head != path.stem:
                direct[head].add(path.stem)
    out: dict[str, set[str]] = {}
    for stem in stems:
        seen, todo = set(), [stem]
        while todo:
            for user in direct.get(todo.pop(), ()):
                if user not in seen:
                    seen.add(user)
                    todo.append(user)
        out[stem] = {f"{s}.py" for s in seen if s.startswith("test_")}
    return out


def _names(rel: str) -> list[str]:
    """What a test may call *rel* by: its path, its name, and its folders two
    levels deep or more (``frontend/src`` for ``frontend/src/App.tsx``)."""
    parts = rel.split("/")
    return [rel, parts[-1], *("/".join(parts[:i]) for i in range(2, len(parts)))]


def rule_for(rel: str, sources: dict) -> object | None:
    """The rule of the longest prefix in *sources* that *rel* starts with."""
    keys = [k for k in sources if not k.startswith("$") and rel.startswith(k)]
    return sources[max(keys, key=len)] if keys else None


def select(
    changed: list[str],
    data: dict,
    tests: Path = TESTS,
    by: str = "context",
    lines: dict[str, set[int]] | None = None,
    graph: object | None = None,
    assume: str | None = None,
) -> dict:
    """The tests to run for *changed* (repository-relative paths), as data:
    ``tests`` (file names), ``everything`` (the reason, or ``None``), ``notes``
    and, by module, ``groups`` (each changed module, its kind and its tests
    with the reason). *lines* are the changed lines per path, from git."""
    if by == "module":
        return select_by_module(changed, data, tests, lines or {}, graph, assume)
    return select_by_context(changed, data, tests)


# The paths the module graph reads (the rest go to the context rules).
GRAPH_ROOTS = ("backend/app/", "vscode-daemon/", "bench/")


def _whole_suite_rule(rel: str, sources: dict) -> bool:
    rule = rule_for(rel, sources)
    return rule is not None and "all" in (rule if isinstance(rule, list) else [rule])


def select_by_module(
    changed: list[str],
    data: dict,
    tests: Path,
    lines: dict[str, set[int]],
    graph: object | None,
    assume: str | None,
) -> dict:
    """The tests to run for *changed*, from the import graph; a path the
    graph cannot decide goes to the context rules, with the reason."""
    modules = _sibling("affected_modules")
    sources: dict = (data.get("tests") or {}).get("sources", {})
    always = {k for k in (data.get("tests") or {}).get("always", {}) if k[0] != "$"}
    existing = set(test_files(tests))
    chosen: set[str] = set()
    notes: list[str] = []
    groups: list = []
    fallback: dict[str, str] = {}
    everything: str | None = None
    for rel in sorted({c.replace("\\", "/") for c in changed}):
        if rel.startswith(TESTS_REL):
            name = rel[len(TESTS_REL) :]
            if name == "conftest.py" or "/" in name or not name.endswith(".py"):
                everything = everything or f"{rel}: shared by every test"
            elif graph_of(graph, tests) and rel in graph_of(graph, tests).facts:
                groups.append(modules.module_group(graph_of(graph, tests), rel, None))
            else:
                notes.append(f"{rel}: no longer there, nothing to run for it")
            continue
        if _whole_suite_rule(rel, sources):
            everything = everything or f"{rel}: shared by every context"
        elif rel.endswith(".py") and rel.startswith(GRAPH_ROOTS):
            g = graph_of(graph, tests)
            if rel not in g.facts:
                fallback[rel] = "deleted, so the graph cannot see who imported it"
                continue
            groups.append(modules.module_group(g, rel, lines.get(rel), assume))
            if rel.startswith("bench/"):
                fallback[rel] = "a bench file, which the bench tests also read as data"
        else:
            fallback[rel] = "not a Python module of the graph"
    if groups and (opaque := modules.opaque_importers(graph_of(graph, tests))):
        for group in groups:
            fallback.setdefault(
                group.path, f"{opaque[0]} imports a module by a computed name"
            )
    for group in groups:
        chosen |= {t.rsplit("/", 1)[-1] for t in group.tests}
        notes.append(
            f"{group.path}: {group.kind} change ({group.why}): "
            f"{len(group.tests)} test file(s) by the module graph"
        )
    if fallback:
        by_context = select_by_context(sorted(fallback), data, tests)
        everything = everything or by_context["everything"]
        chosen |= set(by_context["tests"])
        for rel, why in sorted(fallback.items()):
            notes.append(f"{rel}: by its context rule, since it is {why}")
        notes.extend(f"  {note}" for note in by_context["notes"])
    chosen |= always
    return {
        "tests": sorted(chosen & existing),
        "everything": everything,
        "notes": notes,
        "groups": groups,
    }


_GRAPHS: dict = {}


def graph_of(graph: object | None, tests: Path):
    """The graph handed in, or the repository's (built once per run) when
    *tests* is the repository's own folder."""
    if graph is not None:
        return graph
    root = tests.resolve().parent.parent
    if root not in _GRAPHS:
        _GRAPHS[root] = _sibling("affected_graph").build(root)
    return _GRAPHS[root]


def select_by_context(changed: list[str], data: dict, tests: Path = TESTS) -> dict:
    """The tests to run for *changed* by the contexts and their rule."""
    contexts = data["contexts"]
    section = data.get("tests") or {}
    files: dict = {k: v for k, v in section.get("files", {}).items() if k[0] != "$"}
    always: dict = {k: v for k, v in section.get("always", {}).items() if k[0] != "$"}
    sources: dict = section.get("sources", {})
    existing = set(test_files(tests))
    chosen: set[str] = set()
    notes: list[str] = []
    everything: str | None = None
    users: dict[str, set[str]] | None = None
    texts: dict[str, str] | None = None
    reached: set[str] = set()

    for rel in sorted(set(changed)):
        rel = rel.replace("\\", "/")
        if rel.startswith(TESTS_REL):
            name = rel[len(TESTS_REL) :]
            if name == "conftest.py" or "/" in name or not name.endswith(".py"):
                everything = everything or f"{rel}: shared by every test"
                continue
            users = users if users is not None else importers(tests)
            hit = set(users.get(name[:-3], set()))
            if name.startswith("test_"):
                hit.add(name)
            chosen |= hit
            notes.append(f"{rel}: the test itself and its importers ({len(hit)})")
            continue
        parts = rel.split("/")
        if rel.startswith(SERVICES_REL) and len(parts) > 4 and parts[3] in contexts:
            rule: object = parts[3]
        else:
            rule = rule_for(rel, sources)
        if rule is None:
            everything = everything or f"{rel}: outside every context and rule"
            continue
        for token in rule if isinstance(rule, list) else [rule]:
            if token == "all":
                everything = everything or f"{rel}: shared by every context"
            elif token == "mentions":
                if texts is None:
                    texts = {
                        n: (tests / n).read_text(encoding="utf-8") for n in existing
                    }
                hit = {n for n, t in texts.items() if any(w in t for w in _names(rel))}
                chosen |= hit
                notes.append(f"{rel}: the tests that name it ({len(hit)})")
            elif isinstance(token, str) and token.startswith("test_"):
                chosen.add(token)
                notes.append(f"{rel}: {token}, named by its rule")
            elif token == "imports":
                path = ROOT / rel
                if not (rel.endswith(".py") and path.is_file()):
                    everything = everything or f"{rel}: cannot read its imports"
                    continue
                names = set(top(contexts, module_contexts(path, contexts)))
                reached |= dependants(contexts, names)
                notes.append(f"{rel}: imports {', '.join(sorted(names))}")
            elif token in contexts:
                reached |= dependants(contexts, {token})
                notes.append(f"{rel}: context {token}")
    if reached:
        notes.append("contexts reached: " + ", ".join(sorted(reached)))
        chosen |= {n for n, ctxs in files.items() if set(ctxs) & reached}
    chosen |= set(always)
    return {
        "tests": sorted(chosen & existing),
        "everything": everything,
        "notes": notes,
    }


# --- the command -------------------------------------------------------------


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def changed_files(base: str) -> list[str]:
    """The branch's changes since *base* plus the working tree's, untracked too."""
    return sorted(
        set(_git("diff", "--name-only", f"{base}...HEAD"))
        | set(_git("diff", "--name-only", "HEAD"))
        | set(_git("ls-files", "--others", "--exclude-standard"))
    )


def changed_lines(base: str, paths: list[str] | None = None) -> dict[str, set[int]]:
    """The changed lines of each file since the merge base with *base*, the
    working tree included (``git diff -U0``); ``{}`` when git cannot say."""
    try:
        merge_base = _git("merge-base", base, "HEAD")[0]
        diff = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "-U0", "--no-color", "--no-ext-diff"]
            + [merge_base, "--", *(paths or [])],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, IndexError, subprocess.CalledProcessError):
        return {}
    return _sibling("affected_modules").lines_from_diff(diff)


def _report(result: dict, explain: bool) -> None:
    for note in result["notes"]:
        print(note, file=sys.stderr)
    if not explain:
        return
    for group in result.get("groups", []):
        print(f"{group.path} ({group.kind}):", file=sys.stderr)
        for reason in group.tests.values():
            print(f"  {reason}", file=sys.stderr)


def main(argv: list[str]) -> int:
    extra: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, extra = argv[:at], argv[at + 1 :]
    parser = argparse.ArgumentParser(
        description="The backend tests a change can break."
    )
    parser.add_argument("--files", nargs="+", help="the changed files (repo paths)")
    parser.add_argument("--base", default="origin/main", help="git ref to diff from")
    parser.add_argument("--run", action="store_true", help="run pytest on them")
    parser.add_argument("--suggest", nargs="+", help="test files to suggest for")
    parser.add_argument(
        "--by",
        choices=("module", "context"),
        default="module",
        help="select by the import graph (default) or by whole contexts",
    )
    parser.add_argument(
        "--explain", action="store_true", help="print why each test is selected"
    )
    parser.add_argument(
        "--assume",
        choices=("body", "top"),
        help="read a file without a diff as a body or a top-level change "
        "(default: top, the safe reading)",
    )
    args = parser.parse_args(argv)
    data = load()

    if args.suggest:
        print(json.dumps({Path(p).name: suggest(Path(p), data) for p in args.suggest}))
        return 0
    lines: dict[str, set[int]] = {}
    if args.files is not None:
        changed = args.files
        if args.by == "module":
            lines = changed_lines(args.base, changed)
    else:
        try:
            changed = changed_files(args.base)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Cannot list the changes since {args.base}: {exc}", file=sys.stderr)
            return 2
        if args.by == "module":
            lines = changed_lines(args.base)
    result = select(changed, data, by=args.by, lines=lines, assume=args.assume)
    _report(result, args.explain)
    if result["everything"]:
        print(f"The whole suite: {result['everything']}.", file=sys.stderr)
        targets = ["tests"]
    else:
        print(
            f"{len(result['tests'])} of {len(test_files())} test files "
            f"({len(changed)} changed file(s)).",
            file=sys.stderr,
        )
        targets = [f"tests/{name}" for name in result["tests"]]
    for target in targets:
        print(f"backend/{target}")
    if not args.run:
        return 0
    if not targets:
        print("Nothing to run.", file=sys.stderr)
        return 0
    env = {**os.environ, "PYTHONPATH": ".:app"}
    command = [sys.executable, "-m", "pytest", *targets, *extra]
    return subprocess.run(command, cwd=BACKEND, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
