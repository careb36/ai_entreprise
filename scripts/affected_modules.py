"""
The tests a change can break, from the import graph (``scripts/affected_tests.py
--by module``; the graph is ``scripts/affected_graph.py``).

A changed module is read by what changed in it:

- *body*: every changed line is inside a function's body. Only what calls the
  function can see it, so the tests selected are those that use the module,
  directly or through other modules (the reverse transitive closure of the
  *use* edges). ``conftest.py`` and ``main.py`` are wiring: their fixtures
  patch, reset or install what they import, and ``main`` mounts the routers
  and installs handlers, so a test reaches a module through them only when
  they import it directly (a change to ``lifecycle.py`` selects the tests that
  build the app; one to a module ``lifecycle`` imports does not, through
  ``main``).
- *top*: a line outside every function body (an import, a constant, a
  decorator, a default argument, a class body), or no line information
  (``--files`` without a diff in git, a deleted line at a boundary). Its top
  level runs wherever the module is loaded: the tests selected are every test
  that loads it by any edge, ``conftest.py``'s fixtures and the routers
  ``main.py`` mounts included. A raise at import fails exactly those tests.

Added for any changed module: the tests that walk a folder (``rglob``,
``glob``, ``os.walk``) and name one of its folders as a string. The changed
tests and the tests that import them come from the same graph (a test file is
a node), and the always-run set is added. Standard library only.
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_graph_module():
    """``affected_graph`` beside this file (``python3 -I`` puts no script
    folder on ``sys.path``)."""
    if "affected_graph" in sys.modules:
        return sys.modules["affected_graph"]
    spec = importlib.util.spec_from_file_location(
        "affected_graph", Path(__file__).with_name("affected_graph.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["affected_graph"] = module
    spec.loader.exec_module(module)
    return module


affected_graph = _load_graph_module()
CONFTEST, MAIN = affected_graph.CONFTEST, affected_graph.MAIN
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def wiring(node: str) -> bool:
    return node == MAIN or node == CONFTEST or node.startswith(CONFTEST + "::")


# --- what changed in a file ---------------------------------------------------


def lines_from_diff(diff: str) -> dict[str, set[int]]:
    """The changed lines of each file in a ``git diff -U0`` (the new side; a
    deletion marks the lines around it)."""
    out: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            current = name[2:] if name.startswith("b/") else None
            if current is not None:
                out.setdefault(current, set())
        elif current is not None and (m := _HUNK.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or "1")
            if count:
                out[current].update(range(start, start + count))
            else:
                out[current].update({max(start, 1), start + 1})
    return out


def body_intervals(tree: ast.AST) -> list[tuple[int, int]]:
    """The line ranges of every function's body (not its decorators,
    signature or defaults, which run when the ``def`` runs)."""
    return [
        (node.body[0].lineno, node.end_lineno or node.body[-1].lineno)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body
    ]


def classify(path: Path, lines: set[int] | None) -> tuple[str, str]:
    """``("body" | "top", why)`` for a change of *lines* in *path*."""
    if lines is None:
        return "top", "no line information, read as a top-level change"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return "top", f"cannot be parsed ({type(exc).__name__})"
    if not lines:
        return "top", "no changed line (a rename or mode change)"
    spans = body_intervals(tree)
    outside = sorted(n for n in lines if not any(a <= n <= b for a, b in spans))
    if outside:
        return "top", f"line {outside[0]} is outside every function body"
    return "body", "every changed line is inside a function body"


# --- the closure ----------------------------------------------------------------


def reach(graph, seeds: set[str], top: bool) -> dict[str, str | None]:
    """Every node that reaches *seeds*, mapped to its next step towards one.

    For a body change, the *use* edges, with the wiring nodes crossed only
    from a seed or another wiring node; for a top-level change, every edge."""
    users = graph.reverse("uses")
    if top:
        for dst, srcs in graph.reverse("loads").items():
            users.setdefault(dst, set()).update(srcs)
    parent: dict[str, str | None] = {s: None for s in seeds}
    todo = deque(sorted(seeds))
    while todo:
        node = todo.popleft()
        for user in sorted(users.get(node, ())):
            if user in parent:
                continue
            if not top and wiring(user) and not (node in seeds or wiring(node)):
                continue
            parent[user] = node
            todo.append(user)
    return parent


def chain(parent: dict[str, str | None], node: str) -> list[str]:
    out = [node]
    while (step := parent.get(out[-1])) is not None:
        out.append(step)
    return out


def _short(node: str) -> str:
    for prefix in ("backend/app/", "backend/tests/"):
        if node.startswith(prefix):
            return node[len(prefix) :]
    return node


def explain(graph, parent: dict[str, str | None], test: str) -> str:
    steps = chain(parent, test)
    parts = [_short(steps[0])]
    for src, dst in itertools.pairwise(steps):
        why = graph.why.get((src, dst))
        parts.append(f"{_short(dst)}" + (f" ({why})" if why else ""))
    return " -> ".join(parts)


# --- the selection --------------------------------------------------------------


@dataclass
class Group:
    path: str
    kind: str
    why: str
    tests: dict[str, str] = field(default_factory=dict)  # test -> explanation


def folder_names(rel: str) -> set[str]:
    parts = rel.split("/")[:-1]
    names = {p for p in parts if p != "backend"}
    names |= {
        "/".join(parts[i:j])
        for i in range(len(parts))
        for j in range(i + 2, len(parts) + 1)
    }
    return names


def scanners(graph, rel: str) -> set[str]:
    """The tests that walk a folder and name a folder of *rel*."""
    names = folder_names(rel)
    return {
        path
        for path, facts in graph.facts.items()
        if path.startswith("backend/tests/test_")
        and facts.scans
        and any(s in names or s.rstrip("/") in names for s in facts.strings)
    }


def module_group(
    graph, rel: str, lines: set[int] | None, assume: str | None = None
) -> Group:
    """The tests a change of *lines* in the module *rel* can break (*assume*,
    ``body`` or ``top``, stands for the lines when there are none)."""
    if rel.startswith("backend/tests/"):
        kind, why = "test", "a test or test helper: itself and what imports it"
        top = False
    elif lines is None and assume in ("body", "top"):
        kind, why = assume, f"no line information, read as {assume} (--assume)"
        top = kind == "top"
    else:
        kind, why = classify(graph.root / rel, lines)
        top = kind == "top"
    group = Group(rel, kind, why)
    parent = reach(graph, {rel}, top)
    for node in sorted(parent):
        if node.startswith("backend/tests/test_") and "::" not in node:
            group.tests[node] = explain(graph, parent, node)
    for test in sorted(scanners(graph, rel) - set(group.tests)):
        group.tests[test] = f"{_short(test)} walks a folder it names"
    return group


def opaque_importers(graph) -> list[str]:
    """The app modules that import a module by a computed name."""
    return sorted(
        path
        for path, facts in graph.facts.items()
        if path.startswith(("backend/app/", "vscode-daemon/")) and facts.opaque
    )
