#!/usr/bin/env python3
"""
The backend's dependency rule between contexts, checked from the imports.

``docs/architecture/contexts.json`` divides ``backend/app/services`` into
contexts. Each context names the contexts it may import (``depends_on``,
which must not run in a circle) and the flat modules it holds (``modules``);
a module already moved into ``services/<context>/`` belongs to that context
by its folder (the folder's ``__init__`` too). While a context migrates
(ADR 0016), the flat ``services/<module>.py`` it left behind is a re-export
shim of ``services/<context>/<module>.py``: it stays listed in ``modules``
under that same context, so its importers are held to the rule as if they
imported the owner, and the check fails when it is listed anywhere else. The imports are read with ``ast`` (no module is imported or
run): ``from services import x``, ``from services.x import y``,
``import services.x``, the same through a context package
(``services.evidence.risk``) and relative imports inside one.

The check fails when:

- a flat module of ``services`` is in no context, or in more than one, or a
  context lists a module that does not exist, or a folder under ``services``
  is not a context, or a shim is listed in another context than its owner's;
- the contexts' ``depends_on`` names an unknown context or runs in a circle;
- a module imports a module of another context its own context may not
  import, and that edge is not in the ``allowlist``.

The allowlist holds the edges that broke the rule when it was adopted
(``"acceptance -> task_worker"``), named by the modules' stems whichever
folder holds them, so an edge keeps its name when its module moves into a
context folder. It may only shrink: an edge listed there
that no longer exists is reported, and ``--update`` drops it; ``--update``
never adds an edge. Exit status 0 when the code keeps to the rule, 1
otherwise. Run from anywhere: paths are resolved from the repository root.
Standard library only.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "architecture" / "contexts.json"
SERVICES = ROOT / "backend" / "app" / "services"
PACKAGE = "services"


def load(path: Path = DATA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def discover(services: Path, contexts: dict) -> tuple[dict[str, Path], list[str]]:
    """Every module of *services* by its key, and the folders that are no context.

    A flat module's key is its stem (``risk``); a module inside a context
    folder is ``<context>.<stem>`` (``evidence.risk``), its package
    ``<context>``.
    """
    found: dict[str, Path] = {}
    strays: list[str] = []
    for path in sorted(services.rglob("*.py")):
        rel = path.relative_to(services)
        if "__pycache__" in rel.parts:
            continue
        if len(rel.parts) == 1:
            if rel.stem != "__init__":
                found[rel.stem] = path
            continue
        if rel.parts[0] not in contexts:
            strays.append(rel.parts[0])
            continue
        parts = [*rel.parts[:-1], rel.stem]
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found[".".join(parts)] = path
    return found, sorted(set(strays))


def _resolve(dotted: str, modules: dict[str, Path]) -> str | None:
    """The module key a ``services.<dotted>`` name points at, or ``None``."""
    parts = dotted.split(".")
    while parts:
        key = ".".join(parts)
        if key in modules:
            return key
        parts = parts[:-1]
    return None


def imports_of(key: str, path: Path, modules: dict[str, Path]) -> set[str]:
    """The keys of the ``services`` modules *key* imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = key.split(".")[:-1] if path.name != "__init__.py" else key.split(".")
    targets: set[str] = set()

    def add(dotted: str) -> None:
        if (hit := _resolve(dotted, modules)) and hit != key:
            targets.add(hit)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE + "."):
                    add(alias.name[len(PACKAGE) + 1 :])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                if node.level - 1 > len(package):
                    continue
                prefix = ".".join([*base, *([node.module] if node.module else [])])
            elif node.module == PACKAGE:
                prefix = ""
            elif node.module and node.module.startswith(PACKAGE + "."):
                prefix = node.module[len(PACKAGE) + 1 :]
            else:
                continue
            for alias in node.names:
                name = f"{prefix}.{alias.name}" if prefix else alias.name
                if name in modules:
                    add(name)
                elif prefix:
                    add(prefix)
    return targets


def _name(key: str) -> str:
    """The module's name in an edge: its stem, whichever folder holds it.

    A migrated module and the shim it left at the flat path are one module, so
    ``evidence.verification -> sandbox`` is the allowlisted
    ``verification -> sandbox``: the allowlist survives a move unchanged."""
    return key.rsplit(".", 1)[-1]


def _is_shim(path: Path) -> bool:
    """A flat module that replaces itself with its owner in ``sys.modules``."""
    return "sys.modules[__name__]" in path.read_text(encoding="utf-8")


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if state.get(dep) == 1:
                return stack[stack.index(dep) :] + [dep]
            if state.get(dep) is None and dep in graph and (cycle := visit(dep)):
                return cycle
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if state.get(node) is None and (cycle := visit(node)):
            return cycle
    return None


def analyse(data: dict, services: Path = SERVICES) -> dict:
    """Everything the check reports, as data (the test reads it too)."""
    contexts: dict = data["contexts"]
    problems: list[str] = []
    for name, ctx in contexts.items():
        for dep in ctx.get("depends_on", []):
            if dep not in contexts:
                problems.append(f"context {name}: depends on unknown context {dep}")
    if cycle := find_cycle({n: c.get("depends_on", []) for n, c in contexts.items()}):
        problems.append(
            "the contexts' dependencies run in a circle: " + " -> ".join(cycle)
        )

    modules, strays = discover(services, contexts)
    for folder in strays:
        problems.append(f"services/{folder}/ is not a context in {DATA.name}")
    owner: dict[str, str] = {}
    for name, ctx in contexts.items():
        for module in ctx.get("modules", []):
            if module in owner:
                problems.append(
                    f"{module}: assigned to both {owner[module]} and {name}"
                )
            owner.setdefault(module, name)
            if module not in modules:
                problems.append(f"{module}: listed in {name}, but no such module")
    shims: dict[str, str] = {}
    for key, path in modules.items():
        if "." in key:
            owner[key] = key.split(".")[0]
        elif key in contexts and path.name == "__init__.py":
            owner[key] = key
        elif key not in owner:
            problems.append(f"{key}: in no context; add it to {DATA.name}")
    # Edges are named by stem, so a stem must name one module: a migrated
    # module and the shim at its flat path, never two modules of their own,
    # or one would inherit the other's allowlisted edges.
    stems: dict[str, list[str]] = {}
    for key in modules:
        if "." in key:
            stems.setdefault(_name(key), []).append(key)
    for stem, keys in sorted(stems.items()):
        if len(keys) > 1:
            problems.append(
                f"{stem}: names {' and '.join(sorted(keys))}; edges are named "
                "by the module's stem, so give one of them another name"
            )
    for key in modules:
        if "." in key or key in contexts:
            continue
        homes = [c for c in contexts if f"{c}.{key}" in modules]
        if homes and not _is_shim(modules[key]):
            problems.append(
                f"{key}: a module of its own beside {homes[0]}.{key}; edges are "
                "named by the module's stem, so leave a shim there "
                "(sys.modules[__name__] = the owner) or rename one of them"
            )
        if homes:
            shims[key] = homes[0]
            if owner.get(key) not in (None, homes[0]):
                problems.append(
                    f"{key}: a shim of {homes[0]}.{key}, but listed in {owner[key]}"
                )

    by_name = {_name(key): key for key in modules if "." in key}
    by_name.update({key: key for key in modules if "." not in key})
    allowed = {n: set(c.get("depends_on", [])) | {n} for n, c in contexts.items()}
    edges: list[tuple[str, str]] = []
    violations: list[str] = []
    for key, path in modules.items():
        for target in sorted(imports_of(key, path, modules)):
            edges.append((key, target))
            src, dst = owner.get(key), owner.get(target)
            if src and dst and dst not in allowed.get(src, set()):
                violations.append(f"{_name(key)} -> {_name(target)}")
    allowlist = set(data.get("allowlist", []))
    new = sorted(v for v in violations if v not in allowlist)
    stale = sorted(allowlist - set(violations))
    for edge in new:
        src, dst = (owner[by_name[part.strip()]] for part in edge.split("->"))
        problems.append(
            f"{edge}: {src} may not import {dst} "
            f"(allowed: {', '.join(sorted(allowed[src] - {src})) or 'nothing'})"
        )
    return {
        "modules": modules,
        "owner": owner,
        "shims": shims,
        "edges": edges,
        "violations": sorted(violations),
        "new": new,
        "stale": stale,
        "problems": problems,
    }


def main(argv: list[str], data_path: Path = DATA, services: Path = SERVICES) -> int:
    update = "--update" in argv
    data = load(data_path)
    result = analyse(data, services)
    if update:
        if result["problems"]:
            print("Not updating while the code breaks the rule:", file=sys.stderr)
        else:
            data["allowlist"] = [
                e for e in data.get("allowlist", []) if e not in set(result["stale"])
            ]
            data_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            print(f"Allowlist written: {len(data['allowlist'])} edge(s).")
    elif result["stale"]:
        print(
            "The allowlist can shrink (these edges are gone): "
            + ", ".join(result["stale"])
            + ". Run scripts/check_architecture.py --update and commit it."
        )
    for problem in result["problems"]:
        print(problem, file=sys.stderr)
    if not result["problems"]:
        print(
            f"{len(result['modules']) - len(result['shims'])} modules "
            f"(and {len(result['shims'])} shim(s)) in {len(data['contexts'])} contexts, "
            f"{len(result['edges'])} imports, {len(result['violations'])} allowlisted "
            "edge(s) against the rule."
        )
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
