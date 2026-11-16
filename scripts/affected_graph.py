"""
The import graph of the backend and its tests, module to module, for
``scripts/affected_tests.py --by module``.

Every Python file of ``backend/app``, ``vscode-daemon``, ``bench`` and
``backend/tests`` is a node, named by its repository path. The files are read
with ``ast`` (nothing is imported or run) and give two kinds of edge:

- *use*: the importer may call what it names. ``import a.b``, ``from a import
  b`` (the submodule ``a.b`` when there is one, else the module a package's
  ``__init__`` re-exports ``b`` from, else ``a``), relative imports, imports
  inside functions; in a test or a test helper also a dotted string that names
  a module (``mock.patch("services.loop.x.y")``, ``importlib`` by name), a
  ``*.py`` path string (a module loaded by path), the routers whose routes its
  URL strings match (a literal ``"/v1/sprints"`` matches every route it is a
  prefix of) and every router when it walks ``app.routes`` or
  ``app.openapi()``; in the app the argument of ``importlib.import_module``
  or ``__import__``. Two edges stand for calls no import shows: a module that
  ``hooks.emit``\\s uses every module that registers with ``hooks.on`` (the
  handler runs in the emitter's call), and a module whose top level changes
  another module's object (``hooks.on(...)``, ``other.X = ...``, a decorator
  of another module) is used by that module.
- *load*: the importer only runs the module's top level. A package's
  ``__init__`` for every module imported from inside it, the package itself in
  a ``from package import re_exported``, and the routers ``main.py`` mounts
  (``app.include_router``): a test that builds the app reaches a router
  through the URLs it calls, not through ``main``.

``conftest.py`` is a node every test uses: its module-level imports and its
autouse fixtures' imports, and a test that names one of its other fixtures as
a parameter (``test_client``) uses that fixture's imports too. Standard
library only.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP, TESTS = Path("backend/app"), Path("backend/tests")
DAEMON, BENCH = Path("vscode-daemon"), Path("bench")
CONFTEST = "backend/tests/conftest.py"
MAIN = "backend/app/main.py"
ROUTERS = "backend/app/routers/"

_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_DOTTED_HEAD = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.")
_PY_PATH = re.compile(r"[\w./-]*\w\.py")
_ROUTE = re.compile(
    r"@(?:router|app)\.(?:get|post|put|patch|delete|api_route|websocket)\(\s*"
    r"\"([^\"]*)\""
)
_PREFIX = re.compile(r"APIRouter\([^)]*prefix=\"([^\"]*)\"")
_DYNAMIC_CALLS = {"import_module", "__import__", "find_spec", "patch", "reload"}
_HOLE = "\x00"  # an f-string's formatted part in a URL


def rel(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


# --- the modules and their names -------------------------------------------


@dataclass
class Index:
    """The dotted names Python finds each file by, per place on ``sys.path``."""

    app: dict[str, str] = field(default_factory=dict)
    daemon: dict[str, str] = field(default_factory=dict)
    bench: dict[str, str] = field(default_factory=dict)
    tests: dict[str, str] = field(default_factory=dict)
    dotted: dict[str, str] = field(default_factory=dict)  # path -> its name

    @classmethod
    def build(cls, top: Path = ROOT) -> Index:
        index = cls()
        for root, table in ((top / APP, index.app), (top / BENCH, index.bench)):
            for path in sorted(root.rglob("*.py")):
                parts = path.relative_to(root).with_suffix("").parts
                if "__pycache__" in parts or any("-" in p for p in parts):
                    continue
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if parts:
                    table[".".join(parts)] = rel(path, top)
                    index.dotted.setdefault(rel(path, top), ".".join(parts))
        for root, table in ((top / DAEMON, index.daemon), (top / TESTS, index.tests)):
            for path in sorted(root.glob("*.py")):
                if path.stem != "conftest":
                    table[path.stem] = rel(path, top)
                index.dotted.setdefault(rel(path, top), path.stem)
        return index

    def tables(self, path: str) -> list[dict[str, str]]:
        """Where an import in *path* is looked up (``sys.path`` in the tests:
        their folder, the daemon, ``backend/app``, ``bench``)."""
        if path.startswith("backend/tests/"):
            return [self.tests, self.daemon, self.app, self.bench]
        if path.startswith("vscode-daemon/"):
            return [self.daemon]
        if path.startswith("bench/"):
            return [self.bench, self.app]
        return [self.app]

    def resolve(self, dotted: str, tables: list[dict[str, str]]) -> list[str]:
        """The file of the longest module prefix of *dotted*, in each table."""
        dotted = dotted.removeprefix("app.")
        found = []
        for table in tables:
            parts = dotted.split(".")
            while parts:
                if (hit := table.get(".".join(parts))) is not None:
                    found.append(hit)
                    break
                parts = parts[:-1]
        return found


def _packages(dotted: str, table: dict[str, str]) -> list[str]:
    """The ``__init__`` files Python runs before the module *dotted*."""
    parts = dotted.split(".")
    return [p for i in range(1, len(parts)) if (p := table.get(".".join(parts[:i])))]


# --- what one file says ------------------------------------------------------


@dataclass
class Facts:
    imports: list[tuple[int, str, list[str]]] = field(default_factory=list)
    strings: list[str] = field(default_factory=list)
    urls: set[str] = field(default_factory=set)
    dynamic: list[str] = field(default_factory=list)  # import_module("x"), patch("x")
    emits: bool = False
    registers: bool = False
    touched: set[str] = field(default_factory=set)  # names a top level changes
    fixtures: dict[str, tuple[bool, list[str], Facts]] = field(default_factory=dict)
    params: set[str] = field(default_factory=set)
    enumerates_routes: bool = False
    scans: bool = False  # walks a folder (rglob, glob, os.walk, iterdir)
    opaque: bool = False  # import_module(a computed name)
    prefixes: set[str] = field(default_factory=set)  # f"services.loop.{x}"
    tree: ast.Module | None = None


def _import_nodes(nodes) -> list[tuple[int, str, list[str]]]:
    out = []
    for node in nodes:
        if isinstance(node, ast.Import):
            out.extend((0, alias.name, []) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append((node.level, node.module or "", [a.name for a in node.names]))
    return out


def _url(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(
            v.value if isinstance(v, ast.Constant) else _HOLE for v in node.values
        )
    else:
        return None
    return text.split("?")[0] if text.startswith("/") and len(text) > 1 else None


def _prefix(node: ast.AST, facts: Facts) -> bool:
    """Record the dotted head of an f-string (``f"services.loop.{x}"``)."""
    if not (isinstance(node, ast.JoinedStr) and node.values):
        return False
    head = node.values[0]
    if isinstance(head, ast.Constant) and _DOTTED_HEAD.fullmatch(str(head.value)):
        facts.prefixes.add(str(head.value).removeprefix("app."))
        return True
    return False


def _root(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _top_level_touches(body: list[ast.stmt]) -> set[str]:
    """The names a module's top level calls a method of, assigns into or
    decorates with (``hooks.on(...)``, ``other.X = 1``, ``@other.register``)."""
    touched: set[str] = set()
    for stmt in body:
        if isinstance(stmt, (ast.If, ast.Try)):
            inner = [*stmt.body, *stmt.orelse, *getattr(stmt, "finalbody", [])]
            touched |= _top_level_touches(inner)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            if isinstance(stmt.value.func, ast.Attribute):
                touched.add(_root(stmt.value.func) or "")
        elif isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            touched |= {
                _root(t) or ""
                for t in targets
                if isinstance(t, (ast.Attribute, ast.Subscript))
            }
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            roots = {
                _root(d) or ""
                for d in stmt.decorator_list
                if isinstance(d, (ast.Attribute, ast.Call))
            }
            # A route's decorators (``@limiter.limit``) act on that route only,
            # which its callers reach through the router.
            if "router" not in roots and "app" not in roots:
                touched |= roots
    return touched - {""}


def _is_fixture(node: ast.AST) -> tuple[bool, bool]:
    """Whether a function is a pytest fixture, and an autouse one."""
    for deco in getattr(node, "decorator_list", []):
        call = deco if isinstance(deco, ast.Call) else None
        target = call.func if call else deco
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            auto = any(
                k.arg == "autouse" and getattr(k.value, "value", False)
                for k in (call.keywords if call else [])
            )
            return True, auto
    return False, False


def facts_of(tree: ast.AST, path: str = "") -> Facts:
    facts = Facts()
    # An f-string's literal parts are not URLs of their own.
    parts = {
        id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            facts.imports.extend(_import_nodes([node]))
        elif id(node) not in parts and (url := _url(node)) is not None:
            facts.urls.add(url)
        if isinstance(node, ast.JoinedStr) and path.startswith("backend/tests/"):
            _prefix(node, facts)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            facts.strings.append(node.value)
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name in _DYNAMIC_CALLS and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    facts.dynamic.append(arg.value)
                elif name in {"import_module", "__import__"}:
                    facts.opaque = not _prefix(arg, facts)
            facts.scans |= name in {"rglob", "glob", "walk", "iterdir"}
            facts.enumerates_routes |= name == "openapi"
            if isinstance(func, ast.Attribute) and _root(func) == "hooks":
                facts.emits |= func.attr == "emit"
                facts.registers |= func.attr == "on"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            facts.params |= {
                a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
            }
        elif isinstance(node, ast.Attribute) and node.attr == "routes":
            facts.enumerates_routes = True
    if isinstance(tree, ast.Module):
        facts.touched = _top_level_touches(tree.body)
        if path == CONFTEST:
            for node in tree.body:
                fixture, auto = _is_fixture(node)
                if fixture:
                    params = [a.arg for a in node.args.args]  # type: ignore[attr-defined]
                    facts.fixtures[node.name] = (auto, params, facts_of(node))  # type: ignore[attr-defined]
    return facts


def parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


# --- the routers --------------------------------------------------------------


def route_table(root: Path = ROOT) -> dict[str, list[list[str]]]:
    """Each router's routes, as path segments (``{x}`` a parameter)."""
    table: dict[str, list[list[str]]] = {}
    for router in sorted((root / APP / "routers").glob("*.py")):
        text = router.read_text(encoding="utf-8")
        prefix = m.group(1) if (m := _PREFIX.search(text)) else ""
        routes = [_segments(prefix + p) for p in _ROUTE.findall(text)]
        if routes:
            table[rel(router, root)] = routes
    return table


def _segments(url: str) -> list[str]:
    return [s for s in url.split("/") if s]


def _segment_matches(test: str, route: str) -> bool:
    if route.startswith("{") or test == route:
        return True
    if _HOLE not in test:
        return False
    pattern = ".*".join(re.escape(p) for p in test.split(_HOLE))
    return re.fullmatch(pattern, route) is not None


def routers_for(url: str, table: dict[str, list[list[str]]]) -> set[str]:
    """The routers a URL string can reach: a route it matches, or one it is the
    leading part of (``"/v1/sprints"`` + a path built later)."""
    test = _segments(url)
    if not test:
        return set()
    return {
        router
        for router, routes in table.items()
        for route in routes
        if len(test) <= len(route)
        and all(_segment_matches(t, r) for t, r in zip(test, route, strict=False))
    }


# --- the graph --------------------------------------------------------------


@dataclass
class Graph:
    index: Index
    root: Path = ROOT
    uses: dict[str, set[str]] = field(default_factory=dict)
    loads: dict[str, set[str]] = field(default_factory=dict)
    why: dict[tuple[str, str], str] = field(default_factory=dict)
    facts: dict[str, Facts] = field(default_factory=dict)

    def use(self, src: str, dst: str, reason: str = "") -> None:
        if src != dst:
            self.uses.setdefault(src, set()).add(dst)
            if reason:
                self.why.setdefault((src, dst), reason)

    def load(self, src: str, dst: str) -> None:
        if src != dst:
            self.loads.setdefault(src, set()).add(dst)

    def reverse(self, kind: str = "uses") -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for src, dsts in getattr(self, kind).items():
            for dst in dsts:
                out.setdefault(dst, set()).add(src)
        return out


def _files(root: Path) -> list[Path]:
    found = [*(root / APP).rglob("*.py"), *(root / DAEMON).glob("*.py")]
    found += [*(root / TESTS).glob("*.py"), *(root / BENCH).rglob("*.py")]
    return sorted(p for p in found if "__pycache__" not in p.parts)


def _reexports(index: Index, graph: Graph) -> dict[str, dict[str, str]]:
    """For each package ``__init__`` of the app, the names it binds from a
    module of its own (``from routers.sprint import router as sprint_router``)."""
    out: dict[str, dict[str, str]] = {}
    for dotted, path in index.app.items():
        facts = graph.facts.get(path)
        if not path.endswith("__init__.py") or facts is None or facts.tree is None:
            continue
        names: dict[str, str] = {}
        for node in facts.tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    source = node.module.removeprefix("app.")
                    target = index.app.get(f"{source}.{alias.name}") or index.app.get(
                        source
                    )
                    if target:
                        names[alias.asname or alias.name] = target
        out[dotted] = names
    return out


def _add_imports(graph: Graph, path: str, facts: Facts, reexports: dict) -> None:
    index = graph.index
    tables = index.tables(path)
    own = index.dotted.get(path, "")
    package = own.split(".") if path.endswith("__init__.py") else own.split(".")[:-1]
    for level, module, names in facts.imports:
        if level:
            if level - 1 > len(package) or not path.startswith(
                ("backend/app/", "bench/")
            ):
                continue
            base = ".".join(
                package[: len(package) - (level - 1)] + ([module] if module else [])
            )
        else:
            base = module
        for table in tables:
            _add_one(
                graph, path, table, base, names, reexports if table is index.app else {}
            )


def _add_one(
    graph: Graph, path: str, table: dict, base: str, names: list[str], reexports: dict
) -> None:
    # ``app.main`` is ``main`` with ``backend`` on the path (the tests do both).
    if base.startswith("app.") and base[4:].split(".")[0] in table:
        base = base[4:]
    for pkg in _packages(base + ".x", table) if base else []:
        graph.load(path, pkg)
    if not names:
        hits = graph.index.resolve(base, [table])
        for hit in hits:
            graph.use(path, hit)
            for pkg in _packages(graph.index.dotted.get(hit, ""), table):
                graph.load(path, pkg)
        return
    for name in names:
        dotted = f"{base}.{name}" if base else name
        if dotted in table:
            graph.use(path, table[dotted])
        elif name in reexports.get(base, {}):
            graph.use(path, reexports[base][name], f"{base} re-exports {name}")
            graph.load(path, table[base])
        elif base in table:
            graph.use(path, table[base])


def build(root: Path = ROOT) -> Graph:
    """The graph of the repository at *root*."""
    index = Index.build(root)
    graph = Graph(index, root=root)
    for path in _files(root):
        tree = parse(path)
        facts = facts_of(tree, rel(path, root)) if tree is not None else Facts()
        facts.tree = tree
        if rel(path, root) == CONFTEST and tree is not None:
            # Its fixtures' imports count only for the tests that use them.
            facts.imports = _import_nodes(tree.body)
            facts.strings, facts.dynamic, facts.urls = [], [], set()
        graph.facts[rel(path, root)] = facts
    reexports = _reexports(index, graph)
    routes = route_table(root)
    by_name: dict[str, list[str]] = {}
    for node in graph.facts:
        by_name.setdefault(node.rsplit("/", 1)[-1], []).append(node)
    registrars = sorted(
        p
        for p, f in graph.facts.items()
        if f.registers and p.startswith("backend/app/")
    )
    for path, facts in graph.facts.items():
        _add_imports(graph, path, facts, reexports)
        in_tests = path.startswith("backend/tests/")
        tables = index.tables(path)
        texts = (facts.strings if in_tests else []) + facts.dynamic
        for text in texts:
            if _DOTTED.fullmatch(text):
                for hit in index.resolve(text, tables):
                    graph.use(path, hit, f'names "{text}"')
        for head in sorted(facts.prefixes):
            for dotted, hit in index.app.items():
                if (dotted + ".").startswith(head) or dotted.startswith(head):
                    graph.use(path, hit, f'names f"{head}..."')
        if in_tests:
            _add_test_edges(graph, path, facts, routes, by_name)
        if facts.emits:
            for target in registrars:
                graph.use(
                    path, target, "hooks.emit runs the handlers hooks.on registered"
                )
        _add_touches(graph, path, facts)
    # main.py mounts the routers: building the app runs their top level only.
    for router in [d for d in graph.uses.get(MAIN, set()) if d.startswith(ROUTERS)]:
        graph.uses[MAIN].discard(router)
        graph.load(MAIN, router)
    _add_conftest(graph)
    return graph


def _add_test_edges(
    graph: Graph, path: str, facts: Facts, routes: dict, by_name: dict
) -> None:
    for text in facts.strings:
        if _PY_PATH.fullmatch(text) and text.rsplit("/", 1)[-1] not in (
            "__init__.py",
            "conftest.py",
        ):
            for node in by_name.get(text.rsplit("/", 1)[-1], []):
                if "/" not in text or node.endswith(text):
                    graph.use(path, node, f'loads "{text}" by path')
    for url in sorted(facts.urls):
        for router in sorted(routers_for(url, routes)):
            graph.use(path, router, f"calls {url.replace(_HOLE, '{}')}")
    if facts.enumerates_routes:
        for router in routes:
            graph.use(path, router, "walks every route of the app")


def _add_touches(graph: Graph, path: str, facts: Facts) -> None:
    """A top level that changes another module's object is used by that module."""
    if not facts.touched or path.startswith("backend/tests/") or path == MAIN:
        # A test's top level changes its own run only; main.py's wiring
        # reaches the tests that build the app, and a change to it runs all.
        return
    tables = graph.index.tables(path)
    bound: dict[str, str] = {}
    for level, module, names in facts.imports:
        if level or not module:
            continue
        if not names:
            bound[module.split(".")[0]] = module
        for name in names:
            bound[name] = f"{module}.{name}"
    for name in facts.touched & set(bound):
        for hit in graph.index.resolve(bound[name], tables):
            graph.use(
                hit, path, f"{path.rsplit('/', 1)[-1]} changes {name} when loaded"
            )


def _add_conftest(graph: Graph) -> None:
    """Every test uses ``conftest.py``'s module level and autouse fixtures; a
    test that names another fixture uses its imports through ``conftest``."""
    facts = graph.facts.get(CONFTEST)
    if facts is None:
        return
    fixtures = facts.fixtures
    shared = graph.uses.setdefault(CONFTEST, set())
    for name, (auto, _params, inner) in fixtures.items():
        node = f"{CONFTEST}::{name}"
        own = Graph(graph.index, facts={node: inner})
        _add_imports(own, node, inner, {})
        texts = inner.strings + inner.dynamic
        for text in texts:
            if _DOTTED.fullmatch(text):
                for hit in graph.index.resolve(text, graph.index.tables(CONFTEST)):
                    own.use(node, hit)
        targets = own.uses.get(node, set())
        for target in own.loads.get(node, set()):
            graph.load(CONFTEST if auto else node, target)
        if auto:
            for target in targets:
                graph.use(CONFTEST, target, f"autouse fixture {name}")
        else:
            graph.uses[node] = set(targets)
            for target in targets:
                graph.why[(node, target)] = f"fixture {name}"
    for name, (_auto, params, _inner) in fixtures.items():
        for param in params:
            if param in fixtures and not fixtures[param][0]:
                graph.use(f"{CONFTEST}::{name}", f"{CONFTEST}::{param}")
    for path, test_facts in graph.facts.items():
        if path.startswith("backend/tests/test_"):
            graph.use(path, CONFTEST)
            for param in test_facts.params & set(fixtures):
                if not fixtures[param][0]:
                    graph.use(path, f"{CONFTEST}::{param}", f"fixture {param}")
    shared.discard(CONFTEST)
